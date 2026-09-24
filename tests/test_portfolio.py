from datetime import datetime

import pytest

from trading.portfolio import InsufficientFunds, Portfolio

T0 = datetime(2024, 1, 2)
T1 = datetime(2024, 1, 10)


def test_buy_reduces_cash_by_cost_plus_fees():
    p = Portfolio(10_000.0)
    p.buy("AAPL", 10, 100.0, fees=5.0, timestamp=T0)
    assert p.cash == pytest.approx(10_000 - 1_000 - 5)
    assert p.has_position("AAPL")


def test_buy_beyond_cash_is_rejected():
    p = Portfolio(1_000.0)
    with pytest.raises(InsufficientFunds):
        p.buy("AAPL", 100, 100.0, fees=0.0, timestamp=T0)
    assert p.cash == 1_000.0
    assert not p.has_position("AAPL")


def test_double_entry_into_same_symbol_is_rejected():
    p = Portfolio(10_000.0)
    p.buy("AAPL", 10, 100.0, 0.0, T0)
    with pytest.raises(ValueError):
        p.buy("AAPL", 5, 100.0, 0.0, T0)


def test_sell_without_position_is_rejected():
    p = Portfolio(10_000.0)
    with pytest.raises(ValueError):
        p.sell("AAPL", 100.0, 0.0, T0)


def test_round_trip_pnl_matches_cash_change():
    """Součet net_pnl obchodů musí přesně odpovídat změně hotovosti."""
    p = Portfolio(10_000.0)
    p.buy("AAPL", 10, 100.0, fees=5.0, timestamp=T0)
    p.sell("AAPL", 110.0, fees=5.0, timestamp=T1)

    trade = p.trades[0]
    assert trade.gross_pnl == pytest.approx(100.0)
    assert trade.net_pnl == pytest.approx(90.0)
    assert p.cash == pytest.approx(10_000 + trade.net_pnl)
    assert p.total_fees == pytest.approx(10.0)


def test_trade_counts_fees_from_both_legs():
    p = Portfolio(10_000.0)
    p.buy("AAPL", 10, 100.0, fees=3.0, timestamp=T0)
    p.sell("AAPL", 100.0, fees=4.0, timestamp=T1)
    assert p.trades[0].fees == pytest.approx(7.0)
    assert p.trades[0].net_pnl == pytest.approx(-7.0)


def test_losing_trade_is_not_a_win():
    p = Portfolio(10_000.0)
    p.buy("AAPL", 10, 100.0, 0.0, T0)
    p.sell("AAPL", 90.0, 0.0, T1)
    assert not p.trades[0].is_win
    assert p.trades[0].return_pct == pytest.approx(-10.0)


def test_equity_tracks_market_prices():
    p = Portfolio(10_000.0)
    p.buy("AAPL", 10, 100.0, 0.0, T0)
    assert p.equity({"AAPL": 100.0}) == pytest.approx(10_000)
    assert p.equity({"AAPL": 120.0}) == pytest.approx(10_200)


def test_equity_falls_back_to_entry_price_when_quote_missing():
    p = Portfolio(10_000.0)
    p.buy("AAPL", 10, 100.0, 0.0, T0)
    assert p.equity({}) == pytest.approx(10_000)


def test_close_all_realizes_open_positions():
    p = Portfolio(10_000.0)
    p.buy("AAPL", 10, 100.0, 0.0, T0)
    p.buy("MSFT", 5, 200.0, 0.0, T0)

    closed = p.close_all(T1, {"AAPL": 90.0, "MSFT": 210.0}, lambda q, price: 1.0)

    assert len(closed) == 2
    assert not p.positions
    # -100 na AAPL, +50 na MSFT, 2 poplatky po 1 => -52
    assert sum(t.net_pnl for t in closed) == pytest.approx(-52.0)


def test_mark_to_market_appends_equity_point():
    p = Portfolio(10_000.0)
    p.buy("AAPL", 10, 100.0, 0.0, T0)
    point = p.mark_to_market(T1, {"AAPL": 105.0})
    assert point.equity == pytest.approx(10_050)
    assert len(p.equity_curve) == 1
