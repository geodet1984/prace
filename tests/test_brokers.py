"""Testy brokerské vrstvy.

Těžiště je na pojistkách. Chyba ve strategii stojí peníze pomalu a je vidět;
smyčka bez pojistky vyprázdní účet za minutu. Každý test tady odpovídá jedné
chybě, kterou je snadné udělat.
"""

import pytest

from trading.brokers import (
    BrokerOrder,
    ConnectionError_,
    GuardedBroker,
    OrderRejected,
    OrderStatus,
    OrderType,
    SafetyLimits,
    SimulatedBroker,
    TimeInForce,
    TradingHalted,
    UnknownOrder,
    create,
)
from trading.execution import ZERO_COST, CostModel
from trading.models import Side


@pytest.fixture
def broker():
    b = SimulatedBroker(100_000.0, ZERO_COST)
    b.connect()
    b.set_price("AAPL", 200.0)
    return b


def loose_limits(**overrides):
    base = {
        "max_order_value": 1e9,
        "max_daily_traded_value": 1e9,
        "max_orders_per_day": 1000,
        "max_position_pct_of_equity": 1.0,
    }
    base.update(overrides)
    return SafetyLimits(**base)


class FakeLiveBroker(SimulatedBroker):
    """Simulace, která se tváří jako ostrý účet."""

    name = "fake-live"

    @property
    def is_live(self) -> bool:
        return True


# --- rozhraní a validace ------------------------------------------------


def test_order_requires_positive_quantity():
    with pytest.raises(ValueError):
        BrokerOrder("AAPL", Side.BUY, 0)


def test_limit_order_requires_price():
    with pytest.raises(ValueError, match="limit_price"):
        BrokerOrder("AAPL", Side.BUY, 1, OrderType.LIMIT)


def test_stop_order_requires_stop_price():
    with pytest.raises(ValueError, match="stop_price"):
        BrokerOrder("AAPL", Side.BUY, 1, OrderType.STOP)


def test_order_status_open_and_done_are_complementary():
    for status in OrderStatus:
        assert status.is_open != status.is_done


def test_factory_creates_simulated():
    assert create("simulated").name == "simulated"


def test_factory_rejects_unknown():
    with pytest.raises(ValueError, match="neznámý broker"):
        create("neexistuje")


def test_operations_require_connection():
    b = SimulatedBroker()
    with pytest.raises(ConnectionError_):
        b.get_account()


def test_context_manager_connects_and_disconnects():
    b = SimulatedBroker()
    with b as connected:
        assert connected.is_connected
    assert not b.is_connected


# --- simulovaný broker --------------------------------------------------


def test_market_buy_fills_immediately(broker):
    order = broker.submit_order(BrokerOrder("AAPL", Side.BUY, 10))
    assert order.status is OrderStatus.FILLED
    assert order.filled_quantity == 10
    assert order.average_fill_price == pytest.approx(200.0)
    assert order.remaining_quantity == 0


def test_market_order_without_price_is_rejected(broker):
    with pytest.raises(OrderRejected, match="neznámá cena"):
        broker.submit_order(BrokerOrder("NEZNAMY", Side.BUY, 1))


def test_buy_reduces_cash_and_creates_position(broker):
    broker.submit_order(BrokerOrder("AAPL", Side.BUY, 10))
    account = broker.get_account()
    assert account.cash == pytest.approx(98_000.0)
    assert account.equity == pytest.approx(100_000.0)
    assert [p.symbol for p in broker.get_positions()] == ["AAPL"]


def test_round_trip_realizes_profit(broker):
    broker.submit_order(BrokerOrder("AAPL", Side.BUY, 10))
    broker.set_price("AAPL", 220.0)
    broker.submit_order(BrokerOrder("AAPL", Side.SELL, 10))
    assert broker.get_account().cash == pytest.approx(100_200.0)
    assert not broker.get_positions()


def test_slippage_moves_price_against_us():
    b = SimulatedBroker(100_000.0, CostModel(slippage_bps=50))
    b.connect()
    b.set_price("AAPL", 200.0)
    order = b.submit_order(BrokerOrder("AAPL", Side.BUY, 1))
    assert order.average_fill_price == pytest.approx(201.0)


def test_selling_without_position_is_rejected(broker):
    with pytest.raises(OrderRejected, match="žádná pozice"):
        broker.submit_order(BrokerOrder("AAPL", Side.SELL, 1))


def test_buying_beyond_cash_is_rejected(broker):
    with pytest.raises(OrderRejected):
        broker.submit_order(BrokerOrder("AAPL", Side.BUY, 10_000))


def test_limit_order_waits_until_price_is_crossed(broker):
    order = broker.submit_order(
        BrokerOrder("AAPL", Side.BUY, 5, OrderType.LIMIT, limit_price=190.0)
    )
    assert order.status is OrderStatus.SUBMITTED
    assert len(broker.get_open_orders()) == 1

    broker.set_price("AAPL", 195.0)
    assert broker.get_order(order.order_id).status is OrderStatus.SUBMITTED

    broker.set_price("AAPL", 189.0)
    filled = broker.get_order(order.order_id)
    assert filled.status is OrderStatus.FILLED
    assert filled.average_fill_price == pytest.approx(190.0)


def test_stop_order_triggers_on_the_way_down(broker):
    broker.submit_order(BrokerOrder("AAPL", Side.BUY, 5))
    order = broker.submit_order(
        BrokerOrder("AAPL", Side.SELL, 5, OrderType.STOP, stop_price=190.0)
    )
    assert order.status is OrderStatus.SUBMITTED

    broker.set_price("AAPL", 185.0)
    assert broker.get_order(order.order_id).status is OrderStatus.FILLED
    assert not broker.get_positions()


def test_cancel_removes_order_from_the_book(broker):
    order = broker.submit_order(
        BrokerOrder("AAPL", Side.BUY, 5, OrderType.LIMIT, limit_price=100.0)
    )
    broker.cancel_order(order.order_id)
    assert broker.get_order(order.order_id).status is OrderStatus.CANCELLED
    assert not broker.get_open_orders()


def test_cancel_is_idempotent(broker):
    order = broker.submit_order(
        BrokerOrder("AAPL", Side.BUY, 5, OrderType.LIMIT, limit_price=100.0)
    )
    broker.cancel_order(order.order_id)
    broker.cancel_order(order.order_id)
    assert broker.get_order(order.order_id).status is OrderStatus.CANCELLED


def test_cancel_all_orders(broker):
    broker.set_price("MSFT", 300.0)
    broker.submit_order(BrokerOrder("AAPL", Side.BUY, 1, OrderType.LIMIT, limit_price=100.0))
    broker.submit_order(BrokerOrder("MSFT", Side.BUY, 1, OrderType.LIMIT, limit_price=100.0))
    assert broker.cancel_all_orders() == 2
    assert not broker.get_open_orders()


def test_unknown_order_id_raises(broker):
    with pytest.raises(UnknownOrder):
        broker.get_order("nic-takoveho")


def test_duplicate_client_order_id_returns_the_original(broker):
    """Ochrana proti dvojímu odeslání po restartu nebo timeoutu."""
    first = BrokerOrder("AAPL", Side.BUY, 1)
    first.client_order_id = "moje-id-1"
    submitted = broker.submit_order(first)

    second = BrokerOrder("AAPL", Side.BUY, 1)
    second.client_order_id = "moje-id-1"
    again = broker.submit_order(second)

    assert again.order_id == submitted.order_id
    assert len(broker.portfolio.trades) == 0
    assert len(broker.get_positions()) == 1


def test_get_position_by_symbol(broker):
    broker.submit_order(BrokerOrder("AAPL", Side.BUY, 3))
    assert broker.get_position("AAPL").quantity == 3
    assert broker.get_position("MSFT") is None


def test_market_hours_flag(broker):
    assert broker.is_market_open()
    broker.set_market_open(False)
    assert not broker.is_market_open()


# --- pojistky -----------------------------------------------------------


def test_live_account_refuses_orders_without_explicit_opt_in():
    """Nejdůležitější pojistka: na reálný účet se nedostane nic omylem."""
    live = FakeLiveBroker(100_000.0, ZERO_COST)
    live.connect()
    live.set_price("AAPL", 200.0)

    guarded = GuardedBroker(live, loose_limits(), allow_live=False)
    with pytest.raises(TradingHalted, match="ostrý režim nebyl povolen"):
        guarded.submit_order(BrokerOrder("AAPL", Side.BUY, 1))

    assert not live.get_positions()


def test_live_account_works_with_opt_in():
    live = FakeLiveBroker(100_000.0, ZERO_COST)
    live.connect()
    live.set_price("AAPL", 200.0)
    guarded = GuardedBroker(live, loose_limits(), allow_live=True)
    assert guarded.submit_order(BrokerOrder("AAPL", Side.BUY, 1)).status is OrderStatus.FILLED


def test_order_value_cap(broker):
    guarded = GuardedBroker(broker, loose_limits(max_order_value=1_000.0))
    with pytest.raises(TradingHalted, match="přesahuje strop"):
        guarded.submit_order(BrokerOrder("AAPL", Side.BUY, 100))


def test_daily_value_cap(broker):
    guarded = GuardedBroker(broker, loose_limits(max_daily_traded_value=2_500.0))
    guarded.submit_order(BrokerOrder("AAPL", Side.BUY, 10))
    broker.set_price("MSFT", 200.0)
    with pytest.raises(TradingHalted, match="denní objem"):
        guarded.submit_order(BrokerOrder("MSFT", Side.BUY, 10))


def test_daily_order_count_cap_stops_runaway_loop():
    """Zacyklený kód nesmí poslat víc příkazů, než kolik je povoleno."""
    b = SimulatedBroker(1_000_000.0, ZERO_COST)
    b.connect()
    for symbol in "ABCDE":
        b.set_price(symbol, 100.0)

    guarded = GuardedBroker(b, loose_limits(max_orders_per_day=3))
    sent = 0
    with pytest.raises(TradingHalted, match="zacyklení"):
        for symbol in "ABCDE":
            guarded.submit_order(BrokerOrder(symbol, Side.BUY, 1))
            sent += 1
    assert sent == 3


def test_symbol_allowlist(broker):
    guarded = GuardedBroker(broker, loose_limits(allowed_symbols={"MSFT"}))
    with pytest.raises(TradingHalted, match="není na seznamu"):
        guarded.submit_order(BrokerOrder("AAPL", Side.BUY, 1))


def test_position_share_cap(broker):
    guarded = GuardedBroker(broker, loose_limits(max_position_pct_of_equity=0.05))
    with pytest.raises(TradingHalted, match="kapitálu"):
        guarded.submit_order(BrokerOrder("AAPL", Side.BUY, 100))


def test_closed_market_blocks_orders(broker):
    broker.set_market_open(False)
    guarded = GuardedBroker(broker, loose_limits(require_market_open=True))
    with pytest.raises(TradingHalted, match="zavřený"):
        guarded.submit_order(BrokerOrder("AAPL", Side.BUY, 1))


def test_unknown_price_blocks_the_order(broker):
    """Příkaz, jehož hodnotu neumíme ověřit, neprojde."""
    guarded = GuardedBroker(broker, loose_limits())
    with pytest.raises(TradingHalted, match="neznámá cena"):
        guarded.submit_order(BrokerOrder("NEZNAMY", Side.BUY, 1))


def test_kill_switch_blocks_everything(broker):
    guarded = GuardedBroker(broker, loose_limits())
    guarded.halt("ruční zásah")
    with pytest.raises(TradingHalted, match="kill-switch"):
        guarded.submit_order(BrokerOrder("AAPL", Side.BUY, 1))

    guarded.resume()
    assert guarded.submit_order(BrokerOrder("AAPL", Side.BUY, 1)).status is OrderStatus.FILLED


def test_cancel_is_never_blocked(broker):
    """Rušení je vždy krok k menšímu riziku, takže ho pojistky nezastavují."""
    order = broker.submit_order(
        BrokerOrder("AAPL", Side.BUY, 1, OrderType.LIMIT, limit_price=100.0)
    )
    guarded = GuardedBroker(broker, loose_limits())
    guarded.halt("cokoli")
    guarded.cancel_order(order.order_id)
    assert broker.get_order(order.order_id).status is OrderStatus.CANCELLED


def test_dry_run_sends_nothing(broker):
    guarded = GuardedBroker(broker, loose_limits(), dry_run=True)
    order = guarded.submit_order(BrokerOrder("AAPL", Side.BUY, 10))

    assert order.status is OrderStatus.PENDING
    assert len(guarded.dry_run_log) == 1
    assert not broker.get_positions()
    assert broker.get_account().cash == pytest.approx(100_000.0)


def test_guard_passes_through_queries(broker):
    guarded = GuardedBroker(broker, loose_limits())
    assert guarded.get_account().equity == pytest.approx(100_000.0)
    assert guarded.is_market_open()
    assert guarded.get_last_price("AAPL") == 200.0


def test_guard_status_renders(broker):
    guarded = GuardedBroker(broker, loose_limits(allowed_symbols={"AAPL"}))
    text = guarded.status()
    assert "Ostrý režim" in text and "Kill-switch" in text


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_order_value": 0},
        {"max_daily_traded_value": -1},
        {"max_orders_per_day": 0},
        {"max_position_pct_of_equity": 1.5},
    ],
)
def test_invalid_limits_are_rejected(kwargs):
    with pytest.raises(ValueError):
        SafetyLimits(**kwargs)


# --- adaptér Alpaca (bez sítě) ------------------------------------------


def test_alpaca_requires_credentials():
    from trading.brokers.alpaca import AlpacaBroker

    with pytest.raises(ValueError, match="api_key"):
        AlpacaBroker("", "")


def test_alpaca_paper_is_not_live():
    from trading.brokers.alpaca import PAPER_URL, AlpacaBroker

    broker = AlpacaBroker("k", "s", paper=True)
    assert not broker.is_live
    assert broker.base_url == PAPER_URL


def test_alpaca_live_flag_and_url():
    from trading.brokers.alpaca import LIVE_URL, AlpacaBroker

    broker = AlpacaBroker("k", "s", paper=False)
    assert broker.is_live
    assert broker.base_url == LIVE_URL


def test_alpaca_maps_response_to_our_types():
    """Překlad odpovědi na naše typy se dá otestovat bez sítě."""
    from trading.brokers.alpaca import AlpacaBroker

    broker = AlpacaBroker("k", "s")
    order = broker._to_order(
        {
            "id": "abc-123",
            "client_order_id": "moje-1",
            "symbol": "AAPL",
            "side": "buy",
            "qty": "10",
            "type": "limit",
            "time_in_force": "gtc",
            "limit_price": "195.50",
            "status": "partially_filled",
            "filled_qty": "4",
            "filled_avg_price": "195.40",
            "submitted_at": "2026-08-14T13:30:00Z",
        }
    )
    assert order.order_id == "abc-123"
    assert order.side is Side.BUY
    assert order.quantity == 10
    assert order.order_type is OrderType.LIMIT
    assert order.time_in_force is TimeInForce.GTC
    assert order.limit_price == pytest.approx(195.50)
    assert order.status is OrderStatus.PARTIALLY_FILLED
    assert order.filled_quantity == 4
    assert order.remaining_quantity == 6
    assert order.submitted_at is not None


def test_alpaca_maps_every_known_status():
    from trading.brokers.alpaca import _STATUS_MAP, AlpacaBroker

    broker = AlpacaBroker("k", "s")
    for raw_status in _STATUS_MAP:
        order = broker._to_order(
            {"symbol": "X", "side": "sell", "qty": "1", "type": "market", "status": raw_status}
        )
        assert isinstance(order.status, OrderStatus)
