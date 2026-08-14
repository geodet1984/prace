"""Obchodní strategie.

Strategie odpovídá na jedinou otázku: *chci na tomto baru držet pozici?*
Neřeší velikost pozice ani stop-loss — to je práce risk manageru
(``trading.risk``). Toto oddělení je záměrné: signál a řízení rizika se
ladí nezávisle a míchat je dohromady je nejrychlejší cesta ke strategii,
které nikdo nerozumí.

Všechny strategie jsou long-only.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from . import indicators as ind
from .models import HOLD, Position, Signal, SignalType


@dataclass(frozen=True)
class BarContext:
    """Vše, co strategie na daném baru smí vidět.

    Klíčové omezení: ``row`` je aktuální bar a všechny indikátory jsou
    počítané výhradně z minulosti. Strategie nemá přístup k dalším barům,
    takže lookahead bias není možný ani omylem.
    """

    symbol: str
    timestamp: datetime
    row: Mapping[str, Any]
    """Hodnoty aktuálního baru — sloupce OHLCV plus indikátory ze ``prepare``."""

    position: Position | None

    @property
    def close(self) -> float:
        return float(self.row["close"])

    @property
    def in_position(self) -> bool:
        return self.position is not None


class Strategy(ABC):
    """Bázová třída pro strategie."""

    name: str = "base"

    @abstractmethod
    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """Doplní do dat sloupce s indikátory. Volá se jednou před během."""

    @abstractmethod
    def on_bar(self, ctx: BarContext) -> Signal:
        """Vrátí signál pro daný bar."""

    @property
    def warmup(self) -> int:
        """Kolik barů na začátku přeskočit, než mají indikátory smysl."""
        return 0

    def describe(self) -> str:
        return self.name

    def __repr__(self) -> str:  # pragma: no cover - jen pro logy
        return f"<{type(self).__name__} {self.describe()}>"


class SmaCrossover(Strategy):
    """Trend following: nákup při protnutí pomalého průměru rychlým.

    Klasika, která vydělává na dlouhých trendech a systematicky ztrácí
    v postranním trhu (whipsaw). Nízký win rate, ale vysoký poměr
    průměrný zisk / průměrná ztráta.
    """

    name = "sma_crossover"

    def __init__(self, fast: int = 20, slow: int = 50, enter_on_cross: bool = True) -> None:
        if fast >= slow:
            raise ValueError(f"fast ({fast}) musí být menší než slow ({slow})")
        self.fast = fast
        self.slow = slow
        # True  = vstup jen na baru, kde k překřížení skutečně došlo
        # False = vstup kdykoli, když je rychlý průměr nad pomalým
        # Rozdíl je zásadní: při False se pozice po vyražení stop-lossem
        # hned druhý den obnoví a stop tím ztrácí smysl.
        self.enter_on_cross = enter_on_cross

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        fast = ind.sma(df["close"], self.fast)
        slow = ind.sma(df["close"], self.slow)
        df["sma_fast"] = fast
        df["sma_slow"] = slow
        df["sma_cross_up"] = (fast > slow) & (fast.shift(1) <= slow.shift(1))
        return df

    @property
    def warmup(self) -> int:
        return self.slow + 1

    def on_bar(self, ctx: BarContext) -> Signal:
        fast, slow = ctx.row["sma_fast"], ctx.row["sma_slow"]
        if pd.isna(fast) or pd.isna(slow):
            return HOLD

        if not ctx.in_position:
            entered = bool(ctx.row["sma_cross_up"]) if self.enter_on_cross else fast > slow
            if entered:
                return Signal(SignalType.ENTER_LONG, 1.0, f"SMA{self.fast} protla SMA{self.slow}")
            return HOLD

        if fast < slow:
            return Signal(SignalType.EXIT_LONG, 1.0, f"SMA{self.fast} pod SMA{self.slow}")
        return HOLD

    def describe(self) -> str:
        return f"{self.name}(fast={self.fast}, slow={self.slow}, cross={self.enter_on_cross})"


class RsiMeanReversion(Strategy):
    """Mean reversion: nákup přeprodaného titulu, prodej po návratu.

    Vysoký win rate, ale malé zisky proti občasné velké ztrátě, když se
    z propadu stane trend. Bez stop-lossu je tahle strategie nebezpečná —
    proto risk manager není volitelný doplněk.
    """

    name = "rsi_reversion"

    def __init__(
        self,
        period: int = 14,
        oversold: float = 30.0,
        exit_level: float = 55.0,
        trend_filter: int | None = 200,
    ) -> None:
        self.period = period
        self.oversold = oversold
        self.exit_level = exit_level
        # Filtr dlouhodobého trendu: nakupovat propady jen v rostoucím trhu.
        # Bez něj strategie ochotně chytá padající nůž.
        self.trend_filter = trend_filter

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["rsi"] = ind.rsi(df["close"], self.period)
        if self.trend_filter:
            df["trend_sma"] = ind.sma(df["close"], self.trend_filter)
        return df

    @property
    def warmup(self) -> int:
        return max(self.period, self.trend_filter or 0) + 1

    def on_bar(self, ctx: BarContext) -> Signal:
        rsi_value = ctx.row["rsi"]
        if pd.isna(rsi_value):
            return HOLD

        if not ctx.in_position:
            if rsi_value > self.oversold:
                return HOLD
            if self.trend_filter:
                trend = ctx.row.get("trend_sma")
                if pd.isna(trend) or ctx.close < trend:
                    return HOLD
            return Signal(SignalType.ENTER_LONG, 1.0, f"RSI {rsi_value:.1f} pod {self.oversold}")

        if rsi_value >= self.exit_level:
            return Signal(SignalType.EXIT_LONG, 1.0, f"RSI {rsi_value:.1f} nad {self.exit_level}")
        return HOLD

    def describe(self) -> str:
        return (
            f"{self.name}(period={self.period}, oversold={self.oversold}, "
            f"exit={self.exit_level}, trend={self.trend_filter})"
        )


class DonchianBreakout(Strategy):
    """Momentum: nákup na proražení maxima, výstup na proražení minima.

    Jádro původního Turtle systému. Funguje na trzích, které trendují,
    a je odolnější vůči přeoptimalizování než většina indikátorových
    kombinací, protože má jen dva parametry.
    """

    name = "donchian_breakout"

    def __init__(self, entry_window: int = 20, exit_window: int = 10) -> None:
        self.entry_window = entry_window
        self.exit_window = exit_window

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        # shift(1): srovnáváme s kanálem *před* aktuálním barem, jinak by
        # dnešní maximum bylo součástí hranice, kterou má dnešek prorazit.
        df["channel_high"] = ind.rolling_high(df["high"], self.entry_window).shift(1)
        df["channel_low"] = ind.rolling_low(df["low"], self.exit_window).shift(1)
        return df

    @property
    def warmup(self) -> int:
        return max(self.entry_window, self.exit_window) + 2

    def on_bar(self, ctx: BarContext) -> Signal:
        high, low = ctx.row["channel_high"], ctx.row["channel_low"]

        if not ctx.in_position:
            if pd.isna(high):
                return HOLD
            if ctx.close > high:
                return Signal(
                    SignalType.ENTER_LONG, 1.0, f"proražení {self.entry_window}d maxima {high:.2f}"
                )
            return HOLD

        if pd.isna(low):
            return HOLD
        if ctx.close < low:
            return Signal(
                SignalType.EXIT_LONG, 1.0, f"propad pod {self.exit_window}d minimum {low:.2f}"
            )
        return HOLD

    def describe(self) -> str:
        return f"{self.name}(entry={self.entry_window}, exit={self.exit_window})"


REGISTRY: dict[str, type[Strategy]] = {
    SmaCrossover.name: SmaCrossover,
    RsiMeanReversion.name: RsiMeanReversion,
    DonchianBreakout.name: DonchianBreakout,
}


def create(name: str, **params) -> Strategy:
    """Vytvoří strategii podle jména z registru."""
    try:
        cls = REGISTRY[name]
    except KeyError:
        raise ValueError(
            f"neznámá strategie {name!r}; dostupné: {', '.join(sorted(REGISTRY))}"
        ) from None
    return cls(**params)
