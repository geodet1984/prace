"""Testy statistiky, která má odhalovat falešné strategie.

Klíčová vlastnost: na čistém šumu musí všechno vyjít nepříznivě. Kdyby
nástroj na odhalování přeoptimalizování schvaloval náhodu, byl by horší
než žádný — dodával by falešnou jistotu.
"""

import math

import numpy as np
import pandas as pd
import pytest

from trading import stats


def noise(n=756, seed=0, mu=0.0, sigma=0.01):
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(mu, sigma, n))


# --- základ -------------------------------------------------------------


def test_period_and_annual_sharpe_are_consistent():
    r = noise(1000, mu=0.0005)
    period = stats.period_sharpe(r)
    annual = stats.annualize_sharpe(period)
    assert annual == pytest.approx(period * math.sqrt(252))
    assert stats.deannualize_sharpe(annual) == pytest.approx(period)


def test_sharpe_of_constant_series_is_zero():
    """Konstantní řada má nulovou volatilitu, takže Sharpe nedává smysl.

    Regrese: std z plovoucí čárky vyjde ~1e-18, ne přesně 0, a bez tolerance
    z toho podíl udělá Sharpe v řádu 10¹⁵.
    """
    assert stats.period_sharpe(pd.Series([0.01] * 100)) == 0.0
    assert stats.period_sharpe(pd.Series([0.0] * 100)) == 0.0


def test_moments_of_normal_data():
    skew, kurt = stats._moments(noise(20000, seed=5).to_numpy())
    assert skew == pytest.approx(0.0, abs=0.1)
    assert kurt == pytest.approx(3.0, abs=0.2)


# --- Probabilistic Sharpe Ratio -----------------------------------------


def test_psr_is_a_probability():
    assert 0.0 <= stats.probabilistic_sharpe_ratio(noise(500, seed=1)) <= 1.0


def test_psr_high_for_strong_track_record():
    strong = noise(2000, seed=2, mu=0.001)
    assert stats.probabilistic_sharpe_ratio(strong) > 0.95


def test_psr_near_half_for_zero_edge():
    flat = noise(4000, seed=3, mu=0.0)
    assert 0.2 < stats.probabilistic_sharpe_ratio(flat) < 0.8


def test_psr_falls_as_benchmark_rises():
    r = noise(2000, seed=4, mu=0.0006)
    assert stats.probabilistic_sharpe_ratio(r, 0.0) > stats.probabilistic_sharpe_ratio(r, 1.5)


def test_negative_skew_lowers_psr_at_equal_sharpe():
    """Dvě řady se stejným Sharpe si nezaslouží stejnou důvěru.

    Řada s vzácnými velkými ztrátami je rizikovější, i když to Sharpe neukáže.
    """
    rng = np.random.default_rng(9)
    n = 2000
    symmetric = pd.Series(rng.normal(0.0005, 0.01, n))

    # Záporně zešikmená řada: většinou drobné zisky, občas velká ztráta.
    skewed = pd.Series(rng.gamma(2.0, 0.004, n) - 0.0075)
    skewed = skewed * (symmetric.std() / skewed.std())
    skewed = skewed - skewed.mean() + symmetric.mean()
    skewed = -skewed + 2 * symmetric.mean()  # obrátíme šikmost do záporu

    assert stats._moments(skewed.to_numpy())[0] < -0.3
    assert stats.period_sharpe(skewed) == pytest.approx(stats.period_sharpe(symmetric), abs=0.02)
    assert stats.probabilistic_sharpe_ratio(skewed) < stats.probabilistic_sharpe_ratio(symmetric)


# --- očekávané maximum a deflace ----------------------------------------


def test_expected_max_sharpe_grows_with_trials():
    sigma = 0.05
    values = [stats.expected_max_sharpe(n, sigma) for n in (1, 10, 100, 1000)]
    assert values == sorted(values)
    assert values[0] == 0.0


def test_expected_max_sharpe_zero_without_dispersion():
    assert stats.expected_max_sharpe(1000, 0.0) == 0.0


def test_expected_max_sharpe_rejects_zero_trials():
    with pytest.raises(ValueError):
        stats.expected_max_sharpe(0, 0.1)


def test_deflated_sharpe_rejects_noise():
    """Nejdůležitější test celého modulu."""
    result = stats.deflated_sharpe_ratio(noise(1000, seed=11), n_trials=200)
    assert not result.is_significant
    assert result.deflated < 0.5


def test_deflated_sharpe_accepts_strong_edge_with_few_trials():
    strong = noise(2000, seed=12, mu=0.0015)
    result = stats.deflated_sharpe_ratio(strong, n_trials=5)
    assert result.is_significant


def test_more_trials_lower_the_verdict():
    r = noise(2000, seed=13, mu=0.0009)
    few = stats.deflated_sharpe_ratio(r, n_trials=2).deflated
    many = stats.deflated_sharpe_ratio(r, n_trials=5000).deflated
    assert few > many


def test_trial_sharpes_raise_the_bar():
    """Když se Sharpe napříč pokusy hodně rozptyluje, laťka musí být výš."""
    r = noise(2000, seed=14, mu=0.001)
    tight = stats.deflated_sharpe_ratio(r, n_trials=50, trial_sharpes=[0.1, 0.12, 0.09, 0.11])
    wide = stats.deflated_sharpe_ratio(r, n_trials=50, trial_sharpes=[-1.5, 0.2, 1.8, -0.4])
    assert wide.benchmark_annual > tight.benchmark_annual
    assert wide.deflated < tight.deflated


def test_deflated_sharpe_needs_data():
    with pytest.raises(ValueError):
        stats.deflated_sharpe_ratio(pd.Series([0.01, 0.02]), n_trials=10)


def test_result_formats():
    text = stats.deflated_sharpe_ratio(noise(500), n_trials=10).format()
    assert "Deflated Sharpe Ratio" in text


# --- minimální délka historie -------------------------------------------


def test_min_track_record_is_infinite_below_threshold():
    assert stats.min_track_record_length(noise(1000, seed=15), benchmark_sharpe=5.0) == float("inf")


def test_min_track_record_shrinks_with_stronger_edge():
    weak = stats.min_track_record_length(noise(3000, seed=16, mu=0.0003))
    strong = stats.min_track_record_length(noise(3000, seed=16, mu=0.0020))
    assert strong < weak


def test_min_track_record_grows_with_confidence():
    r = noise(3000, seed=17, mu=0.001)
    assert stats.min_track_record_length(r, confidence=0.99) > stats.min_track_record_length(
        r, confidence=0.90
    )


# --- PBO ----------------------------------------------------------------


def _matrix(n_rows, n_cols, seed, skilled=0):
    rng = np.random.default_rng(seed)
    frame = pd.DataFrame(rng.normal(0, 0.01, (n_rows, n_cols)))
    for i in range(skilled):
        frame[i] = rng.normal(0.0015, 0.01, n_rows)
    return frame


def test_pbo_high_for_pure_noise():
    """Výběr nejlepší z náhodných konfigurací je hod mincí — PBO kolem 0,5.

    Průměruje se přes víc datových sad schválně: PBO z jediné sady má obrovský
    rozptyl (na šumu jsem naměřil 0,19 až 0,73), protože všech C(S, S/2)
    kombinací sdílí tatáž data. Tvrdit něco z jednoho běhu by byl přesně ten
    druh závěru, proti kterému tenhle modul je.
    """
    values = [
        stats.probability_of_backtest_overfitting(
            _matrix(1000, 30, seed=seed), n_splits=8
        ).pbo
        for seed in range(10)
    ]
    assert 0.35 < float(np.mean(values)) < 0.65


def test_pbo_low_when_a_real_edge_exists():
    values = [
        stats.probability_of_backtest_overfitting(
            _matrix(1500, 30, seed=seed, skilled=1), n_splits=8
        ).pbo
        for seed in range(6)
    ]
    assert float(np.mean(values)) < 0.25


def test_pbo_requires_even_splits():
    with pytest.raises(ValueError, match="sudé"):
        stats.probability_of_backtest_overfitting(_matrix(500, 5, seed=22), n_splits=7)


def test_pbo_requires_two_configurations():
    with pytest.raises(ValueError, match="2 konfigurace"):
        stats.probability_of_backtest_overfitting(_matrix(500, 1, seed=23), n_splits=8)


def test_pbo_requires_enough_rows():
    with pytest.raises(ValueError, match="málo pozorování"):
        stats.probability_of_backtest_overfitting(_matrix(10, 5, seed=24), n_splits=8)


def test_pbo_formats():
    assert "PBO" in stats.probability_of_backtest_overfitting(
        _matrix(600, 6, seed=25), n_splits=6
    ).format()


# --- bootstrap ----------------------------------------------------------


def test_bootstrap_interval_brackets_the_estimate():
    low, point, high = stats.block_bootstrap_sharpe(noise(1500, seed=30, mu=0.0008), n_samples=400)
    assert low < point < high


def test_bootstrap_interval_contains_zero_for_noise():
    low, _, high = stats.block_bootstrap_sharpe(noise(1500, seed=31), n_samples=400)
    assert low <= 0 <= high


def test_wider_confidence_gives_wider_interval():
    r = noise(1500, seed=32, mu=0.0006)
    narrow = stats.block_bootstrap_sharpe(r, n_samples=400, confidence=0.80)
    wide = stats.block_bootstrap_sharpe(r, n_samples=400, confidence=0.99)
    assert (wide[2] - wide[0]) > (narrow[2] - narrow[0])


def test_bootstrap_is_reproducible():
    r = noise(800, seed=33, mu=0.0005)
    assert stats.block_bootstrap_sharpe(r, n_samples=200, seed=7) == stats.block_bootstrap_sharpe(
        r, n_samples=200, seed=7
    )


def test_bootstrap_needs_data():
    with pytest.raises(ValueError):
        stats.block_bootstrap_sharpe(pd.Series([0.01] * 5))


def test_equity_to_returns_skips_nonpositive():
    equity = pd.Series([100.0, 110.0, 0.0, 120.0])
    assert len(stats.equity_to_returns(equity)) == 2
