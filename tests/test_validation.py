"""Testy walk-forward validace.

Nejdůležitější vlastnost: testovací okno nesmí ovlivnit volbu parametrů.
Kdyby ano, walk-forward by měřil totéž co obyčejný backtest a celý smysl
by se ztratil.
"""

import pandas as pd
import pytest

from trading import data as data_module
from trading import strategies
from trading.backtest import BacktestConfig
from trading.risk import RiskConfig
from trading.validation import (
    configuration_returns,
    parameter_grid,
    walk_forward,
    walk_forward_splits,
)


def config():
    return BacktestConfig(
        risk=RiskConfig(
            risk_per_trade=0.02,
            max_position_pct=1.0,
            max_daily_loss_pct=None,
            max_drawdown_pct=None,
        )
    )


def factory(name="sma_crossover"):
    return lambda **params: strategies.create(name, **params)


@pytest.fixture(scope="module")
def history():
    return {"X": data_module.synthetic("X", days=2000, seed=4)}


# --- mřížka -------------------------------------------------------------


def test_parameter_grid_is_the_cartesian_product():
    grid = parameter_grid(fast=[5, 10], slow=[30, 50])
    assert len(grid) == 4
    assert {"fast": 5, "slow": 30} in grid
    assert {"fast": 10, "slow": 50} in grid


def test_empty_grid_yields_one_default_configuration():
    assert parameter_grid() == [{}]


def test_single_parameter_grid():
    assert parameter_grid(fast=[5, 10, 20]) == [{"fast": 5}, {"fast": 10}, {"fast": 20}]


# --- rozdělení v čase ---------------------------------------------------


def test_splits_never_overlap():
    for split in walk_forward_splits(2000, 500, 250, embargo=10):
        assert split.train_end <= split.test_start
        assert split.train_start < split.train_end
        assert split.test_start < split.test_end


def test_embargo_creates_a_gap():
    for split in walk_forward_splits(2000, 500, 250, embargo=25):
        assert split.test_start - split.train_end == 25


def test_anchored_windows_keep_the_start():
    splits = walk_forward_splits(2000, 500, 250, anchored=True)
    assert all(s.train_start == 0 for s in splits)
    assert splits[-1].train_end > splits[0].train_end


def test_rolling_windows_move_the_start():
    splits = walk_forward_splits(2000, 500, 250, anchored=False)
    assert splits[-1].train_start > splits[0].train_start
    assert all(s.train_end - s.train_start <= 500 for s in splits)


def test_test_windows_tile_the_timeline_without_gaps():
    splits = walk_forward_splits(2000, 500, 250, embargo=0)
    for previous, current in zip(splits, splits[1:], strict=False):
        assert current.test_start == previous.test_end


def test_splits_reject_impossible_geometry():
    with pytest.raises(ValueError, match="málo dat"):
        walk_forward_splits(100, 500, 250)
    with pytest.raises(ValueError):
        walk_forward_splits(2000, 1, 250)


def test_splits_cover_a_reasonable_share_of_the_data():
    splits = walk_forward_splits(2520, 756, 252, embargo=5)
    covered = sum(s.test_end - s.test_start for s in splits)
    assert covered > 1500


# --- walk-forward -------------------------------------------------------


def test_walk_forward_produces_out_of_sample_returns(history):
    result = walk_forward(
        factory(), history, parameter_grid(fast=[10, 20], slow=[50, 100]), config(),
        train_size=756, test_size=252,
    )
    assert not result.oos_returns.empty
    assert len(result.oos_sharpes) == len(result.chosen_params)
    assert result.n_trials == 4 * len(result.oos_sharpes)


def test_out_of_sample_returns_start_after_the_first_training_window(history):
    """Do OOS křivky nesmí spadnout nic z prvního tréninkového okna."""
    train_size = 756
    result = walk_forward(
        factory(), history, parameter_grid(fast=[10, 20]), config(),
        train_size=train_size, test_size=252, embargo=5,
    )
    first_allowed = history["X"].index[train_size]
    assert result.oos_returns.index.min() >= first_allowed


def test_out_of_sample_returns_are_chronological(history):
    result = walk_forward(
        factory(), history, parameter_grid(fast=[10, 20]), config(),
        train_size=756, test_size=252,
    )
    assert result.oos_returns.index.is_monotonic_increasing
    assert not result.oos_returns.index.has_duplicates


def test_future_data_does_not_change_earlier_windows(history):
    """Prodloužení historie nesmí změnit parametry vybrané v dřívějších oknech."""
    short = {"X": history["X"].iloc[:1500]}
    grid = parameter_grid(fast=[10, 20], slow=[50, 100])

    short_result = walk_forward(factory(), short, grid, config(), train_size=756, test_size=252)
    long_result = walk_forward(factory(), history, grid, config(), train_size=756, test_size=252)

    common = len(short_result.chosen_params)
    assert long_result.chosen_params[:common] == short_result.chosen_params


def test_walk_forward_is_deterministic(history):
    grid = parameter_grid(fast=[10, 20])
    runs = [
        walk_forward(factory(), history, grid, config(), train_size=756, test_size=252)
        for _ in range(2)
    ]
    assert runs[0].chosen_params == runs[1].chosen_params
    pd.testing.assert_series_equal(runs[0].oos_returns, runs[1].oos_returns)


def test_parameter_stability_detects_a_constant_choice(history):
    """Jediná konfigurace v mřížce znamená stabilitu 100 %."""
    result = walk_forward(
        factory(), history, parameter_grid(fast=[10], slow=[50]), config(),
        train_size=756, test_size=252,
    )
    assert result.parameter_stability == pytest.approx(1.0)


def test_degradation_compares_in_and_out_of_sample(history):
    result = walk_forward(
        factory(), history, parameter_grid(fast=[5, 10, 20], slow=[50, 100]), config(),
        train_size=756, test_size=252,
    )
    import numpy as np

    expected = float(np.mean(result.is_sharpes) - np.mean(result.oos_sharpes))
    assert result.degradation == pytest.approx(expected)


def test_oos_equity_starts_near_one(history):
    result = walk_forward(
        factory(), history, parameter_grid(fast=[10]), config(), train_size=756, test_size=252
    )
    assert 0.5 < result.oos_equity.iloc[0] < 1.5


def test_walk_forward_rejects_empty_grid(history):
    with pytest.raises(ValueError, match="prázdná mřížka"):
        walk_forward(factory(), history, [], config())


def test_walk_forward_rejects_too_short_history():
    tiny = {"X": data_module.synthetic("X", days=300, seed=1)}
    with pytest.raises(ValueError):
        walk_forward(factory(), tiny, parameter_grid(fast=[10]), config(), train_size=756)


def test_broken_configurations_are_skipped(history):
    """Neplatná kombinace parametrů nesmí shodit celý běh."""
    grid = [{"fast": 10, "slow": 50}, {"fast": 100, "slow": 20}]  # druhá je neplatná
    result = walk_forward(factory(), history, grid, config(), train_size=756, test_size=252)
    assert all(params["fast"] < params["slow"] for params in result.chosen_params)


# --- matice pro PBO -----------------------------------------------------


def test_configuration_returns_has_one_column_per_configuration(history):
    grid = parameter_grid(fast=[5, 10, 20], slow=[50, 100])
    matrix = configuration_returns(factory(), history, grid, config())
    assert matrix.shape[1] == len(grid)
    assert matrix.index.is_monotonic_increasing


def test_configuration_returns_labels_columns_readably(history):
    matrix = configuration_returns(
        factory(), history, parameter_grid(fast=[5, 10], slow=[50]), config()
    )
    assert "fast=5, slow=50" in matrix.columns


def test_configuration_returns_needs_two_survivors(history):
    with pytest.raises(ValueError, match="méně než 2"):
        configuration_returns(factory(), history, parameter_grid(fast=[10], slow=[50]), config())


def test_configuration_returns_feeds_pbo(history):
    from trading import stats

    matrix = configuration_returns(
        factory(), history, parameter_grid(fast=[5, 10, 20], slow=[50, 100, 150]), config()
    )
    result = stats.probability_of_backtest_overfitting(matrix, n_splits=8)
    assert 0.0 <= result.pbo <= 1.0
    assert result.n_configurations == matrix.shape[1]
