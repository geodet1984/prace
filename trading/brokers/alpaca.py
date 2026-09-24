"""Adaptér na Alpaca Trading API v2.

Proč zrovna Alpaca: má plnohodnotný paper účet se stejným API jako ostrý,
takže přechod z nanečisto na naostro znamená změnu jedné URL a klíčů.
Od roku 2026 má Alpaca evropskou entitu s passportingem do 29 zemí EHP
včetně Česka.

Implementováno přes ``urllib`` ze standardní knihovny — žádná další
závislost. REST rozhraní Alpaky je jednoduché a stabilní.

**Poctivé upozornění: tenhle adaptér nebyl ověřen proti živému API.**
Prostředí, ve kterém vznikl, nemá na burzovní servery přístup a nemám
přihlašovací údaje. Struktura koncových bodů a názvy polí odpovídají
dokumentaci Alpaca Trading API v2, ale než přes tohle pošlete první příkaz,
projděte si ``tests/test_brokers.py`` a hlavně si to sami vyzkoušejte na
paper účtu. Rozhraní ``Broker`` je záměrně malé právě proto, aby se dalo
proklepat za odpoledne.

Koncové body, na které se to mapuje:

    GET    /v2/account            stav účtu
    GET    /v2/positions          otevřené pozice
    GET    /v2/orders?status=open čekající příkazy
    POST   /v2/orders             odeslání příkazu
    DELETE /v2/orders/{id}        zrušení příkazu
    GET    /v2/clock              je otevřeno?
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from ..models import Side
from .base import (
    AccountSnapshot,
    Broker,
    BrokerOrder,
    BrokerPosition,
    ConnectionError_,
    OrderRejected,
    OrderStatus,
    OrderType,
    TimeInForce,
    UnknownOrder,
)

logger = logging.getLogger(__name__)

PAPER_URL = "https://paper-api.alpaca.markets"
LIVE_URL = "https://api.alpaca.markets"

#: Překlad našich stavů na stavy Alpaky.
_STATUS_MAP = {
    "new": OrderStatus.SUBMITTED,
    "accepted": OrderStatus.SUBMITTED,
    "pending_new": OrderStatus.SUBMITTED,
    "accepted_for_bidding": OrderStatus.SUBMITTED,
    "held": OrderStatus.SUBMITTED,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "filled": OrderStatus.FILLED,
    "done_for_day": OrderStatus.EXPIRED,
    "canceled": OrderStatus.CANCELLED,
    "pending_cancel": OrderStatus.CANCELLED,
    "expired": OrderStatus.EXPIRED,
    "replaced": OrderStatus.CANCELLED,
    "pending_replace": OrderStatus.SUBMITTED,
    "rejected": OrderStatus.REJECTED,
    "suspended": OrderStatus.REJECTED,
    "stopped": OrderStatus.REJECTED,
    "calculated": OrderStatus.SUBMITTED,
}

_TYPE_TO_ALPACA = {
    OrderType.MARKET: "market",
    OrderType.LIMIT: "limit",
    OrderType.STOP: "stop",
    OrderType.STOP_LIMIT: "stop_limit",
}

_TIF_TO_ALPACA = {
    TimeInForce.DAY: "day",
    TimeInForce.GTC: "gtc",
    TimeInForce.IOC: "ioc",
    TimeInForce.FOK: "fok",
    TimeInForce.OPG: "opg",
    TimeInForce.CLS: "cls",
}


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


class AlpacaBroker(Broker):
    """Klient Alpaca Trading API v2."""

    name = "alpaca"
    supports_fractional = True
    supports_opening_auction = True

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        paper: bool = True,
        base_url: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        if not api_key or not api_secret:
            raise ValueError("chybí api_key nebo api_secret")
        self.api_key = api_key
        self.api_secret = api_secret
        self.paper = paper
        self.base_url = (base_url or (PAPER_URL if paper else LIVE_URL)).rstrip("/")
        self.timeout = timeout
        self._connected = False

        if not paper:
            logger.warning("AlpacaBroker míří na OSTRÝ účet (%s)", self.base_url)

    @property
    def is_live(self) -> bool:
        return not self.paper

    # --- HTTP ------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        params: dict | None = None,
    ):
        url = f"{self.base_url}/v2/{path.lstrip('/')}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=body, method=method)
        request.add_header("APCA-API-KEY-ID", self.api_key)
        request.add_header("APCA-API-SECRET-KEY", self.api_secret)
        request.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code == 404:
                raise UnknownOrder(f"{method} {path}: nenalezeno ({detail})") from exc
            if exc.code in (401, 403):
                raise ConnectionError_(f"přihlášení odmítnuto ({exc.code}): {detail}") from exc
            if exc.code == 422:
                raise OrderRejected(f"broker příkaz odmítl: {detail}") from exc
            raise ConnectionError_(f"{method} {path} selhalo ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise ConnectionError_(f"{method} {path}: síť nedostupná ({exc.reason})") from exc
        except json.JSONDecodeError as exc:
            raise ConnectionError_(f"{method} {path}: odpověď není platný JSON") from exc

    # --- spojení ---------------------------------------------------------

    def connect(self) -> None:
        account = self._request("GET", "account")
        if account.get("status") not in (None, "ACTIVE"):
            logger.warning("účet není ve stavu ACTIVE, ale %s", account.get("status"))
        self._connected = True
        logger.info(
            "připojeno k Alpaca (%s), kapitál %s %s",
            "paper" if self.paper else "OSTRÝ ÚČET",
            account.get("equity"),
            account.get("currency", "USD"),
        )

    def disconnect(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    # --- stav ------------------------------------------------------------

    def get_account(self) -> AccountSnapshot:
        raw = self._request("GET", "account")
        equity = _to_float(raw.get("equity"))
        cash = _to_float(raw.get("cash"))
        return AccountSnapshot(
            cash=cash,
            equity=equity,
            buying_power=_to_float(raw.get("buying_power")),
            currency=raw.get("currency", "USD"),
            positions_value=_to_float(raw.get("long_market_value")),
            trading_blocked=bool(raw.get("trading_blocked") or raw.get("account_blocked")),
            pattern_day_trader=bool(raw.get("pattern_day_trader")),
            raw=raw,
        )

    def get_positions(self) -> list[BrokerPosition]:
        rows = self._request("GET", "positions")
        return [
            BrokerPosition(
                symbol=row["symbol"],
                quantity=_to_float(row.get("qty")),
                average_price=_to_float(row.get("avg_entry_price")),
                market_price=_to_float(row.get("current_price")) or None,
                market_value=_to_float(row.get("market_value")) or None,
                unrealized_pnl=_to_float(row.get("unrealized_pl")) or None,
            )
            for row in rows
        ]

    def get_last_price(self, symbol: str) -> float | None:
        """Poslední cena z otevřené pozice.

        Samostatná tržní data má Alpaca na jiném hostu (``data.alpaca.markets``)
        a s vlastními limity. Tenhle projekt bere ceny z ``trading.data``,
        takže se sem ta závislost netahá.
        """
        position = self.get_position(symbol)
        return position.market_price if position else None

    def is_market_open(self) -> bool:
        return bool(self._request("GET", "clock").get("is_open", False))

    # --- příkazy ---------------------------------------------------------

    def _to_order(self, raw: dict, template: BrokerOrder | None = None) -> BrokerOrder:
        """Převede odpověď Alpaky na náš typ."""
        side = Side.BUY if raw.get("side") == "buy" else Side.SELL
        order_type = next(
            (k for k, v in _TYPE_TO_ALPACA.items() if v == raw.get("type")), OrderType.MARKET
        )
        tif = next(
            (k for k, v in _TIF_TO_ALPACA.items() if v == raw.get("time_in_force")),
            TimeInForce.DAY,
        )

        order = BrokerOrder(
            symbol=raw.get("symbol", template.symbol if template else "?"),
            side=side,
            quantity=_to_float(raw.get("qty"), template.quantity if template else 1.0),
            order_type=order_type,
            time_in_force=tif,
            limit_price=_to_float(raw.get("limit_price")) or None,
            stop_price=_to_float(raw.get("stop_price")) or None,
        )
        order.order_id = str(raw.get("id", ""))
        order.client_order_id = str(raw.get("client_order_id", ""))
        order.status = _STATUS_MAP.get(raw.get("status", ""), OrderStatus.SUBMITTED)
        order.filled_quantity = _to_float(raw.get("filled_qty"))
        order.average_fill_price = _to_float(raw.get("filled_avg_price")) or None
        order.submitted_at = _parse_time(raw.get("submitted_at") or raw.get("created_at"))
        order.updated_at = _parse_time(raw.get("updated_at"))
        order.reason = template.reason if template else ""
        order.raw = raw
        return order

    def submit_order(self, order: BrokerOrder) -> BrokerOrder:
        payload = {
            "symbol": order.symbol,
            "qty": str(order.quantity),
            "side": "buy" if order.side is Side.BUY else "sell",
            "type": _TYPE_TO_ALPACA[order.order_type],
            "time_in_force": _TIF_TO_ALPACA[order.time_in_force],
        }
        if order.limit_price is not None:
            payload["limit_price"] = str(order.limit_price)
        if order.stop_price is not None:
            payload["stop_price"] = str(order.stop_price)
        if order.client_order_id:
            # Alpaca odmítne druhý příkaz se stejným client_order_id, což je
            # naše obrana proti duplicitě po restartu nebo timeoutu.
            payload["client_order_id"] = order.client_order_id

        return self._to_order(self._request("POST", "orders", payload), template=order)

    def cancel_order(self, order_id: str) -> None:
        self._request("DELETE", f"orders/{order_id}")

    def get_order(self, order_id: str) -> BrokerOrder:
        return self._to_order(self._request("GET", f"orders/{order_id}"))

    def get_open_orders(self) -> list[BrokerOrder]:
        rows = self._request("GET", "orders", params={"status": "open", "limit": 500})
        return [self._to_order(row) for row in rows]

    def describe(self) -> str:
        mode = "OSTRÝ ÚČET" if self.is_live else "paper"
        return f"alpaca ({mode}, {self.base_url})"
