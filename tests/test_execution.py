import pytest

from trading.execution import COST_PRESETS, CostModel
from trading.models import Side


def test_slippage_pushes_price_against_us():
    costs = CostModel(slippage_bps=10.0)  # 0,1 %
    assert costs.fill_price(Side.BUY, 100.0) == pytest.approx(100.10)
    assert costs.fill_price(Side.SELL, 100.0) == pytest.approx(99.90)


def test_zero_slippage_leaves_price_untouched():
    costs = CostModel(slippage_bps=0.0)
    assert costs.fill_price(Side.BUY, 100.0) == 100.0
    assert costs.fill_price(Side.SELL, 100.0) == 100.0


def test_percentage_commission():
    costs = CostModel(commission_pct=0.001, slippage_bps=0.0)
    assert costs.commission(10, 100.0) == pytest.approx(1.0)


def test_per_share_commission():
    costs = CostModel(commission_per_share=0.005, slippage_bps=0.0)
    assert costs.commission(200, 50.0) == pytest.approx(1.0)


def test_minimum_commission_applies():
    costs = CostModel(commission_pct=0.001, min_commission=5.0)
    assert costs.commission(1, 100.0) == pytest.approx(5.0)


def test_no_commission_on_empty_order():
    costs = CostModel(commission_pct=0.001, min_commission=5.0)
    assert costs.commission(0, 100.0) == 0.0


@pytest.mark.parametrize("name", sorted(COST_PRESETS))
def test_presets_are_usable(name):
    costs = COST_PRESETS[name]
    assert costs.commission(10, 100.0) >= 0.0
    assert costs.fill_price(Side.BUY, 100.0) >= 100.0


def test_crypto_presets_are_far_more_expensive_than_equity():
    """Krypto burzy berou procenta, ETF brokeři nulu. Řádový rozdíl."""
    equity = COST_PRESETS["default"].commission(1, 1000.0)
    for name in ("binance", "kraken", "coinbase"):
        assert COST_PRESETS[name].commission(1, 1000.0) > equity
    assert COST_PRESETS["coinbase"].commission(1, 1000.0) > COST_PRESETS["binance"].commission(
        1, 1000.0
    )


def test_default_preset_has_nonzero_slippage():
    """Výchozí model nesmí předstírat bezztrátové plnění."""
    assert COST_PRESETS["default"].slippage_bps > 0
