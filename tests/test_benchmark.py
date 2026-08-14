import pytest

from trading import data as data_module
from trading.benchmark import buy_and_hold
from trading.execution import ZERO_COST, CostModel
from trading.metrics import analyze


def test_benchmark_tracks_underlying_return():
    df = data_module.synthetic("X", days=400, seed=4)
    result, _ = buy_and_hold({"X": df}, 100_000.0, ZERO_COST)
    report = analyze(result)

    underlying = (df["close"].iloc[-1] / df["open"].iloc[0] - 1.0) * 100.0
    assert report.total_return_pct == pytest.approx(underlying, abs=0.5)


def test_benchmark_holds_a_single_trade_per_symbol():
    data = {s: data_module.synthetic(s, days=300, seed=i) for i, s in enumerate("ABC")}
    result, _ = buy_and_hold(data, 100_000.0, ZERO_COST)
    assert len(result.trades) == 3
    assert all(t.exit_reason == "konec testovaného období" for t in result.trades)


def test_benchmark_splits_capital_evenly():
    data = {s: data_module.synthetic(s, days=300, seed=i) for i, s in enumerate("AB")}
    result, _ = buy_and_hold(data, 100_000.0, ZERO_COST)
    costs = [t.quantity * t.entry_price for t in result.trades]
    assert costs[0] == pytest.approx(costs[1], rel=0.01)
    assert sum(costs) == pytest.approx(100_000.0, rel=0.01)


def test_costs_reduce_benchmark_return():
    df = data_module.synthetic("X", days=300, seed=4)
    free, _ = buy_and_hold({"X": df}, 100_000.0, ZERO_COST)
    paid, fees = buy_and_hold({"X": df}, 100_000.0, CostModel(commission_pct=0.01, slippage_bps=50))
    assert paid.final_equity < free.final_equity
    assert fees > 0


def test_benchmark_needs_data():
    with pytest.raises(ValueError):
        buy_and_hold({})
