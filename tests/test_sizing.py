"""Testy velikosti pozice — Kellyho kritérium a cílování volatility."""

import math

import numpy as np
import pandas as pd
import pytest

from trading import sizing

# --- Kelly: kontrola proti známým hodnotám ------------------------------


def test_kelly_even_money_bet_at_sixty_percent():
    """Učebnicový případ: férová výplata 1:1 a 60 % úspěšnost dá 20 %."""
    assert sizing.kelly_discrete(0.6, 1.0, 1.0) == pytest.approx(0.20)


def test_kelly_is_zero_without_edge():
    assert sizing.kelly_discrete(0.5, 1.0, 1.0) == 0.0


def test_kelly_refuses_to_bet_on_negative_edge():
    """Při záporném edge říká Kelly 'nesázej', ne 'sázej naopak'."""
    assert sizing.kelly_discrete(0.3, 1.0, 1.0) == 0.0


def test_kelly_low_win_rate_high_payoff():
    assert sizing.kelly_discrete(0.4, 3.0, 1.0) == pytest.approx(0.20)


def test_kelly_rejects_invalid_win_rate():
    with pytest.raises(ValueError):
        sizing.kelly_discrete(1.5, 1.0, 1.0)


def test_kelly_handles_degenerate_payoffs():
    assert sizing.kelly_discrete(0.6, 0.0, 1.0) == 0.0
    assert sizing.kelly_discrete(0.6, 1.0, 0.0) == 0.0


def test_kelly_continuous_is_mu_over_variance():
    assert sizing.kelly_continuous(0.10, 0.04) == pytest.approx(2.5)
    assert sizing.kelly_continuous(0.10, 0.0) == 0.0


def test_kelly_from_sharpe():
    assert sizing.kelly_from_sharpe(1.0, 0.15) == pytest.approx(1 / 0.15)
    assert sizing.kelly_from_sharpe(1.0, 0.0) == 0.0


def test_kelly_from_returns_needs_enough_data():
    assert sizing.kelly_from_returns(pd.Series([0.01] * 5)) == 0.0


# --- růstová funkce -----------------------------------------------------


def test_half_kelly_keeps_three_quarters_of_growth():
    """Známý výsledek, kvůli kterému se používá zlomkové Kelly."""
    mu, var = 0.10, 0.04
    full = sizing.kelly_continuous(mu, var)
    best = sizing.kelly_growth_rate(full, mu, var)
    half = sizing.kelly_growth_rate(full / 2, mu, var)
    assert half / best == pytest.approx(0.75)


def test_double_kelly_gives_zero_growth():
    """Přestřelit dvojnásobně znamená přijít o celý růst."""
    mu, var = 0.10, 0.04
    full = sizing.kelly_continuous(mu, var)
    assert sizing.kelly_growth_rate(2 * full, mu, var) == pytest.approx(0.0)


def test_growth_is_maximal_at_full_kelly():
    mu, var = 0.08, 0.03
    full = sizing.kelly_continuous(mu, var)
    best = sizing.kelly_growth_rate(full, mu, var)
    for factor in (0.3, 0.7, 1.3, 1.9):
        assert sizing.kelly_growth_rate(full * factor, mu, var) < best


# --- KellySizer ---------------------------------------------------------


def _feed(sizer, wins, losses, win_r=2.0, loss_r=-1.0):
    for _ in range(wins):
        sizer.record(win_r, 1.0)
    for _ in range(losses):
        sizer.record(loss_r, 1.0)


def test_sizer_stays_neutral_until_enough_trades():
    sizer = sizing.KellySizer(sizing.KellyConfig(min_trades=30))
    _feed(sizer, 10, 5)
    assert sizer.full_kelly() is None
    assert sizer.multiplier(0.01) == 1.0


def test_sizer_computes_kelly_after_enough_trades():
    sizer = sizing.KellySizer(sizing.KellyConfig(min_trades=30, fraction=1.0))
    _feed(sizer, 20, 20)  # 50 % úspěšnost, +2R / -1R
    assert sizer.trade_count == 40
    # f* = p/a - q/b = 0.5/1 - 0.5/2 = 0.25
    assert sizer.full_kelly() == pytest.approx(0.25)


def test_fraction_scales_the_suggestion():
    # Strop se tu musí odsunout, jinak by ho plné Kelly 0.25 hned narazilo.
    config = sizing.KellyConfig(min_trades=10, fraction=0.25, max_risk_per_trade=1.0)
    quarter = sizing.KellySizer(config)
    _feed(quarter, 20, 20)
    assert quarter.full_kelly() == pytest.approx(0.25)
    assert quarter.suggested_risk_per_trade() == pytest.approx(0.0625)


def test_absolute_cap_limits_the_suggestion():
    """Kelly na malém vzorku umí vyjít absurdně vysoko — strop to utne."""
    sizer = sizing.KellySizer(
        sizing.KellyConfig(min_trades=10, fraction=1.0, max_risk_per_trade=0.05)
    )
    _feed(sizer, 38, 2, win_r=5.0)
    assert sizer.suggested_risk_per_trade() == pytest.approx(0.05)


def test_multiplier_shrinks_for_losing_strategy():
    sizer = sizing.KellySizer(sizing.KellyConfig(min_trades=10, fraction=0.5))
    _feed(sizer, 5, 35)
    assert sizer.full_kelly() == 0.0
    assert sizer.multiplier(0.01) == sizer.config.min_multiplier


def test_multiplier_is_bounded():
    sizer = sizing.KellySizer(
        sizing.KellyConfig(min_trades=10, fraction=1.0, max_multiplier=2.0)
    )
    _feed(sizer, 39, 1, win_r=10.0)
    assert sizer.multiplier(0.001) == pytest.approx(2.0)


def test_sizer_ignores_nonsense_records():
    sizer = sizing.KellySizer()
    sizer.record(1.0, 0.0)
    sizer.record(float("nan"), 1.0)
    assert sizer.trade_count == 0


@pytest.mark.parametrize("kwargs", [{"fraction": 0.0}, {"fraction": 1.5}, {"max_multiplier": 0.1}])
def test_invalid_kelly_config(kwargs):
    with pytest.raises(ValueError):
        sizing.KellyConfig(**kwargs)


# --- odhad volatility ---------------------------------------------------


def _returns(n=1000, sigma=0.01, seed=0):
    return pd.Series(np.random.default_rng(seed).normal(0.0, sigma, n))


def test_realized_volatility_recovers_the_input():
    r = _returns(2000, sigma=0.01)
    realized = sizing.realized_volatility(r, 250).dropna().mean()
    assert realized == pytest.approx(0.01 * math.sqrt(252), rel=0.15)


def test_ewma_volatility_recovers_the_input():
    r = _returns(3000, sigma=0.01)
    assert sizing.ewma_volatility(r).dropna().mean() == pytest.approx(
        0.01 * math.sqrt(252), rel=0.15
    )


def test_ewma_reacts_faster_than_a_window():
    """Po skoku volatility musí EWMA dohnat novou úroveň dřív než okno."""
    calm = np.full(300, 0.002)
    wild = np.full(60, 0.03)
    r = pd.Series(np.concatenate([calm, wild]))

    ewma = sizing.ewma_volatility(r, lambda_=0.94).iloc[310]
    window = sizing.realized_volatility(r, 60).iloc[310]
    assert ewma > window


def test_ewma_rejects_bad_lambda():
    with pytest.raises(ValueError):
        sizing.ewma_volatility(_returns(100), lambda_=1.5)


# --- cílování volatility ------------------------------------------------


def test_targeter_is_neutral_during_warmup():
    targeter = sizing.VolatilityTargeter(sizing.VolTargetConfig(min_observations=50))
    for value in _returns(20, sigma=0.05):
        targeter.update(value)
    assert targeter.scalar == 1.0
    assert targeter.current_volatility is None


def test_targeter_scales_down_when_volatility_is_high():
    config = sizing.VolTargetConfig(target_annual_vol=0.10, min_observations=30)
    targeter = sizing.VolatilityTargeter(config)
    for value in _returns(500, sigma=0.03):
        targeter.update(value)
    assert targeter.scalar < 1.0
    assert targeter.current_volatility > 0.10


def test_targeter_scales_up_when_volatility_is_low():
    config = sizing.VolTargetConfig(target_annual_vol=0.20, min_observations=30)
    targeter = sizing.VolatilityTargeter(config)
    for value in _returns(500, sigma=0.002):
        targeter.update(value)
    assert targeter.scalar > 1.0


def test_leverage_caps_are_respected():
    config = sizing.VolTargetConfig(
        target_annual_vol=5.0, min_observations=30, max_leverage=1.5, min_leverage=0.3
    )
    targeter = sizing.VolatilityTargeter(config)
    for value in _returns(300, sigma=0.001):
        targeter.update(value)
    assert targeter.scalar <= 1.5

    config.target_annual_vol = 0.001
    targeter = sizing.VolatilityTargeter(config)
    for value in _returns(300, sigma=0.05, seed=1):
        targeter.update(value)
    assert targeter.scalar >= 0.3


def test_rebalance_band_reduces_turnover():
    """Bez pásma necitlivosti by se škálování měnilo skoro každý den."""
    r = _returns(1500, sigma=0.015, seed=3)

    def changes(band):
        series = sizing.vol_target_series(
            r, sizing.VolTargetConfig(rebalance_band=band, min_observations=30)
        )
        return int((series.diff().fillna(0) != 0).sum())

    assert changes(0.30) < changes(0.0) / 2


def test_targeting_moves_volatility_towards_the_target():
    r = _returns(3000, sigma=0.02, seed=5)
    scaled = sizing.apply_vol_target(r, sizing.VolTargetConfig(target_annual_vol=0.10))

    original = r.std(ddof=1) * math.sqrt(252)
    achieved = scaled.std(ddof=1) * math.sqrt(252)
    assert abs(achieved - 0.10) < abs(original - 0.10)


def test_targeting_reduces_tail_risk_on_clustered_volatility():
    """Chvosty se zkrátí i tam, kde se Sharpe nezlepší.

    Podle literatury je omezení extrémních výnosů ten spolehlivý přínos
    cílování; zlepšení Sharpe závisí na tom, jestli aktivum skutečně
    vykazuje zápornou vazbu mezi volatilitou a výnosem.
    """
    rng = np.random.default_rng(9)
    calm = rng.normal(0.0005, 0.006, 1500)
    storm = rng.normal(-0.001, 0.035, 400)
    r = pd.Series(np.concatenate([calm, storm, calm]))

    scaled = sizing.apply_vol_target(r, sizing.VolTargetConfig(target_annual_vol=0.12))
    assert scaled.min() > r.min()
    assert scaled.std(ddof=1) < r.std(ddof=1)


def test_targeter_ignores_non_finite_input():
    targeter = sizing.VolatilityTargeter()
    before = targeter.scalar
    assert targeter.update(float("nan")) == before


@pytest.mark.parametrize(
    "kwargs",
    [
        {"target_annual_vol": 0.0},
        {"max_leverage": 0.1, "min_leverage": 0.5},
        {"rebalance_band": 1.0},
    ],
)
def test_invalid_vol_target_config(kwargs):
    with pytest.raises(ValueError):
        sizing.VolTargetConfig(**kwargs)


def test_vol_target_series_does_not_peek_ahead():
    """Škálování pro den T smí vycházet jen z dat do T-1."""
    r = _returns(500, seed=7)
    full = sizing.vol_target_series(r)
    partial = sizing.vol_target_series(r.iloc[:300])
    pd.testing.assert_series_equal(full.iloc[:300], partial)
