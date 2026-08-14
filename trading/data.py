"""Načítání tržních dat.

Zdroje:
  * ``yfinance``  — reálná historická i denní data (vyžaduje síť)
  * CSV cache     — offline opakovatelné běhy
  * generátor     — syntetická geometrická náhodná procházka pro testy a demo

Kanonický tvar dat je DataFrame indexovaný časem se sloupci
``open, high, low, close, volume``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = ["open", "high", "low", "close", "volume"]

DEFAULT_CACHE_DIR = Path("data/cache")


class DataError(RuntimeError):
    """Data se nepodařilo načíst nebo nemají očekávaný tvar."""


def _normalize(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Sjednotí názvy sloupců, seřadí podle času a zahodí neúplné bary."""
    if isinstance(df.columns, pd.MultiIndex):
        # yfinance vrací MultiIndex, když stahuje víc tickerů najednou.
        has_symbol = symbol in df.columns.get_level_values(-1)
        df = df.xs(symbol, axis=1, level=-1) if has_symbol else df.droplevel(-1, axis=1)

    df = df.rename(columns={c: str(c).lower().replace(" ", "_") for c in df.columns})

    if "adj_close" in df.columns and "close" in df.columns:
        # Používáme upravené ceny — jinak by split vypadal jako propad o 50 %.
        ratio = df["adj_close"] / df["close"].replace(0.0, np.nan)
        for col in ("open", "high", "low"):
            if col in df.columns:
                df[col] = df[col] * ratio
        df["close"] = df["adj_close"]

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise DataError(f"{symbol}: chybí sloupce {missing}, dostupné: {list(df.columns)}")

    df = df[REQUIRED_COLUMNS].copy()
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df.dropna(subset=["open", "high", "low", "close"])

    if df.empty:
        raise DataError(f"{symbol}: po vyčištění nezbyla žádná data")
    return df


def load_csv(path: str | Path, symbol: str = "?") -> pd.DataFrame:
    """Načte OHLCV z CSV. První sloupec musí být datum."""
    path = Path(path)
    if not path.exists():
        raise DataError(f"soubor neexistuje: {path}")
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    return _normalize(df, symbol)


def save_csv(df: pd.DataFrame, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path)


def _cache_path(cache_dir: Path, symbol: str, interval: str) -> Path:
    safe = symbol.replace("/", "_").replace("^", "idx_")
    return cache_dir / f"{safe}_{interval}.csv"


def fetch(
    symbol: str,
    start: str | None = None,
    end: str | None = None,
    interval: str = "1d",
    cache_dir: str | Path | None = DEFAULT_CACHE_DIR,
    use_cache: bool = True,
) -> pd.DataFrame:
    """Stáhne data z Yahoo Finance, s volitelnou CSV cache.

    Když je síť nedostupná a cache existuje, vrátí se cache.
    """
    cache_dir = Path(cache_dir) if cache_dir else None
    cached = _cache_path(cache_dir, symbol, interval) if cache_dir else None

    if use_cache and cached and cached.exists():
        try:
            df = load_csv(cached, symbol)
            if _covers(df, start, end):
                logger.debug("%s: použita cache %s", symbol, cached)
                return _slice(df, start, end)
        except DataError:
            logger.warning("%s: poškozená cache %s, stahuji znovu", symbol, cached)

    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover - závisí na prostředí
        raise DataError("yfinance není nainstalován: pip install yfinance") from exc

    logger.info("stahuji %s (%s) %s..%s", symbol, interval, start or "začátek", end or "dnes")
    try:
        raw = yf.download(
            symbol,
            start=start,
            end=end,
            interval=interval,
            auto_adjust=False,
            progress=False,
            threads=False,
        )
    except Exception as exc:
        if cached and cached.exists():
            logger.warning("%s: stažení selhalo (%s), používám cache", symbol, exc)
            return _slice(load_csv(cached, symbol), start, end)
        raise DataError(f"{symbol}: stažení selhalo: {exc}") from exc

    if raw is None or raw.empty:
        if cached and cached.exists():
            logger.warning("%s: prázdná odpověď, používám cache", symbol)
            return _slice(load_csv(cached, symbol), start, end)
        raise DataError(f"{symbol}: burza nevrátila žádná data (neplatný ticker?)")

    df = _normalize(raw, symbol)
    if cached:
        save_csv(df, cached)
    return df


def _covers(df: pd.DataFrame, start: str | None, end: str | None) -> bool:
    """Pokrývá cache požadovaný interval?"""
    if start and df.index[0] > pd.Timestamp(start):
        return False
    return not (end and df.index[-1] < pd.Timestamp(end) - pd.Timedelta(days=5))


def _slice(df: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    if start:
        df = df[df.index >= pd.Timestamp(start)]
    if end:
        df = df[df.index <= pd.Timestamp(end)]
    return df


def fetch_many(
    symbols: list[str],
    start: str | None = None,
    end: str | None = None,
    interval: str = "1d",
    cache_dir: str | Path | None = DEFAULT_CACHE_DIR,
    use_cache: bool = True,
) -> dict[str, pd.DataFrame]:
    """Stáhne víc titulů. Selhání jednoho neshodí celou dávku."""
    out: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        try:
            out[symbol] = fetch(symbol, start, end, interval, cache_dir, use_cache)
        except DataError as exc:
            logger.error("%s: přeskakuji — %s", symbol, exc)
    if not out:
        raise DataError("nepodařilo se načíst žádný z požadovaných titulů")
    return out


def synthetic(
    symbol: str = "TEST",
    days: int = 750,
    start: str = "2021-01-01",
    initial_price: float = 100.0,
    annual_drift: float = 0.08,
    annual_vol: float = 0.25,
    seed: int = 4,
) -> pd.DataFrame:
    """Vygeneruje reprodukovatelnou geometrickou náhodnou procházku.

    Slouží k testům a k demu bez sítě. Backtest na syntetických datech
    nevypovídá vůbec nic o kvalitě strategie — jen o tom, že kód běží.
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start=start, periods=days)

    dt = 1.0 / 252.0
    shocks = rng.normal(
        loc=(annual_drift - 0.5 * annual_vol**2) * dt,
        scale=annual_vol * np.sqrt(dt),
        size=days,
    )
    close = initial_price * np.exp(np.cumsum(shocks))

    intraday = np.abs(rng.normal(0.0, annual_vol * np.sqrt(dt) * 0.7, size=days))
    open_ = close * (1.0 + rng.normal(0.0, annual_vol * np.sqrt(dt) * 0.3, size=days))
    high = np.maximum(open_, close) * (1.0 + intraday)
    low = np.minimum(open_, close) * (1.0 - intraday)
    volume = rng.integers(500_000, 5_000_000, size=days).astype(float)

    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )
    df.index.name = "date"
    return df
