from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from trading import metrics
from trading.models import BacktestResult, EquityPoint, Trade


def curve(values, start="2024-01-01"):
    dates = pd.bdate_range(start, periods=len(values))
    return [
        EquityPoint(ts, cash=v, positions_value=0.0)
        for ts, v in zip(dates, values, strict=True)
    ]


def trade(pnl, fees=0.0, days=5, symbol="X"):
    entry = datetime(2024, 1, 2)
    quantity = 10.0
    entry_price = 100.0
    # exit_price dopočítáme tak, aby net_pnl vyšlo přesně na zadanou hodnotu
    exit_price = entry_price + (pnl + fees) / quantity
    return Trade(
        symbol, quantity, entry, entry_price, entry + timedelta(days=days), exit_price, fees
    )


# --- drawdown -----------------------------------------------------------


def test_max_drawdown_of_rising_curve_is_zero():
    equity = metrics.equity_series(curve([100, 110, 120, 130]))
    dd, _ = metrics.max_drawdown(equity)
    assert dd == pytest.approx(0.0)


def test_max_drawdown_measures_peak_to_trough():
    equity = metrics.equity_series(curve([100, 200, 100, 150]))
    dd, _ = metrics.max_drawdown(equity)
    assert dd == pytest.approx(0.5)


def test_drawdown_duration_counts_days_under_water():
    equity = metrics.equity_series(curve([100, 90, 90, 90, 110]))
    _, days = metrics.max_drawdown(equity)
    assert days >= 3


# --- výnos a poměry -----------------------------------------------------


def test_cagr_doubles_over_one_year():
    dates = pd.DatetimeIndex(["2024-01-01", "2025-01-01"])
    equity = pd.Series([100.0, 200.0], index=dates)
    assert metrics.cagr(equity) == pytest.approx(1.0, rel=0.01)


def test_sharpe_of_constant_equity_is_zero():
    equity = metrics.equity_series(curve([100] * 50))
    assert metrics.sharpe_ratio(equity) == 0.0


def test_sharpe_is_positive_for_steady_growth():
    equity = metrics.equity_series(curve([100 * 1.001**i for i in range(200)]))
    assert metrics.sharpe_ratio(equity) > 1.0


def test_risk_free_rate_lowers_sharpe():
    equity = metrics.equity_series(curve([100 * 1.001**i for i in range(200)]))
    assert metrics.sharpe_ratio(equity, 0.05) < metrics.sharpe_ratio(equity, 0.0)


def test_sortino_ignores_upside_volatility():
    """Řada, která jen roste (různě rychle), nemá záporné odchylky."""
    equity = metrics.equity_series(curve([100 * 1.002**i for i in range(100)]))
    assert metrics.sortino_ratio(equity) == float("inf")


# --- statistiky obchodů -------------------------------------------------


def test_trade_stats_on_empty_list():
    stats = metrics.trade_stats([])
    assert stats["num_trades"] == 0
    assert stats["profit_factor"] == 0.0


def test_win_rate_and_profit_factor():
    trades = [trade(100), trade(100), trade(-50), trade(-50)]
    stats = metrics.trade_stats(trades)
    assert stats["num_trades"] == 4
    assert stats["win_rate"] == pytest.approx(50.0)
    assert stats["profit_factor"] == pytest.approx(2.0)
    assert stats["expectancy"] == pytest.approx(25.0)


def test_payoff_ratio_compares_average_win_to_average_loss():
    stats = metrics.trade_stats([trade(300), trade(-100)])
    assert stats["payoff_ratio"] == pytest.approx(3.0)


def test_fees_reduce_net_pnl():
    stats = metrics.trade_stats([trade(100, fees=30)])
    assert stats["expectancy"] == pytest.approx(100.0)
    assert metrics.trade_stats([trade(0, fees=30)])["expectancy"] == pytest.approx(0.0)


def test_profit_factor_below_one_means_losing_system():
    stats = metrics.trade_stats([trade(50), trade(-100)])
    assert stats["profit_factor"] < 1.0


# --- kompletní report ---------------------------------------------------


def test_analyze_produces_consistent_report():
    result = BacktestResult(
        strategy="test",
        symbols=["X"],
        initial_capital=100_000.0,
        trades=[trade(1_000), trade(-500)],
        equity_curve=curve([100_000, 101_000, 100_500]),
    )
    report = metrics.analyze(result, total_fees=12.0)

    assert report.final_equity == pytest.approx(100_500)
    assert report.total_return_pct == pytest.approx(0.5)
    assert report.num_trades == 2
    assert report.win_rate_pct == pytest.approx(50.0)
    assert report.total_fees == 12.0


def test_report_warns_about_tiny_sample():
    result = BacktestResult("t", ["X"], 100_000.0, [trade(100)], curve([100_000, 100_100]))
    report = metrics.analyze(result)
    assert any("bezcenný vzorek" in w for w in report.warnings)


def test_report_warns_about_deep_drawdown():
    result = BacktestResult(
        "t", ["X"], 100_000.0, [trade(1) for _ in range(40)], curve([100_000, 50_000, 60_000])
    )
    report = metrics.analyze(result)
    assert any("drawdown" in w for w in report.warnings)


def test_report_formats_without_error():
    result = BacktestResult("t", ["X"], 100_000.0, [trade(100)], curve([100_000, 100_100]))
    text = metrics.analyze(result).format()
    assert "Sharpe" in text and "drawdown" in text.lower()


def test_analyze_handles_empty_equity_curve():
    report = metrics.analyze(BacktestResult("t", ["X"], 100_000.0, [], []))
    assert report.final_equity == 100_000.0
    assert report.max_drawdown_pct == 0.0


def test_annualized_volatility_matches_formula():
    rng = np.random.default_rng(0)
    prices = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 1000))))
    from trading.indicators import annualized_volatility

    assert annualized_volatility(prices) == pytest.approx(0.01 * np.sqrt(252), rel=0.1)
