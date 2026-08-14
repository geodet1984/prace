"""Rozhraní brokera a společné datové typy.

Cílem je, aby strategie a risk manager nevěděly, jestli běží proti simulaci
nebo proti reálnému účtu. Jediný rozdíl má být v tom, který objekt se předá.

Návrhová rozhodnutí, která stojí za vysvětlení:

* **Příkazy jsou asynchronní.** ``submit_order`` vrátí příkaz ve stavu
  ``SUBMITTED``, ne hotové plnění. Reálný broker potvrzuje se zpožděním,
  částečně plní a občas odmítne. Rozhraní, které tváří odeslání jako
  okamžité plnění, se při přechodu na ostrý účet rozpadne.

* **Pravdu má broker, ne naše evidence.** ``get_positions`` a ``get_account``
  se ptají brokera. Lokální stav slouží k rozhodování, ne k účetnictví —
  po výpadku spojení se rozejdou a smiřovat se musí podle brokera.

* **``client_order_id`` je povinná praxe.** Umožní poznat vlastní příkaz po
  restartu a je jedinou obranou proti duplicitnímu odeslání.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..models import Side


class BrokerError(RuntimeError):
    """Základ pro všechny chyby brokerské vrstvy."""


class ConnectionError_(BrokerError):
    """Spojení s brokerem není navázané nebo spadlo."""


class OrderRejected(BrokerError):
    """Broker příkaz odmítl (nedostatek prostředků, uzavřený trh, ...)."""


class UnknownOrder(BrokerError):
    """Příkaz s daným identifikátorem broker nezná."""


class OrderStatus(str, Enum):
    """Životní cyklus příkazu."""

    PENDING = "PENDING"
    """Vytvořen lokálně, ještě neodeslán."""

    SUBMITTED = "SUBMITTED"
    """Odeslán brokerovi, čeká na trhu."""

    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

    @property
    def is_open(self) -> bool:
        return self in (OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED)

    @property
    def is_done(self) -> bool:
        return not self.is_open


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class TimeInForce(str, Enum):
    """Jak dlouho příkaz platí."""

    DAY = "DAY"
    GTC = "GTC"
    """Good till cancelled — platí, dokud ho nezrušíte."""

    IOC = "IOC"
    """Immediate or cancel — co nejde plnit hned, se zruší."""

    FOK = "FOK"
    """Fill or kill — buď celý, nebo nic."""

    OPG = "OPG"
    """Účast v otevírací aukci. Odpovídá tomu, jak plní backtest engine."""

    CLS = "CLS"
    """Účast v uzavírací aukci."""


@dataclass
class BrokerOrder:
    """Příkaz tak, jak ho eviduje broker."""

    symbol: str
    side: Side
    quantity: float
    order_type: OrderType = OrderType.MARKET
    time_in_force: TimeInForce = TimeInForce.DAY
    limit_price: float | None = None
    stop_price: float | None = None

    order_id: str = ""
    """Identifikátor u brokera. Prázdný, dokud není příkaz odeslán."""

    client_order_id: str = ""
    """Náš vlastní identifikátor — brání duplicitnímu odeslání po restartu."""

    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: float = 0.0
    average_fill_price: float | None = None
    submitted_at: datetime | None = None
    updated_at: datetime | None = None
    reason: str = ""
    """Důvod ze strategie, nebo text zamítnutí od brokera."""

    raw: dict = field(default_factory=dict)
    """Původní odpověď brokera pro ladění."""

    @property
    def remaining_quantity(self) -> float:
        return max(0.0, self.quantity - self.filled_quantity)

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("quantity musí být kladná")
        if self.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and self.limit_price is None:
            raise ValueError(f"{self.order_type.value} vyžaduje limit_price")
        if self.order_type in (OrderType.STOP, OrderType.STOP_LIMIT) and self.stop_price is None:
            raise ValueError(f"{self.order_type.value} vyžaduje stop_price")


@dataclass
class BrokerPosition:
    """Otevřená pozice podle brokera."""

    symbol: str
    quantity: float
    average_price: float
    market_price: float | None = None
    market_value: float | None = None
    unrealized_pnl: float | None = None

    @property
    def is_long(self) -> bool:
        return self.quantity > 0


@dataclass
class AccountSnapshot:
    """Stav účtu v jednom okamžiku."""

    cash: float
    equity: float
    buying_power: float
    currency: str = "USD"
    positions_value: float = 0.0
    trading_blocked: bool = False
    """Broker účet zablokoval — typicky nedodané dokumenty nebo margin call."""

    pattern_day_trader: bool = False
    """Označení PDT. U účtů pod 25 000 USD omezuje počet denních obchodů."""

    raw: dict = field(default_factory=dict)


class Broker(ABC):
    """Rozhraní, které musí splnit každý adaptér.

    Implementace nemá řešit strategii ani velikost pozice. Jejím jediným
    úkolem je věrně přeložit tyhle metody na to, co broker skutečně nabízí,
    a nepředstírat schopnosti, které nemá.
    """

    name: str = "base"

    #: Podporuje broker zlomkové akcie?
    supports_fractional: bool = False

    #: Umí broker příkazy do otevírací aukce (``TimeInForce.OPG``)?
    supports_opening_auction: bool = False

    # --- spojení ---------------------------------------------------------

    @abstractmethod
    def connect(self) -> None:
        """Naváže spojení a ověří přihlašovací údaje."""

    @abstractmethod
    def disconnect(self) -> None:
        """Ukončí spojení. Musí být idempotentní."""

    @property
    @abstractmethod
    def is_connected(self) -> bool: ...

    def __enter__(self) -> Broker:
        self.connect()
        return self

    def __exit__(self, *exc_info) -> None:
        self.disconnect()

    # --- stav účtu -------------------------------------------------------

    @abstractmethod
    def get_account(self) -> AccountSnapshot: ...

    @abstractmethod
    def get_positions(self) -> list[BrokerPosition]: ...

    def get_position(self, symbol: str) -> BrokerPosition | None:
        return next((p for p in self.get_positions() if p.symbol == symbol), None)

    # --- příkazy ---------------------------------------------------------

    @abstractmethod
    def submit_order(self, order: BrokerOrder) -> BrokerOrder:
        """Odešle příkaz. Vrací jeho aktualizovanou podobu.

        Návrat **neznamená plnění**, jen že broker příkaz přijal.
        """

    @abstractmethod
    def cancel_order(self, order_id: str) -> None: ...

    @abstractmethod
    def get_order(self, order_id: str) -> BrokerOrder: ...

    @abstractmethod
    def get_open_orders(self) -> list[BrokerOrder]: ...

    def cancel_all_orders(self) -> int:
        """Zruší všechny čekající příkazy. Vrací počet zrušených."""
        count = 0
        for order in self.get_open_orders():
            if order.order_id:
                self.cancel_order(order.order_id)
                count += 1
        return count

    # --- trh -------------------------------------------------------------

    @abstractmethod
    def is_market_open(self) -> bool: ...

    def get_last_price(self, symbol: str) -> float | None:
        """Poslední známá cena. Adaptér ji nemusí umět — pak vrací None."""
        return None

    # --- ostrý režim -----------------------------------------------------

    @property
    def is_live(self) -> bool:
        """Obchoduje tenhle adaptér s reálnými penězi?

        Používá to ``GuardedBroker``, aby poznal, kdy má být přísný. Adaptér,
        který to neumí spolehlivě určit, ať vrací ``True`` — konzervativní
        odhad tady stojí míň než ten opačný.
        """
        return False

    def describe(self) -> str:
        mode = "OSTRÝ ÚČET" if self.is_live else "simulace / paper"
        return f"{self.name} ({mode})"
