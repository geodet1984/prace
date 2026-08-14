"""Testy diverzifikace.

Ústřední tvrzení modulu je, že N nekorelovaných strategií zvedne Sharpe
√N-krát. Testy ověřují jednak to, jednak — což je v praxi důležitější —
že nástroj pozná, když složky nezávislé nejsou.
"""

import math

import numpy as np
import pandas as pd
import pytest

from trading import ensemble, strategies
from trading.models import Position, SignalType
from trading.strategies import BarContext


def independent(n=4000, count=5, sharpe=0.4, sigma=0.01, seed=0):
    """Nezávislé řady se zadaným anualizovaným Sharpe."""
    rng = np.random.default_rng(seed)
    mu = sharpe / math.sqrt(252) * sigma
    return pd.DataFrame({f"s{i}": rng.normal(mu, sigma, n) for i in range(count)})


def duplicated(n=4000, count=5, sigma=0.01, seed=1):
    """Řady, které jsou v podstatě jedna a tatáž strategie."""
    rng = np.random.default_rng(seed)
    base = rng.normal(0.4 / math.sqrt(252) * sigma, sigma, n)
    return pd.DataFrame({f"d{i}": base + rng.normal(0, sigma * 0.15, n) for i in range(count)})


# --- diagnostika --------------------------------------------------------


def test_average_correlation_of_independent_series_is_near_zero():
    corr = independent().corr()
    assert abs(ensemble.average_correlation(corr)) < 0.05


def test_average_correlation_of_duplicates_is_high():
    assert ensemble.average_correlation(duplicated().corr()) > 0.9


def test_average_correlation_of_single_column():
    assert ensemble.average_correlation(pd.DataFrame({"a": [1.0]}).corr()) == 0.0


def test_effective_bets_matches_count_for_independent_series():
    data = independent(count=4)
    weights = ensemble.equal_weights(data)
    assert ensemble.effective_number_of_bets(weights, data.cov()) > 3.0


def test_effective_bets_collapses_to_one_for_duplicates():
    data = duplicated()
    weights = ensemble.equal_weights(data)
    assert ensemble.effective_number_of_bets(weights, data.cov()) < 1.5


def test_diversification_ratio_is_one_for_a_single_asset():
    data = independent(count=1)
    weights = ensemble.equal_weights(data)
    assert ensemble.diversification_ratio(weights, data.cov()) == pytest.approx(1.0)


def test_diversification_ratio_grows_with_independence():
    indep = independent(count=5)
    dup = duplicated(count=5)
    assert ensemble.diversification_ratio(
        ensemble.equal_weights(indep), indep.cov()
    ) > ensemble.diversification_ratio(ensemble.equal_weights(dup), dup.cov())


def test_blend_sharpe_formula_gives_sqrt_n_for_zero_correlation():
    """Jádro celého modulu: nulová korelace znamená násobek √N."""
    n = 4
    identity = np.eye(n)
    assert ensemble.blend_sharpe([0.5] * n, identity) == pytest.approx(0.5 * math.sqrt(n))


def test_blend_sharpe_gives_no_gain_for_perfect_correlation():
    n = 4
    ones = np.ones((n, n))
    assert ensemble.blend_sharpe([0.5] * n, ones) == pytest.approx(0.5)


def test_blend_sharpe_edge_cases():
    assert ensemble.blend_sharpe([], np.eye(0)) == 0.0
    assert ensemble.blend_sharpe([0.7], np.eye(1)) == pytest.approx(0.7)


# --- schémata vážení ----------------------------------------------------


@pytest.mark.parametrize("scheme", sorted(ensemble.WEIGHT_SCHEMES))
def test_weights_sum_to_one_and_are_non_negative(scheme):
    weights = ensemble.WEIGHT_SCHEMES[scheme](independent())
    assert weights.sum() == pytest.approx(1.0)
    assert (weights >= 0).all()


def test_equal_weights_are_equal():
    weights = ensemble.equal_weights(independent(count=4))
    assert weights.tolist() == pytest.approx([0.25] * 4)


def test_inverse_volatility_favours_the_calmer_series():
    rng = np.random.default_rng(2)
    data = pd.DataFrame(
        {"klidna": rng.normal(0, 0.005, 2000), "divoka": rng.normal(0, 0.02, 2000)}
    )
    weights = ensemble.inverse_volatility_weights(data)
    assert weights["klidna"] > weights["divoka"]
    # Poměr vah odpovídá obrácenému poměru volatilit (0.02 / 0.005 = 4).
    assert weights["klidna"] / weights["divoka"] == pytest.approx(4.0, rel=0.1)


def test_inverse_volatility_falls_back_when_everything_is_constant():
    data = pd.DataFrame({"a": [0.0] * 100, "b": [0.0] * 100})
    assert ensemble.inverse_volatility_weights(data).tolist() == pytest.approx([0.5, 0.5])


def test_hrp_handles_a_single_column():
    weights = ensemble.hierarchical_risk_parity_weights(independent(count=1))
    assert weights.iloc[0] == pytest.approx(1.0)


def test_hrp_gives_similar_weights_to_symmetric_inputs():
    weights = ensemble.hierarchical_risk_parity_weights(independent(count=4, n=6000))
    assert weights.max() / weights.min() < 2.0


def test_hrp_splits_capital_between_clusters():
    """Dvojice korelovaných strategií nemá dostat dvojnásobek proti samotáři.

    Přesně tomuhle se HRP snaží zabránit — bez shlukování by naivní vážení
    dalo dvěma klonům dohromady dvojnásobnou váhu proti jedné nezávislé.
    """
    rng = np.random.default_rng(4)
    shared = rng.normal(0, 0.01, 4000)
    data = pd.DataFrame(
        {
            "klon_a": shared + rng.normal(0, 0.001, 4000),
            "klon_b": shared + rng.normal(0, 0.001, 4000),
            "samotar": rng.normal(0, 0.01, 4000),
        }
    )
    weights = ensemble.hierarchical_risk_parity_weights(data)
    assert weights["klon_a"] + weights["klon_b"] < 0.7
    assert weights["samotar"] > 0.3


def test_linkage_order_keeps_every_leaf():
    data = independent(count=6)
    corr = ensemble._with_unit_diagonal(data.corr())
    distance = ((1.0 - corr) / 2.0) ** 0.5
    order = ensemble._single_linkage_order(distance)
    assert sorted(order) == sorted(data.columns)


# --- blend --------------------------------------------------------------


def test_blend_of_independent_strategies_beats_every_component():
    _, report = ensemble.blend(independent(count=5, n=6000), "equal")
    assert report.sharpe_gain > 1.4
    assert report.blended_sharpe == pytest.approx(report.theoretical_max_sharpe, rel=0.1)


def test_blend_of_duplicates_gains_nothing():
    _, report = ensemble.blend(duplicated(), "equal")
    assert report.sharpe_gain < 1.15
    assert report.effective_bets < 1.5


def test_blend_returns_have_the_right_length():
    data = independent(n=500)
    blended, _ = ensemble.blend(data)
    assert len(blended) == 500


def test_blend_rejects_unknown_scheme():
    with pytest.raises(ValueError, match="neznámé schéma"):
        ensemble.blend(independent(), "kouzelne")


def test_blend_rejects_empty_input():
    with pytest.raises(ValueError):
        ensemble.blend(pd.DataFrame())


def test_report_warns_about_high_correlation():
    _, report = ensemble.blend(duplicated(), "equal")
    assert "jednu sázku vícekrát" in report.format()


def test_report_lists_all_weights():
    _, report = ensemble.blend(independent(count=3), "equal")
    text = report.format()
    assert all(name in text for name in ["s0", "s1", "s2"])


# --- hlasovací ensemble -------------------------------------------------


def frame(closes):
    n = len(closes)
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [1e6] * n,
        },
        index=pd.bdate_range("2024-01-01", periods=n),
    )


class AlwaysEnter(strategies.Strategy):
    name = "vzdy_vstup"

    def prepare(self, df):
        out = df.copy()
        out["moje"] = 1.0
        return out

    def on_bar(self, ctx):
        from trading.models import Signal

        assert ctx.row["moje"] == 1.0, "dílčí strategie musí vidět svůj vlastní sloupec"
        if not ctx.in_position:
            return Signal(SignalType.ENTER_LONG, 1.0, "vždy")
        return strategies.HOLD


class AlwaysHold(strategies.Strategy):
    name = "vzdy_drz"

    def prepare(self, df):
        out = df.copy()
        out["moje"] = 2.0
        return out

    def on_bar(self, ctx):
        assert ctx.row["moje"] == 2.0
        return strategies.HOLD


class AlwaysExit(strategies.Strategy):
    name = "vzdy_ven"

    def prepare(self, df):
        return df.copy()

    def on_bar(self, ctx):
        from trading.models import Signal

        if ctx.in_position:
            return Signal(SignalType.EXIT_LONG, 1.0, "vždy ven")
        return strategies.HOLD


def signal_from(members, position=None, **kwargs):
    combo = ensemble.VotingEnsemble(members, **kwargs)
    prepared = combo.prepare(frame([100.0] * 20))
    timestamp = prepared.index[-1]
    return combo.on_bar(BarContext("X", timestamp, prepared.loc[timestamp], position))


def test_ensemble_needs_at_least_one_strategy():
    with pytest.raises(ValueError):
        ensemble.VotingEnsemble([])


def test_majority_is_the_default_threshold():
    combo = ensemble.VotingEnsemble([AlwaysEnter(), AlwaysHold(), AlwaysEnter()])
    assert combo.min_entry_votes == 2


def test_invalid_vote_threshold_is_rejected():
    with pytest.raises(ValueError):
        ensemble.VotingEnsemble([AlwaysEnter()], min_entry_votes=5)


def test_entry_requires_enough_votes():
    assert signal_from([AlwaysEnter(), AlwaysHold(), AlwaysHold()]).type is SignalType.HOLD
    assert signal_from([AlwaysEnter(), AlwaysEnter(), AlwaysHold()]).type is SignalType.ENTER_LONG


def test_signal_strength_reflects_the_margin():
    """Vlažná shoda dá slabší signál, takže risk manager zmenší pozici."""
    weak = signal_from([AlwaysEnter(), AlwaysEnter(), AlwaysHold()])
    strong = signal_from([AlwaysEnter(), AlwaysEnter(), AlwaysEnter()])
    assert weak.strength == pytest.approx(2 / 3)
    assert strong.strength == pytest.approx(1.0)


def test_single_vote_is_enough_to_exit():
    """Dovnitř po dohodě, ven při první pochybnosti."""
    position = Position("X", 1.0, 100.0, pd.Timestamp("2024-01-01"))
    signal = signal_from([AlwaysEnter(), AlwaysEnter(), AlwaysExit()], position=position)
    assert signal.type is SignalType.EXIT_LONG


def test_exit_threshold_can_be_raised():
    position = Position("X", 1.0, 100.0, pd.Timestamp("2024-01-01"))
    signal = signal_from(
        [AlwaysEnter(), AlwaysEnter(), AlwaysExit()], position=position, min_exit_votes=2
    )
    assert signal.type is SignalType.HOLD


def test_member_columns_do_not_collide():
    """Dvě strategie se stejným názvem sloupce si nesmí přepsat data.

    Aserce uvnitř ``AlwaysEnter`` a ``AlwaysHold`` selže, kdyby některá
    dostala cizí hodnotu.
    """
    combo = ensemble.VotingEnsemble([AlwaysEnter(), AlwaysHold()])
    prepared = combo.prepare(frame([100.0] * 20))
    assert "s0__moje" in prepared.columns
    assert "s1__moje" in prepared.columns
    timestamp = prepared.index[-1]
    combo.on_bar(BarContext("X", timestamp, prepared.loc[timestamp], None))


def test_ensemble_warmup_is_the_slowest_member():
    combo = ensemble.VotingEnsemble(
        [strategies.create("sma_crossover", fast=5, slow=20), strategies.create("tsmom")]
    )
    assert combo.warmup == strategies.create("tsmom").warmup


def test_ensemble_runs_through_the_engine():
    from trading import data as data_module
    from trading.backtest import BacktestConfig, BacktestEngine

    combo = ensemble.VotingEnsemble(
        [
            strategies.create("sma_crossover", fast=10, slow=30),
            strategies.create("donchian_breakout", entry_window=15, exit_window=7),
        ],
        min_entry_votes=1,
    )
    engine = BacktestEngine(combo, BacktestConfig())
    result = engine.run({"X": data_module.synthetic("X", days=900, seed=3)})
    assert result.equity_curve
    assert "ensemble" in result.strategy
