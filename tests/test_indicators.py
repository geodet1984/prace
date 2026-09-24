import numpy as np
import pandas as pd
import pytest

from trading import indicators as ind


@pytest.fixture
def prices():
    return pd.Series([10.0, 11, 12, 11, 10, 9, 10, 12, 14, 13, 12, 11, 10, 11, 13, 15])


def test_sma_matches_manual_average(prices):
    result = ind.sma(prices, 3)
    assert np.isnan(result.iloc[0]) and np.isnan(result.iloc[1])
    assert result.iloc[2] == pytest.approx((10 + 11 + 12) / 3)
    assert result.iloc[-1] == pytest.approx((11 + 13 + 15) / 3)


def test_sma_never_uses_future_values(prices):
    """Zkrácení řady nesmí změnit už spočítané hodnoty."""
    full = ind.sma(prices, 3)
    partial = ind.sma(prices.iloc[:10], 3)
    pd.testing.assert_series_equal(full.iloc[:10], partial)


def test_ema_warmup_is_nan(prices):
    result = ind.ema(prices, 5)
    assert result.iloc[:4].isna().all()
    assert result.iloc[4:].notna().all()


def test_rsi_stays_in_bounds(prices):
    result = ind.rsi(prices, 5).dropna()
    assert not result.empty
    assert (result >= 0).all() and (result <= 100).all()


def test_rsi_is_100_for_monotonic_rise():
    rising = pd.Series(np.arange(1.0, 40.0))
    result = ind.rsi(rising, 14).dropna()
    assert result.iloc[-1] == pytest.approx(100.0)


def test_rsi_is_low_for_monotonic_fall():
    falling = pd.Series(np.arange(40.0, 1.0, -1.0))
    result = ind.rsi(falling, 14).dropna()
    assert result.iloc[-1] == pytest.approx(0.0, abs=1e-9)


def test_atr_is_positive_and_reflects_range():
    high = pd.Series([11.0] * 30)
    low = pd.Series([9.0] * 30)
    close = pd.Series([10.0] * 30)
    result = ind.atr(high, low, close, 14).dropna()
    assert (result > 0).all()
    assert result.iloc[-1] == pytest.approx(2.0, abs=0.01)


def test_true_range_accounts_for_overnight_gap():
    high = pd.Series([10.0, 20.0])
    low = pd.Series([9.0, 19.0])
    close = pd.Series([9.5, 19.5])
    tr = ind.true_range(high, low, close)
    # Druhý bar otevřel o 10 výš — TR musí tu mezeru zahrnout, ne jen high-low.
    assert tr.iloc[1] == pytest.approx(20.0 - 9.5)


def test_bollinger_bands_are_ordered(prices):
    lower, middle, upper = ind.bollinger(prices, 5, 2.0)
    valid = middle.notna()
    assert (lower[valid] <= middle[valid]).all()
    assert (middle[valid] <= upper[valid]).all()


def test_rolling_high_low(prices):
    assert ind.rolling_high(prices, 3).iloc[2] == 12.0
    assert ind.rolling_low(prices, 3).iloc[2] == 10.0
