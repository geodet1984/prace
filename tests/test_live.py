"""Testy paper tradingu — hlavně to, že stav přežije restart beze změny."""

import json

import pytest

from trading import data as data_module
from trading import strategies
from trading.backtest import BacktestConfig, BacktestEngine
from trading.execution import ZERO_COST
from trading.live import PaperTrader, make_config
from trading.risk import RiskConfig


def config():
    return make_config(
        100_000.0,
        ZERO_COST,
        RiskConfig(risk_per_trade=0.02, max_position_pct=1.0, max_daily_loss_pct=None),
    )


@pytest.fixture
def history():
    return data_module.synthetic("X", days=400, seed=6)


def test_first_step_processes_history(tmp_path, history):
    trader = PaperTrader(strategies.create("sma_crossover"), config(), tmp_path / "s.json")
    trader.step({"X": history})
    assert trader.last_bar == history.index[-1]


def test_repeated_step_with_same_data_does_nothing(tmp_path, history):
    trader = PaperTrader(strategies.create("sma_crossover"), config(), tmp_path / "s.json")
    trader.step({"X": history})
    cash_after_first = trader.engine.portfolio.cash

    second = trader.step({"X": history})
    assert second == []
    assert trader.engine.portfolio.cash == cash_after_first


def test_state_survives_restart(tmp_path, history):
    path = tmp_path / "s.json"

    first = PaperTrader(strategies.create("sma_crossover"), config(), path)
    first.step({"X": history})
    first.save()

    second = PaperTrader(strategies.create("sma_crossover"), config(), path)

    assert second.engine.portfolio.cash == pytest.approx(first.engine.portfolio.cash)
    assert len(second.engine.portfolio.trades) == len(first.engine.portfolio.trades)
    assert second.last_bar == first.last_bar
    assert second.engine._pending.keys() == first.engine._pending.keys()


def test_incremental_steps_match_one_shot_backtest(tmp_path):
    """Krokovaný živý běh musí dát stejný výsledek jako běh na celé historii.

    Tohle je klíčová vlastnost: kdyby se lišily, nedalo by se z backtestu
    usuzovat vůbec nic o tom, co bude systém dělat naostro.
    """
    full = data_module.synthetic("X", days=500, seed=13)
    strategy_name, params = "sma_crossover", {"fast": 10, "slow": 30}

    path = tmp_path / "s.json"
    trader = PaperTrader(strategies.create(strategy_name, **params), config(), path)
    for cutoff in (200, 300, 400, 500):
        trader.step({"X": full.iloc[:cutoff]})
        trader.save()
        trader = PaperTrader(strategies.create(strategy_name, **params), config(), path)

    engine = BacktestEngine(
        strategies.create(strategy_name, **params),
        BacktestConfig(
            initial_capital=100_000.0,
            costs=ZERO_COST,
            risk=RiskConfig(risk_per_trade=0.02, max_position_pct=1.0, max_daily_loss_pct=None),
            close_at_end=False,
        ),
    )
    engine.run({"X": full})

    live_trades = [(t.entry_time, t.exit_time, t.quantity) for t in trader.engine.portfolio.trades]
    bt_trades = [(t.entry_time, t.exit_time, t.quantity) for t in engine.portfolio.trades]
    assert live_trades == bt_trades
    assert trader.engine.portfolio.cash == pytest.approx(engine.portfolio.cash)


def test_loading_state_of_another_strategy_is_refused(tmp_path, history):
    path = tmp_path / "s.json"
    PaperTrader(strategies.create("sma_crossover"), config(), path).save()

    with pytest.raises(ValueError, match="patří strategii"):
        PaperTrader(strategies.create("donchian_breakout"), config(), path)


def test_incompatible_state_version_is_refused(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"version": 999, "cash": 1.0}), encoding="utf-8")
    with pytest.raises(ValueError, match="verze stavu"):
        PaperTrader(strategies.create("sma_crossover"), config(), path)


def test_too_little_history_raises(tmp_path):
    trader = PaperTrader(strategies.create("sma_crossover"), config(), tmp_path / "s.json")
    with pytest.raises(ValueError, match="málo historie"):
        trader.step({"X": data_module.synthetic("X", days=20)})


def test_status_renders(tmp_path, history):
    trader = PaperTrader(strategies.create("sma_crossover"), config(), tmp_path / "s.json")
    trader.step({"X": history})
    text = trader.status()
    assert "PAPER ÚČET" in text and "Equity" in text


def test_saved_file_is_valid_json(tmp_path, history):
    path = tmp_path / "s.json"
    trader = PaperTrader(strategies.create("sma_crossover"), config(), path)
    trader.step({"X": history})
    trader.save()

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert "trades" in payload and "positions" in payload
    assert not (tmp_path / "s.tmp").exists(), "dočasný soubor se má přejmenovat, ne zůstat"
