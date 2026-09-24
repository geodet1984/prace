import numpy as np
import pandas as pd
import pytest

from trading import data as data_module
from trading.data import REQUIRED_COLUMNS, DataError


def test_synthetic_has_canonical_shape():
    df = data_module.synthetic("X", days=200)
    assert list(df.columns) == REQUIRED_COLUMNS
    assert len(df) == 200
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.is_monotonic_increasing


def test_synthetic_is_reproducible():
    a = data_module.synthetic("X", days=100, seed=123)
    b = data_module.synthetic("X", days=100, seed=123)
    pd.testing.assert_frame_equal(a, b)


def test_different_seeds_give_different_paths():
    a = data_module.synthetic("X", days=100, seed=1)
    b = data_module.synthetic("X", days=100, seed=2)
    assert not np.allclose(a["close"], b["close"])


def test_synthetic_bars_are_internally_consistent():
    """High musí být nejvyšší a low nejnižší — jinak by testy stopů lhaly."""
    df = data_module.synthetic("X", days=500, seed=8)
    assert (df["high"] >= df[["open", "close"]].max(axis=1) - 1e-9).all()
    assert (df["low"] <= df[["open", "close"]].min(axis=1) + 1e-9).all()
    assert (df["close"] > 0).all()


def test_csv_round_trip(tmp_path):
    original = data_module.synthetic("X", days=50)
    path = tmp_path / "X.csv"
    data_module.save_csv(original, path)

    loaded = data_module.load_csv(path, "X")
    pd.testing.assert_frame_equal(original, loaded, check_freq=False)


def test_missing_csv_raises():
    with pytest.raises(DataError, match="neexistuje"):
        data_module.load_csv("/nope/missing.csv")


def test_csv_missing_columns_raises(tmp_path):
    path = tmp_path / "bad.csv"
    pd.DataFrame({"close": [1.0, 2.0]}, index=pd.bdate_range("2024-01-01", periods=2)).to_csv(path)
    with pytest.raises(DataError, match="chybí sloupce"):
        data_module.load_csv(path, "BAD")


def test_normalize_lowercases_and_sorts(tmp_path):
    path = tmp_path / "messy.csv"
    index = pd.DatetimeIndex(["2024-01-03", "2024-01-02", "2024-01-03"])
    pd.DataFrame(
        {
            "Open": [1.0, 2, 3],
            "High": [1.0, 2, 3],
            "Low": [1.0, 2, 3],
            "Close": [1.0, 2, 3],
            "Volume": [1.0, 2, 3],
        },
        index=index,
    ).to_csv(path)

    df = data_module.load_csv(path, "M")
    assert list(df.columns) == REQUIRED_COLUMNS
    assert df.index.is_monotonic_increasing
    assert not df.index.has_duplicates


def test_adjusted_close_rescales_the_whole_bar(tmp_path):
    """Split se musí promítnout do všech cen, ne jen do close."""
    path = tmp_path / "split.csv"
    pd.DataFrame(
        {
            "Open": [100.0, 100.0],
            "High": [110.0, 110.0],
            "Low": [90.0, 90.0],
            "Close": [100.0, 100.0],
            "Adj Close": [50.0, 100.0],
            "Volume": [1.0, 1.0],
        },
        index=pd.bdate_range("2024-01-01", periods=2),
    ).to_csv(path)

    df = data_module.load_csv(path, "S")
    assert df["close"].iloc[0] == pytest.approx(50.0)
    assert df["open"].iloc[0] == pytest.approx(50.0)
    assert df["high"].iloc[0] == pytest.approx(55.0)
    assert df["close"].iloc[1] == pytest.approx(100.0)


def test_rows_without_prices_are_dropped(tmp_path):
    path = tmp_path / "holes.csv"
    pd.DataFrame(
        {
            "open": [100.0, None, 102.0],
            "high": [101.0, None, 103.0],
            "low": [99.0, None, 101.0],
            "close": [100.5, None, 102.5],
            "volume": [1.0, 1.0, 1.0],
        },
        index=pd.bdate_range("2024-01-01", periods=3),
    ).to_csv(path)

    df = data_module.load_csv(path, "H")
    assert len(df) == 2


def test_all_empty_csv_raises(tmp_path):
    path = tmp_path / "empty.csv"
    pd.DataFrame(
        {c: [None] for c in REQUIRED_COLUMNS}, index=pd.bdate_range("2024-01-01", periods=1)
    ).to_csv(path)
    with pytest.raises(DataError):
        data_module.load_csv(path, "E")
