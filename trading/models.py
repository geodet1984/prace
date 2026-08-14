"""Základní datové typy sdílené backtestem i živým během."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Side(str, Enum):
    """Směr obchodu."""

    BUY = "BUY"
    SELL = "SELL"


class SignalType(str, Enum):
    """Co strategie doporučuje udělat na daném baru."""

    ENTER_LONG = "ENTER_LONG"
    EXIT_LONG = "EXIT_LONG"
    HOLD = "HOLD"


@dataclass(frozen=True)
class Signal:
    """Doporučení strategie.

    ``strength`` v rozsahu 0..1 umožňuje risk manageru škálovat velikost
    pozice u méně jistých signálů. ``reason`` je čistě pro čitelnost logu.
    """

    type: SignalType
    strength: float = 1.0
    reason: str = ""

    @property
    def is_actionable(self) -> bool:
        return self.type is not SignalType.HOLD


HOLD = Signal(SignalType.HOLD, 0.0, "bez signálu")


@dataclass(frozen=True)
class Order:
    """Market příkaz čekající na provedení na otevření dalšího baru.

    Záměrně neexistuje varianta "provedeno za aktuální close" — to je
    nejčastější zdroj lookahead biasu v amatérských backtestech.
    """

    symbol: str
    side: Side
    quantity: float
    reason: str = ""
    stop_loss: float | None = None
    take_profit: float | None = None
    risked_amount: float = 0.0


@dataclass
class Position:
    """Otevřená long pozice v jednom titulu."""

    symbol: str
    quantity: float
    entry_price: float
    entry_time: datetime
    stop_loss: float | None = None
    take_profit: float | None = None
    initial_risk: float = 0.0
    """Částka v sázce při otevření. Trailing stop ji později posune, ale
    Kellyho odhad potřebuje původní hodnotu, aby R-násobky seděly."""

    entry_fees: float = 0.0
    """Poplatky zaplacené při vstupu.

    Drží se na pozici, ne dohledáváním v seznamu plnění: ten se po restartu
    neobnovuje, takže by se poplatek vstupní nohy tiše ztratil a uzavřený
    obchod by vykázal nižší náklady, než jaké skutečně byly."""

    def market_value(self, price: float) -> float:
        return self.quantity * price

    def unrealized_pnl(self, price: float) -> float:
        return (price - self.entry_price) * self.quantity

    def unrealized_pnl_pct(self, price: float) -> float:
        if self.entry_price == 0:
            return 0.0
        return (price / self.entry_price - 1.0) * 100.0


@dataclass
class Trade:
    """Uzavřený obchod — jeden kompletní cyklus nákup → prodej."""

    symbol: str
    quantity: float
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    fees: float
    exit_reason: str = ""

    @property
    def gross_pnl(self) -> float:
        return (self.exit_price - self.entry_price) * self.quantity

    @property
    def net_pnl(self) -> float:
        """Zisk po odečtení poplatků — jediné číslo, které skutečně platí."""
        return self.gross_pnl - self.fees

    @property
    def return_pct(self) -> float:
        cost = self.entry_price * self.quantity
        if cost == 0:
            return 0.0
        return self.net_pnl / cost * 100.0

    @property
    def is_win(self) -> bool:
        return self.net_pnl > 0

    @property
    def holding_days(self) -> float:
        return (self.exit_time - self.entry_time).total_seconds() / 86400.0


@dataclass
class Fill:
    """Provedený příkaz tak, jak ho vrátil broker."""

    symbol: str
    side: Side
    quantity: float
    price: float
    fees: float
    timestamp: datetime
    reason: str = ""


@dataclass
class EquityPoint:
    """Jeden bod equity křivky."""

    timestamp: datetime
    cash: float
    positions_value: float

    @property
    def equity(self) -> float:
        return self.cash + self.positions_value


@dataclass
class BacktestResult:
    """Kompletní výstup jednoho běhu backtestu."""

    strategy: str
    symbols: list[str]
    initial_capital: float
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[EquityPoint] = field(default_factory=list)
    rejected_signals: int = 0

    @property
    def final_equity(self) -> float:
        if not self.equity_curve:
            return self.initial_capital
        return self.equity_curve[-1].equity
