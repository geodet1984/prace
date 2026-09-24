"""Simulovaný broker nad interním portfoliem.

Slouží ke dvěma věcem: je to plnohodnotný paper trading a zároveň referenční
implementace rozhraní, proti které se dá testovat všechno ostatní. Když
adaptér na reálného brokera projde stejnou sadou testů jako tenhle, je
šance, že se bude chovat stejně.

Plnění je záměrně jednoduché a lehce pesimistické: market příkaz se plní za
poslední známou cenu posunutou o skluz, limitní příkaz jen když cena úroveň
skutečně protne. Nic z toho nenahradí reálný orderbook, ale nic z toho ani
nelže ve váš prospěch.
"""

from __future__ import annotations

import itertools
import logging
from datetime import datetime

from ..execution import CostModel
from ..models import Side
from ..portfolio import InsufficientFunds, Portfolio
from .base import (
    AccountSnapshot,
    Broker,
    BrokerOrder,
    BrokerPosition,
    ConnectionError_,
    OrderRejected,
    OrderStatus,
    OrderType,
    UnknownOrder,
)

logger = logging.getLogger(__name__)


class SimulatedBroker(Broker):
    """Broker, který drží peníze jen na papíře."""

    name = "simulated"
    supports_fractional = True
    supports_opening_auction = True

    def __init__(
        self,
        initial_capital: float = 100_000.0,
        costs: CostModel | None = None,
        portfolio: Portfolio | None = None,
        currency: str = "USD",
        market_open: bool = True,
    ) -> None:
        self.portfolio = portfolio or Portfolio(initial_capital)
        self.costs = costs or CostModel()
        self.currency = currency
        self._market_open = market_open
        self._connected = False
        self._prices: dict[str, float] = {}
        self._orders: dict[str, BrokerOrder] = {}
        self._ids = itertools.count(1)

    # --- spojení ---------------------------------------------------------

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _require_connection(self) -> None:
        if not self._connected:
            raise ConnectionError_("broker není připojen — zavolejte connect()")

    # --- řízení simulace -------------------------------------------------

    def set_price(self, symbol: str, price: float) -> None:
        """Nastaví aktuální cenu. V simulaci ji dodává volající."""
        if price <= 0:
            raise ValueError("cena musí být kladná")
        self._prices[symbol] = price
        self._match_open_orders(symbol, price)

    def set_prices(self, prices: dict[str, float]) -> None:
        for symbol, price in prices.items():
            self.set_price(symbol, price)

    def set_market_open(self, is_open: bool) -> None:
        self._market_open = is_open

    # --- stav ------------------------------------------------------------

    def get_account(self) -> AccountSnapshot:
        self._require_connection()
        positions_value = self.portfolio.positions_value(self._prices)
        return AccountSnapshot(
            cash=self.portfolio.cash,
            equity=self.portfolio.cash + positions_value,
            buying_power=self.portfolio.cash,
            currency=self.currency,
            positions_value=positions_value,
        )

    def get_positions(self) -> list[BrokerPosition]:
        self._require_connection()
        out = []
        for position in self.portfolio.positions.values():
            price = self._prices.get(position.symbol)
            out.append(
                BrokerPosition(
                    symbol=position.symbol,
                    quantity=position.quantity,
                    average_price=position.entry_price,
                    market_price=price,
                    market_value=position.market_value(price) if price else None,
                    unrealized_pnl=position.unrealized_pnl(price) if price else None,
                )
            )
        return out

    def get_last_price(self, symbol: str) -> float | None:
        return self._prices.get(symbol)

    def is_market_open(self) -> bool:
        return self._market_open

    # --- příkazy ---------------------------------------------------------

    def submit_order(self, order: BrokerOrder) -> BrokerOrder:
        self._require_connection()

        if order.client_order_id:
            existing = next(
                (o for o in self._orders.values() if o.client_order_id == order.client_order_id),
                None,
            )
            if existing is not None:
                # Ochrana proti dvojímu odeslání téhož příkazu po restartu.
                logger.warning(
                    "příkaz s client_order_id %s už existuje, vracím původní",
                    order.client_order_id,
                )
                return existing

        order.order_id = f"sim-{next(self._ids)}"
        order.status = OrderStatus.SUBMITTED
        order.submitted_at = datetime.now()
        order.updated_at = order.submitted_at
        self._orders[order.order_id] = order

        price = self._prices.get(order.symbol)
        if order.order_type is OrderType.MARKET:
            if price is None:
                order.status = OrderStatus.REJECTED
                order.reason = "neznámá cena instrumentu"
                raise OrderRejected(f"{order.symbol}: neznámá cena, market příkaz nelze plnit")
            self._fill(order, price)
        elif price is not None:
            self._try_match(order, price)

        return order

    def cancel_order(self, order_id: str) -> None:
        self._require_connection()
        order = self._orders.get(order_id)
        if order is None:
            raise UnknownOrder(f"příkaz {order_id} neexistuje")
        if order.status.is_done:
            return
        order.status = OrderStatus.CANCELLED
        order.updated_at = datetime.now()

    def get_order(self, order_id: str) -> BrokerOrder:
        self._require_connection()
        order = self._orders.get(order_id)
        if order is None:
            raise UnknownOrder(f"příkaz {order_id} neexistuje")
        return order

    def get_open_orders(self) -> list[BrokerOrder]:
        self._require_connection()
        return [o for o in self._orders.values() if o.status.is_open]

    # --- plnění ----------------------------------------------------------

    def _match_open_orders(self, symbol: str, price: float) -> None:
        for order in list(self._orders.values()):
            if order.status.is_open and order.symbol == symbol:
                self._try_match(order, price)

    def _try_match(self, order: BrokerOrder, price: float) -> None:
        """Zjistí, jestli cena aktivovala podmíněný příkaz."""
        if order.order_type is OrderType.MARKET:
            self._fill(order, price)
            return

        if order.order_type is OrderType.LIMIT:
            crossed = (
                price <= order.limit_price
                if order.side is Side.BUY
                else price >= order.limit_price
            )
            if crossed:
                self._fill(order, order.limit_price)
            return

        if order.order_type is OrderType.STOP:
            triggered = (
                price >= order.stop_price if order.side is Side.BUY else price <= order.stop_price
            )
            if triggered:
                self._fill(order, price)
            return

        if order.order_type is OrderType.STOP_LIMIT:
            triggered = (
                price >= order.stop_price if order.side is Side.BUY else price <= order.stop_price
            )
            if triggered:
                order.order_type = OrderType.LIMIT
                self._try_match(order, price)

    def _fill(self, order: BrokerOrder, price: float) -> None:
        """Provede plnění a promítne ho do portfolia."""
        fill_price = self.costs.fill_price(order.side, price)
        fees = self.costs.commission(order.quantity, fill_price)
        now = datetime.now()

        try:
            if order.side is Side.BUY:
                if self.portfolio.has_position(order.symbol):
                    raise OrderRejected(f"{order.symbol}: pozice už je otevřená")
                self.portfolio.buy(
                    order.symbol,
                    order.quantity,
                    fill_price,
                    fees,
                    now,
                    reason=order.reason,
                )
            else:
                if not self.portfolio.has_position(order.symbol):
                    raise OrderRejected(f"{order.symbol}: žádná pozice k prodeji")
                self.portfolio.sell(order.symbol, fill_price, fees, now, order.reason)
        except (InsufficientFunds, OrderRejected, ValueError) as exc:
            order.status = OrderStatus.REJECTED
            order.reason = str(exc)
            order.updated_at = now
            raise OrderRejected(str(exc)) from exc

        order.status = OrderStatus.FILLED
        order.filled_quantity = order.quantity
        order.average_fill_price = fill_price
        order.updated_at = now
