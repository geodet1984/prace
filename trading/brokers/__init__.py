"""Vrstva pro připojení k brokerovi.

Rozdělení odpovědnosti:

  * ``base``       — rozhraní ``Broker`` a datové typy, na kterých se všichni shodnou
  * ``simulated``  — plná implementace nad interním portfoliem (paper trading)
  * ``guard``      — bezpečnostní obal: limity, kill-switch, povinné potvrzení ostrého režimu
  * ``alpaca``     — adaptér na Alpaca REST API v2

Ostrý režim je záměrně nepohodlný. ``GuardedBroker`` odmítne odeslat příkaz
na reálný účet, dokud mu to nezapnete explicitně, a i pak hlídá strop na
objem příkazu a počet obchodů za den. Chyba v cyklu, která pošle tisíc
příkazů, je běžnější než chyba ve strategii.
"""

from .base import (
    AccountSnapshot,
    Broker,
    BrokerError,
    BrokerOrder,
    BrokerPosition,
    ConnectionError_,
    OrderRejected,
    OrderStatus,
    OrderType,
    TimeInForce,
    UnknownOrder,
)
from .guard import GuardedBroker, SafetyLimits, TradingHalted
from .simulated import SimulatedBroker

__all__ = [
    "AccountSnapshot",
    "Broker",
    "BrokerError",
    "BrokerOrder",
    "BrokerPosition",
    "ConnectionError_",
    "GuardedBroker",
    "OrderRejected",
    "OrderStatus",
    "OrderType",
    "SafetyLimits",
    "SimulatedBroker",
    "TimeInForce",
    "TradingHalted",
    "UnknownOrder",
]


def create(name: str, **kwargs) -> Broker:
    """Vytvoří brokera podle jména.

    ``simulated`` funguje vždy. ``alpaca`` se importuje až na vyžádání, aby
    zbytek projektu nezávisel na dostupnosti sítě ani na přihlašovacích údajích.
    """
    if name == "simulated":
        return SimulatedBroker(**kwargs)
    if name == "alpaca":
        from .alpaca import AlpacaBroker

        return AlpacaBroker(**kwargs)
    raise ValueError(f"neznámý broker {name!r}; dostupné: simulated, alpaca")
