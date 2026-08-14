"""Technické indikátory nad pandas Series.

Všechny funkce vrací Series stejné délky jako vstup, s NaN na začátku tam,
kde ještě není dost dat. Nikdy nedoplňují hodnoty dopředu — dopočítání
chybějícího začátku by do backtestu propašovalo informaci z budoucnosti.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    """Jednoduchý klouzavý průměr."""
    return series.rolling(window=window, min_periods=window).mean()


def ema(series: pd.Series, window: int) -> pd.Series:
    """Exponenciální klouzavý průměr."""
    return series.ewm(span=window, adjust=False, min_periods=window).mean()


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    """Relative Strength Index (Wilderovo vyhlazení), rozsah 0..100."""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    avg_gain = gain.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()

    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # avg_loss == 0 znamená samé růsty -> RSI 100 (ne NaN z dělení nulou).
    out = out.where(avg_loss != 0.0, 100.0)
    return out.where(avg_gain.notna())


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """True Range — rozpětí baru včetně mezery proti předchozímu close."""
    prev_close = close.shift(1)
    ranges = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    )
    return ranges.max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    """Average True Range — měřítko volatility, základ pro stop-loss."""
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()


def bollinger(
    series: pd.Series, window: int = 20, num_std: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Bollingerova pásma: (dolní, střední, horní)."""
    middle = sma(series, window)
    std = series.rolling(window=window, min_periods=window).std(ddof=0)
    return middle - num_std * std, middle, middle + num_std * std


def rolling_high(series: pd.Series, window: int) -> pd.Series:
    """Nejvyšší hodnota za posledních ``window`` barů včetně aktuálního."""
    return series.rolling(window=window, min_periods=window).max()


def rolling_low(series: pd.Series, window: int) -> pd.Series:
    """Nejnižší hodnota za posledních ``window`` barů včetně aktuálního."""
    return series.rolling(window=window, min_periods=window).min()


def returns(series: pd.Series) -> pd.Series:
    """Prosté procentní výnosy mezi bary."""
    return series.pct_change()


def annualized_volatility(series: pd.Series, periods_per_year: int = 252) -> float:
    """Anualizovaná volatilita denních výnosů."""
    r = returns(series).dropna()
    if len(r) < 2:
        return 0.0
    return float(r.std(ddof=1) * np.sqrt(periods_per_year))
