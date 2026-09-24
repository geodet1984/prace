"""Bezpečnostní obal kolem libovolného brokera.

Největší riziko automatického obchodování není špatná strategie. Je to chyba
v kódu, která pošle příkaz stokrát, nebo překlep v desetinné čárce. Strategie
prodělá pomalu a je vidět; smyčka bez pojistky vyprázdní účet za minutu.

``GuardedBroker`` proto stojí mezi vaším kódem a brokerem a vynucuje:

* **explicitní souhlas s ostrým režimem** — bez ``allow_live=True`` neodejde
  na reálný účet ani jeden příkaz
* **strop na objem jednoho příkazu** a na součet za den
* **strop na počet příkazů za den** — pojistka proti smyčce
* **seznam povolených titulů** — chyba v symbolu nikam neodejde
* **kill-switch** — jedno volání zastaví všechno
* **režim nanečisto** — příkazy se jen zaznamenají, neodešlou

Limity se kontrolují **před** odesláním. Když některý nesedí, příkaz se
neodešle a vyletí výjimka.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from .base import (
    AccountSnapshot,
    Broker,
    BrokerError,
    BrokerOrder,
    BrokerPosition,
    OrderStatus,
)

logger = logging.getLogger(__name__)


class TradingHalted(BrokerError):
    """Příkaz neprošel pojistkou. Na brokera se vůbec neodeslal."""


@dataclass
class SafetyLimits:
    """Meze, které nesmí automat překročit.

    Výchozí hodnoty jsou schválně nízké. Kdo je zvyšuje, ať ví proč.
    """

    max_order_value: float = 5_000.0
    """Strop na hodnotu jednoho příkazu v měně účtu."""

    max_daily_traded_value: float = 25_000.0
    """Strop na součet hodnot příkazů za jeden den."""

    max_orders_per_day: int = 20
    """Strop na počet příkazů za den — pojistka proti zacyklení."""

    max_position_pct_of_equity: float = 0.25
    """Strop na podíl jedné pozice na kapitálu."""

    allowed_symbols: set[str] | None = None
    """Povolené tickery. ``None`` znamená bez omezení."""

    require_market_open: bool = True
    """Odmítat příkazy mimo obchodní hodiny."""

    def __post_init__(self) -> None:
        if self.max_order_value <= 0 or self.max_daily_traded_value <= 0:
            raise ValueError("stropy na objem musí být kladné")
        if self.max_orders_per_day < 1:
            raise ValueError("max_orders_per_day musí být aspoň 1")
        if not 0 < self.max_position_pct_of_equity <= 1:
            raise ValueError("max_position_pct_of_equity musí být v (0, 1]")


@dataclass
class _DailyTally:
    """Denní počitadla, která se o půlnoci resetují."""

    day: date | None = None
    order_count: int = 0
    traded_value: float = 0.0
    rejected: list[str] = field(default_factory=list)

    def roll(self, today: date) -> None:
        if self.day != today:
            self.day = today
            self.order_count = 0
            self.traded_value = 0.0
            self.rejected = []


class GuardedBroker(Broker):
    """Obal, který propustí jen příkazy splňující nastavené meze.

    Dotazy na stav (``get_account``, ``get_positions``, ...) prochází beze
    změny. Hlídají se jen operace, které něco mění.
    """

    name = "guarded"

    def __init__(
        self,
        broker: Broker,
        limits: SafetyLimits | None = None,
        allow_live: bool = False,
        dry_run: bool = False,
    ) -> None:
        self.broker = broker
        self.limits = limits or SafetyLimits()
        self.allow_live = allow_live
        self.dry_run = dry_run
        self.tally = _DailyTally()
        self._halted_reason: str | None = None
        self.dry_run_log: list[BrokerOrder] = []

        if broker.is_live and not allow_live:
            logger.warning(
                "%s je ostrý účet, ale allow_live=False — příkazy budou odmítány",
                broker.name,
            )

    # --- kill-switch -----------------------------------------------------

    def halt(self, reason: str) -> None:
        """Okamžitě zastaví odesílání příkazů."""
        self._halted_reason = reason
        logger.error("OBCHODOVÁNÍ ZASTAVENO: %s", reason)

    def resume(self) -> None:
        self._halted_reason = None

    @property
    def is_halted(self) -> bool:
        return self._halted_reason is not None

    @property
    def halted_reason(self) -> str | None:
        return self._halted_reason

    # --- delegované dotazy -----------------------------------------------

    def connect(self) -> None:
        self.broker.connect()

    def disconnect(self) -> None:
        self.broker.disconnect()

    @property
    def is_connected(self) -> bool:
        return self.broker.is_connected

    @property
    def is_live(self) -> bool:
        return self.broker.is_live

    def get_account(self) -> AccountSnapshot:
        return self.broker.get_account()

    def get_positions(self) -> list[BrokerPosition]:
        return self.broker.get_positions()

    def get_order(self, order_id: str) -> BrokerOrder:
        return self.broker.get_order(order_id)

    def get_open_orders(self) -> list[BrokerOrder]:
        return self.broker.get_open_orders()

    def is_market_open(self) -> bool:
        return self.broker.is_market_open()

    def get_last_price(self, symbol: str) -> float | None:
        return self.broker.get_last_price(symbol)

    # --- hlídané operace -------------------------------------------------

    def _estimate_value(self, order: BrokerOrder) -> float:
        """Odhad hodnoty příkazu. Když cena chybí, vrací nekonečno.

        Nekonečno znamená, že příkaz neprojde stropem — což je správně.
        Odesílat příkaz, jehož hodnotu neumíme odhadnout, je hazard.
        """
        price = order.limit_price or order.stop_price or self.broker.get_last_price(order.symbol)
        if price is None or price <= 0:
            return float("inf")
        return order.quantity * price

    def _check(self, order: BrokerOrder) -> None:
        """Projde všechny pojistky. Při porušení vyhodí ``TradingHalted``."""
        limits = self.limits
        today = date.today()
        self.tally.roll(today)

        if self.is_halted:
            raise TradingHalted(f"kill-switch aktivní: {self._halted_reason}")

        if self.broker.is_live and not self.allow_live:
            raise TradingHalted(
                "cíl je ostrý účet, ale ostrý režim nebyl povolen — "
                "vyžaduje se GuardedBroker(..., allow_live=True)"
            )

        if limits.allowed_symbols is not None and order.symbol not in limits.allowed_symbols:
            raise TradingHalted(f"{order.symbol} není na seznamu povolených titulů")

        if limits.require_market_open and not self.broker.is_market_open():
            raise TradingHalted("trh je zavřený")

        if self.tally.order_count >= limits.max_orders_per_day:
            raise TradingHalted(
                f"vyčerpán denní limit {limits.max_orders_per_day} příkazů "
                "(pojistka proti zacyklení)"
            )

        value = self._estimate_value(order)
        if value == float("inf"):
            raise TradingHalted(f"{order.symbol}: neznámá cena, hodnotu příkazu nelze ověřit")

        if value > limits.max_order_value:
            raise TradingHalted(
                f"{order.symbol}: hodnota příkazu {value:,.0f} přesahuje strop "
                f"{limits.max_order_value:,.0f}"
            )

        if self.tally.traded_value + value > limits.max_daily_traded_value:
            raise TradingHalted(
                f"denní objem by dosáhl {self.tally.traded_value + value:,.0f}, "
                f"strop je {limits.max_daily_traded_value:,.0f}"
            )

        # Strop na expozici se dá ověřit jen se známým kapitálem.
        try:
            account = self.broker.get_account()
        except BrokerError:
            account = None

        if account is not None and account.equity > 0:
            if account.trading_blocked:
                raise TradingHalted("broker účet zablokoval")
            share = value / account.equity
            if share > limits.max_position_pct_of_equity:
                raise TradingHalted(
                    f"{order.symbol}: pozice by tvořila {share:.0%} kapitálu, "
                    f"strop je {limits.max_position_pct_of_equity:.0%}"
                )

    def submit_order(self, order: BrokerOrder) -> BrokerOrder:
        self._check(order)
        value = self._estimate_value(order)

        if self.dry_run:
            order.status = OrderStatus.PENDING
            order.reason = (order.reason + " [nanečisto]").strip()
            self.dry_run_log.append(order)
            logger.info(
                "NANEČISTO %s %s %.4f (%.0f)", order.side.value, order.symbol, order.quantity, value
            )
            return order

        submitted = self.broker.submit_order(order)
        self.tally.order_count += 1
        self.tally.traded_value += value
        return submitted

    def cancel_order(self, order_id: str) -> None:
        # Rušení příkazů se nikdy neblokuje — je to vždy krok k menšímu riziku.
        self.broker.cancel_order(order_id)

    def status(self) -> str:
        """Přehled stavu pojistek."""
        self.tally.roll(date.today())
        lines = [
            f"  Broker            {self.broker.describe()}",
            f"  Ostrý režim       {'povolen' if self.allow_live else 'ZAKÁZÁN'}",
            f"  Nanečisto         {'ano' if self.dry_run else 'ne'}",
            f"  Kill-switch       {self._halted_reason or 'neaktivní'}",
            f"  Příkazů dnes      {self.tally.order_count} / {self.limits.max_orders_per_day}",
            f"  Objem dnes        {self.tally.traded_value:,.0f} / "
            f"{self.limits.max_daily_traded_value:,.0f}",
        ]
        if self.limits.allowed_symbols:
            lines.append(f"  Povolené tituly   {', '.join(sorted(self.limits.allowed_symbols))}")
        return "\n".join(lines)
