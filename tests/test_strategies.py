import pandas as pd
import pytest

from trading import data as data_module
from trading import strategies
from trading.models import Position, SignalType
from trading.strategies import BarContext


def frame(closes, highs=None, lows=None):
    n = len(closes)
    return pd.DataFrame(
        {
            "open": closes,
            "high": highs or [c + 1 for c in closes],
            "low": lows or [c - 1 for c in closes],
            "close": closes,
            "volume": [1e6] * n,
        },
        index=pd.bdate_range("2024-01-01", periods=n),
    )


def signal_at(strategy, df, position=None, offset=-1):
    prepared = strategy.prepare(df)
    timestamp = prepared.index[offset]
    row = prepared.loc[timestamp]
    return strategy.on_bar(BarContext("X", timestamp, row, position))


def open_position(entry=100.0):
    return Position("X", 10.0, entry, pd.Timestamp("2024-01-01"))


# --- registr ------------------------------------------------------------


def test_registry_creates_every_strategy():
    for name in strategies.REGISTRY:
        assert strategies.create(name).name == name


def test_unknown_strategy_raises_with_hint():
    with pytest.raises(ValueError, match="neznámá strategie"):
        strategies.create("neexistuje")


def test_strategy_params_are_passed_through():
    strategy = strategies.create("sma_crossover", fast=5, slow=10)
    assert strategy.fast == 5 and strategy.slow == 10


# --- SMA crossover ------------------------------------------------------


def test_sma_fast_must_be_shorter_than_slow():
    with pytest.raises(ValueError):
        strategies.SmaCrossover(fast=50, slow=20)


def test_sma_enters_only_on_the_crossing_bar():
    # Dlouhý pokles, pak obrat vzhůru -> rychlý průměr protne pomalý.
    closes = [100 - i for i in range(40)] + [60 + 3 * i for i in range(30)]
    strategy = strategies.SmaCrossover(fast=5, slow=20)
    prepared = strategy.prepare(frame(closes))

    crossings = [
        ts for ts in prepared.index if prepared.at[ts, "sma_cross_up"]
    ]
    assert len(crossings) == 1

    on_cross = strategy.on_bar(
        BarContext("X", crossings[0], prepared.loc[crossings[0]], None)
    )
    assert on_cross.type is SignalType.ENTER_LONG

    later = prepared.index[-1]
    after = strategy.on_bar(BarContext("X", later, prepared.loc[later], None))
    assert after.type is SignalType.HOLD


def test_sma_state_mode_enters_whenever_trend_is_up():
    closes = [100 - i for i in range(40)] + [60 + 3 * i for i in range(30)]
    strategy = strategies.SmaCrossover(fast=5, slow=20, enter_on_cross=False)
    assert signal_at(strategy, frame(closes)).type is SignalType.ENTER_LONG


def test_sma_exits_when_trend_turns_down():
    closes = [100 + 3 * i for i in range(30)] + [190 - 4 * i for i in range(40)]
    strategy = strategies.SmaCrossover(fast=5, slow=20)
    assert signal_at(strategy, frame(closes), open_position()).type is SignalType.EXIT_LONG


def test_sma_holds_during_warmup():
    strategy = strategies.SmaCrossover(fast=5, slow=20)
    assert signal_at(strategy, frame([100.0] * 10)).type is SignalType.HOLD


# --- RSI mean reversion -------------------------------------------------


def test_rsi_enters_when_oversold_in_uptrend():
    # Dlouhý růst kvůli filtru trendu, pak krátký prudký propad.
    closes = [100 + i for i in range(260)] + [360 - 12 * i for i in range(8)]
    strategy = strategies.RsiMeanReversion(period=14, oversold=35.0, trend_filter=200)
    assert signal_at(strategy, frame(closes)).type is SignalType.ENTER_LONG


def test_rsi_trend_filter_blocks_entry_in_downtrend():
    closes = [400 - i for i in range(260)] + [140 - 12 * i for i in range(8)]
    strategy = strategies.RsiMeanReversion(period=14, oversold=35.0, trend_filter=200)
    assert signal_at(strategy, frame(closes)).type is SignalType.HOLD


def test_rsi_without_trend_filter_buys_the_dip_anyway():
    closes = [400 - i for i in range(260)] + [140 - 12 * i for i in range(8)]
    strategy = strategies.RsiMeanReversion(period=14, oversold=35.0, trend_filter=None)
    assert signal_at(strategy, frame(closes)).type is SignalType.ENTER_LONG


def test_rsi_exits_after_recovery():
    closes = [100 - i for i in range(40)] + [60 + 5 * i for i in range(20)]
    strategy = strategies.RsiMeanReversion(period=14, exit_level=55.0, trend_filter=None)
    assert signal_at(strategy, frame(closes), open_position()).type is SignalType.EXIT_LONG


# --- Donchian breakout --------------------------------------------------


def test_donchian_enters_on_new_high():
    closes = [100.0] * 30 + [130.0]
    strategy = strategies.DonchianBreakout(entry_window=20, exit_window=10)
    assert signal_at(strategy, frame(closes)).type is SignalType.ENTER_LONG


def test_donchian_does_not_enter_inside_the_channel():
    closes = [100.0] * 30 + [100.5]
    strategy = strategies.DonchianBreakout(entry_window=20, exit_window=10)
    assert signal_at(strategy, frame(closes)).type is SignalType.HOLD


def test_donchian_exits_on_new_low():
    closes = [100.0] * 30 + [70.0]
    strategy = strategies.DonchianBreakout(entry_window=20, exit_window=10)
    assert signal_at(strategy, frame(closes), open_position()).type is SignalType.EXIT_LONG


def test_donchian_channel_excludes_current_bar():
    """Hranice kanálu se počítá z minulosti — dnešek ji nesmí sám tvořit."""
    strategy = strategies.DonchianBreakout(entry_window=5, exit_window=3)
    prepared = strategy.prepare(frame([100.0] * 10 + [200.0]))
    assert prepared["channel_high"].iloc[-1] == pytest.approx(101.0)


# --- společné vlastnosti ------------------------------------------------


@pytest.mark.parametrize("name", sorted(strategies.REGISTRY))
def test_prepare_does_not_mutate_input(name):
    df = data_module.synthetic("X", days=300)
    before = df.copy()
    strategies.create(name).prepare(df)
    pd.testing.assert_frame_equal(df, before)


@pytest.mark.parametrize("name", sorted(strategies.REGISTRY))
def test_prepare_keeps_row_count_and_index(name):
    df = data_module.synthetic("X", days=300)
    prepared = strategies.create(name).prepare(df)
    assert len(prepared) == len(df)
    pd.testing.assert_index_equal(prepared.index, df.index)


@pytest.mark.parametrize("name", sorted(strategies.REGISTRY))
def test_indicators_do_not_depend_on_future_bars(name):
    """Zkrácení dat nesmí změnit indikátory na barech, které zůstaly."""
    df = data_module.synthetic("X", days=400, seed=9)
    strategy = strategies.create(name)
    full = strategy.prepare(df)
    partial = strategy.prepare(df.iloc[:300])

    for column in partial.columns:
        pd.testing.assert_series_equal(
            full[column].iloc[:300], partial[column], check_names=False
        )


@pytest.mark.parametrize("name", sorted(strategies.REGISTRY))
def test_no_entry_signal_while_holding(name):
    """Strategie nikdy nenavrhne vstup do už otevřené pozice."""
    df = data_module.synthetic("X", days=400, seed=7)
    strategy = strategies.create(name)
    prepared = strategy.prepare(df)
    position = open_position()

    for timestamp in prepared.index[strategy.warmup :]:
        signal = strategy.on_bar(BarContext("X", timestamp, prepared.loc[timestamp], position))
        assert signal.type is not SignalType.ENTER_LONG
