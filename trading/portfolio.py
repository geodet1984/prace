"""Účetnictví portfolia: hotovost, otevřené pozice, uzavřené obchody."""

from __future__ import annotations

import logging
from datetime import datetime

from .models import EquityPoint, Fill, Position, Side, Trade

logger = logging.getLogger(__name__)


class InsufficientFunds(RuntimeError):
    """Pokus o nákup za peníze, které na účtu nejsou."""


class Portfolio:
    """Long-only portfolio bez páky.

    Drží veškerý stav účtu. Nic nepředpovídá a nic nerozhoduje — jen
    poctivě počítá, kolik peněz zbývá a jak dopadly jednotlivé obchody.
    """

    def __init__(self, initial_capital: float = 100_000.0) -> None:
        if initial_capital <= 0:
            raise ValueError("initial_capital musí být kladný")
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions: dict[str, Position] = {}
        self.trades: list[Trade] = []
        self.equity_curve: list[EquityPoint] = []
        self.fills: list[Fill] = []
        self.total_fees = 0.0

    # --- dotazy ---------------------------------------------------------

    def has_position(self, symbol: str) -> bool:
        return symbol in self.positions

    def get_position(self, symbol: str) -> Position | None:
        return self.positions.get(symbol)

    @property
    def open_position_count(self) -> int:
        return len(self.positions)

    def positions_value(self, prices: dict[str, float]) -> float:
        """Tržní hodnota otevřených pozic při zadaných cenách."""
        total = 0.0
        for symbol, pos in self.positions.items():
            price = prices.get(symbol)
            if price is None:
                # Bez aktuální ceny oceňujeme vstupní cenou — konzervativní
                # a hlavně nezkresluje equity náhodným výpadkem dat.
                price = pos.entry_price
            total += pos.market_value(price)
        return total

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + self.positions_value(prices)

    # --- operace --------------------------------------------------------

    def buy(
        self,
        symbol: str,
        quantity: float,
        price: float,
        fees: float,
        timestamp: datetime,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        reason: str = "",
        initial_risk: float = 0.0,
    ) -> Fill:
        """Otevře long pozici. Navyšování existující pozice není podporováno."""
        if quantity <= 0:
            raise ValueError("quantity musí být kladné")
        if symbol in self.positions:
            raise ValueError(f"{symbol}: pozice už je otevřená")

        cost = quantity * price + fees
        if cost > self.cash + 1e-9:
            raise InsufficientFunds(
                f"{symbol}: potřeba {cost:.2f}, k dispozici {self.cash:.2f}"
            )

        self.cash -= cost
        self.total_fees += fees
        self.positions[symbol] = Position(
            symbol=symbol,
            quantity=quantity,
            entry_price=price,
            entry_time=timestamp,
            stop_loss=stop_loss,
            take_profit=take_profit,
            initial_risk=initial_risk,
        )

        fill = Fill(symbol, Side.BUY, quantity, price, fees, timestamp, reason)
        self.fills.append(fill)
        logger.debug("BUY  %s %.4f @ %.2f (%s)", symbol, quantity, price, reason)
        return fill

    def sell(
        self,
        symbol: str,
        price: float,
        fees: float,
        timestamp: datetime,
        reason: str = "",
    ) -> tuple[Fill, Trade]:
        """Uzavře celou pozici a zapíše obchod."""
        position = self.positions.pop(symbol, None)
        if position is None:
            raise ValueError(f"{symbol}: žádná otevřená pozice k prodeji")

        proceeds = position.quantity * price - fees
        self.cash += proceeds
        self.total_fees += fees

        # Poplatky obou nohou obchodu se přičítají k tomuto obchodu, aby
        # net_pnl odpovídalo skutečné změně hotovosti.
        entry_fees = next(
            (
                f.fees
                for f in reversed(self.fills)
                if f.symbol == symbol and f.side is Side.BUY
            ),
            0.0,
        )

        trade = Trade(
            symbol=symbol,
            quantity=position.quantity,
            entry_time=position.entry_time,
            entry_price=position.entry_price,
            exit_time=timestamp,
            exit_price=price,
            fees=entry_fees + fees,
            exit_reason=reason,
        )
        self.trades.append(trade)

        fill = Fill(symbol, Side.SELL, position.quantity, price, fees, timestamp, reason)
        self.fills.append(fill)
        logger.debug(
            "SELL %s %.4f @ %.2f -> %.2f (%s)",
            symbol,
            position.quantity,
            price,
            trade.net_pnl,
            reason,
        )
        return fill, trade

    def mark_to_market(self, timestamp: datetime, prices: dict[str, float]) -> EquityPoint:
        """Zapíše bod equity křivky."""
        point = EquityPoint(
            timestamp=timestamp,
            cash=self.cash,
            positions_value=self.positions_value(prices),
        )
        self.equity_curve.append(point)
        return point

    def close_all(
        self, timestamp: datetime, prices: dict[str, float], fee_fn, reason: str = "konec období"
    ) -> list[Trade]:
        """Uzavře všechny pozice — použije se na konci backtestu.

        Bez tohohle by nerealizovaná ztráta zůstala schovaná mimo statistiku
        obchodů, což je přesně to zkreslení, kterým se "nikdy neprodělávající"
        systémy chlubí.
        """
        closed = []
        for symbol in list(self.positions):
            position = self.positions[symbol]
            price = prices.get(symbol, position.entry_price)
            fees = fee_fn(position.quantity, price)
            _, trade = self.sell(symbol, price, fees, timestamp, reason)
            closed.append(trade)
        return closed
