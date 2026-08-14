from datetime import datetime

import pytest

from trading.models import Position
from trading.risk import RiskConfig, RiskManager

T0 = datetime(2024, 1, 2)


def make_position(entry=100.0, stop=95.0, target=None, qty=10.0):
    return Position("AAPL", qty, entry, T0, stop_loss=stop, take_profit=target)


# --- velikost pozice ----------------------------------------------------


def test_position_size_follows_risk_per_trade():
    """1 % ze 100 000 = 1 000 rizika; stop 2×ATR(2.5) = 5 -> 200 kusů."""
    rm = RiskManager(RiskConfig(risk_per_trade=0.01, stop_loss_atr_mult=2.0, max_position_pct=1.0))
    d = rm.size_position(equity=100_000, cash=100_000, price=50.0, atr=2.5, open_positions=0)
    assert d.approved
    assert d.quantity == pytest.approx(200.0)
    assert d.stop_loss == pytest.approx(45.0)


def test_wider_stop_means_smaller_position():
    """Volatilnější titul dostane menší pozici při stejném riziku."""
    rm = RiskManager(RiskConfig(risk_per_trade=0.01, stop_loss_atr_mult=2.0, max_position_pct=1.0))
    calm = rm.size_position(equity=100_000, cash=100_000, price=50.0, atr=1.0, open_positions=0)
    wild = rm.size_position(equity=100_000, cash=100_000, price=50.0, atr=5.0, open_positions=0)
    assert calm.quantity > wild.quantity


def test_exposure_cap_limits_position():
    rm = RiskManager(RiskConfig(risk_per_trade=0.5, max_position_pct=0.10))
    d = rm.size_position(equity=100_000, cash=100_000, price=100.0, atr=1.0, open_positions=0)
    assert d.quantity * 100.0 <= 100_000 * 0.10 + 1e-6


def test_cash_cap_limits_position():
    rm = RiskManager(RiskConfig(risk_per_trade=0.5, max_position_pct=1.0))
    d = rm.size_position(equity=100_000, cash=1_000, price=100.0, atr=1.0, open_positions=0)
    assert d.quantity <= 10.0


def test_missing_atr_blocks_the_trade():
    """Bez míry volatility nejde umístit stop, takže se neobchoduje."""
    rm = RiskManager()
    d = rm.size_position(equity=100_000, cash=100_000, price=50.0, atr=None, open_positions=0)
    assert not d.approved
    assert "ATR" in d.rejected_reason


def test_max_open_positions_is_enforced():
    rm = RiskManager(RiskConfig(max_open_positions=3))
    d = rm.size_position(equity=100_000, cash=100_000, price=50.0, atr=1.0, open_positions=3)
    assert not d.approved
    assert "3 pozic" in d.rejected_reason


def test_whole_shares_by_default():
    # riziko 100 / stop 2.6 = 38.46 kusů -> zaokrouhleno dolů na 38
    rm = RiskManager(RiskConfig(risk_per_trade=0.01, max_position_pct=1.0))
    d = rm.size_position(equity=10_000, cash=10_000, price=33.0, atr=1.3, open_positions=0)
    assert d.quantity == 38.0


def test_fractional_shares_when_allowed():
    rm = RiskManager(RiskConfig(risk_per_trade=0.01, max_position_pct=1.0, allow_fractional=True))
    d = rm.size_position(equity=10_000, cash=10_000, price=33.0, atr=1.3, open_positions=0)
    assert d.quantity == pytest.approx(100 / 2.6)


def test_tiny_position_is_rejected():
    rm = RiskManager(RiskConfig(min_position_value=500.0, allow_fractional=True))
    d = rm.size_position(equity=1_000, cash=1_000, price=10.0, atr=5.0, open_positions=0)
    assert not d.approved


def test_signal_strength_scales_position():
    rm = RiskManager(RiskConfig(risk_per_trade=0.01, max_position_pct=1.0, allow_fractional=True))
    full = rm.size_position(equity=100_000, cash=100_000, price=50.0, atr=1.0, open_positions=0)
    half = rm.size_position(
        equity=100_000, cash=100_000, price=50.0, atr=1.0, open_positions=0, strength=0.5
    )
    assert half.quantity == pytest.approx(full.quantity / 2)


def test_take_profit_set_only_when_configured():
    without = RiskManager(RiskConfig()).size_position(
        equity=100_000, cash=100_000, price=50.0, atr=1.0, open_positions=0
    )
    assert without.take_profit is None

    with_target = RiskManager(RiskConfig(take_profit_atr_mult=3.0)).size_position(
        equity=100_000, cash=100_000, price=50.0, atr=1.0, open_positions=0
    )
    assert with_target.take_profit == pytest.approx(53.0)


# --- limity účtu --------------------------------------------------------


def test_drawdown_kill_switch_blocks_new_entries():
    rm = RiskManager(RiskConfig(max_drawdown_pct=0.20))
    rm.update_equity(100_000)
    assert rm.trading_blocked(85_000) is None
    assert rm.trading_blocked(79_000) is not None


def test_daily_loss_limit_blocks_new_entries():
    rm = RiskManager(RiskConfig(max_daily_loss_pct=0.03, max_drawdown_pct=None))
    rm.update_equity(100_000)
    rm.start_day("2024-01-02", 100_000)
    assert rm.trading_blocked(98_000) is None
    assert rm.trading_blocked(96_000) is not None


def test_new_day_resets_daily_limit():
    rm = RiskManager(RiskConfig(max_daily_loss_pct=0.03, max_drawdown_pct=None))
    rm.update_equity(100_000)
    rm.start_day("2024-01-02", 100_000)
    assert rm.trading_blocked(96_000) is not None
    rm.start_day("2024-01-03", 96_000)
    assert rm.trading_blocked(96_000) is None


def test_peak_equity_only_goes_up():
    rm = RiskManager()
    rm.update_equity(100_000)
    rm.update_equity(80_000)
    assert rm.peak_equity == 100_000
    assert rm.current_drawdown(80_000) == pytest.approx(0.20)


# --- výstupy ------------------------------------------------------------


def test_stop_hit_when_low_touches_stop():
    rm = RiskManager()
    hit = rm.check_stop_hit(make_position(stop=95.0), bar_open=99.0, bar_low=94.0, bar_high=100.0)
    assert hit == (95.0, "stop-loss")


def test_gap_down_fills_at_open_not_at_stop():
    """Nejdůležitější test poctivosti: přes noc padlo pod stop, plní se hůř."""
    rm = RiskManager()
    hit = rm.check_stop_hit(make_position(stop=95.0), bar_open=90.0, bar_low=88.0, bar_high=91.0)
    assert hit == (90.0, "stop-loss")


def test_gap_up_fills_take_profit_at_open():
    rm = RiskManager()
    position = make_position(stop=None, target=110.0)
    hit = rm.check_stop_hit(position, bar_open=115.0, bar_low=114.0, bar_high=118.0)
    assert hit == (115.0, "take-profit")


def test_no_exit_when_bar_stays_inside_range():
    rm = RiskManager()
    position = make_position(stop=95.0, target=110.0)
    assert rm.check_stop_hit(position, bar_open=100.0, bar_low=97.0, bar_high=105.0) is None


def test_stop_takes_precedence_over_target_on_wide_bar():
    """Když bar zasáhne obojí, počítáme horší variantu — nevíme, co bylo dřív."""
    rm = RiskManager()
    position = make_position(stop=95.0, target=110.0)
    hit = rm.check_stop_hit(position, bar_open=100.0, bar_low=90.0, bar_high=115.0)
    assert hit[1] == "stop-loss"


def test_trailing_stop_moves_up_only():
    rm = RiskManager(RiskConfig(trailing_stop_atr_mult=2.0))
    position = make_position(stop=95.0)

    rm.update_trailing_stop(position, close=110.0, atr=2.0)
    assert position.stop_loss == pytest.approx(106.0)

    rm.update_trailing_stop(position, close=100.0, atr=2.0)
    assert position.stop_loss == pytest.approx(106.0)


def test_trailing_stop_ignored_when_not_configured():
    rm = RiskManager(RiskConfig(trailing_stop_atr_mult=None))
    position = make_position(stop=95.0)
    rm.update_trailing_stop(position, close=110.0, atr=2.0)
    assert position.stop_loss == pytest.approx(95.0)


# --- validace konfigurace -----------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"risk_per_trade": 0.0},
        {"risk_per_trade": 1.5},
        {"stop_loss_atr_mult": 0.0},
        {"max_position_pct": 0.0},
        {"max_open_positions": 0},
    ],
)
def test_invalid_config_is_rejected(kwargs):
    with pytest.raises(ValueError):
        RiskConfig(**kwargs)
