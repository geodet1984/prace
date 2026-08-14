"""Testy paper tradingu — hlavně to, že stav přežije restart beze změny."""

import json

import pytest

from trading import data as data_module
from trading import strategies
from trading.backtest import BacktestConfig, BacktestEngine
from trading.execution import ZERO_COST, CostModel
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


# --- akumulovaný stav musí přežít restart -------------------------------


def _multi_asset(days=1200):
    return {
        s: data_module.synthetic(s, days=days, seed=i + 4)
        for i, s in enumerate(["AAA", "BBB", "CCC", "DDD"])
    }


def _stepped(tmp_path, name, step_bars, config_factory, days=1200, start=300):
    """Prožene paper účet historií po ``step_bars`` barech, s restartem mezi kroky."""
    universe = _multi_asset(days)
    path = tmp_path / f"{name}.json"
    trader = PaperTrader(strategies.create("tsmom"), config_factory(), path)
    n = start
    while n <= days:
        trader.step({s: df.iloc[:n] for s, df in universe.items()})
        trader.save()
        trader = PaperTrader(strategies.create("tsmom"), config_factory(), path)
        n += step_bars
    return trader


def _cooldown_config():
    return make_config(
        500.0,
        CostModel(commission_pct=0.001, slippage_bps=5),
        RiskConfig(
            risk_per_trade=0.015,
            stop_loss_atr_mult=3.0,
            max_position_pct=0.20,
            max_open_positions=6,
            max_daily_loss_pct=None,
            max_drawdown_pct=None,
            min_position_value=20.0,
            allow_fractional=True,
            reentry_cooldown_bars=10,
        ),
    )


def _trade_keys(trader):
    return [
        (t.symbol, t.entry_time, t.exit_time, round(t.quantity, 6))
        for t in trader.engine.portfolio.trades
    ]


def test_result_does_not_depend_on_how_often_you_run_it(tmp_path):
    """Týdenní spouštění musí dát přesně totéž co denní.

    Engine přehrává bar po baru, takže na wall-clock nezáleží — ale jen
    dokud se ukládá **všechen** nasčítaný stav. Než se do stavu doplnil
    ``bar_index`` a ``cooldown_until``, pauza po stop-lossu se při každém
    restartu vynulovala a denní běh dal jiné obchody než týdenní.
    """
    daily = _stepped(tmp_path, "daily", 1, _cooldown_config)
    weekly = _stepped(tmp_path, "weekly", 5, _cooldown_config)
    monthly = _stepped(tmp_path, "monthly", 20, _cooldown_config)

    assert _trade_keys(daily) == _trade_keys(weekly)
    assert _trade_keys(daily) == _trade_keys(monthly)
    assert daily.engine.portfolio.cash == pytest.approx(weekly.engine.portfolio.cash)


def test_cooldown_state_survives_restart(tmp_path, history):
    trader = PaperTrader(
        strategies.create("sma_crossover"), _cooldown_config(), tmp_path / "c.json"
    )
    trader.step({"X": history})
    trader.engine._cooldown_until = {"X": 12345}
    trader.engine._bar_index = 999
    trader.save()

    restored = PaperTrader(
        strategies.create("sma_crossover"), _cooldown_config(), tmp_path / "c.json"
    )
    assert restored.engine._cooldown_until == {"X": 12345}
    assert restored.engine._bar_index == 999


def test_entry_fees_survive_restart(tmp_path):
    """Každý uzavřený obchod musí vykázat poplatky obou nohou.

    Vstupní poplatek se dřív dohledával v seznamu plnění, který se ze stavu
    neobnovuje. Pozice otevřená před restartem a zavřená po něm pak vykázala
    jen výstupní poplatek — tedy zhruba poloviční náklady.
    """
    universe = _multi_asset(1200)
    costs = CostModel(commission_pct=0.002, slippage_bps=0)
    path = tmp_path / "f.json"

    def config_factory():
        return make_config(
            5_000.0,
            costs,
            RiskConfig(
                max_daily_loss_pct=None,
                max_drawdown_pct=None,
                allow_fractional=True,
                max_position_pct=0.25,
            ),
        )

    trader = PaperTrader(strategies.create("tsmom"), config_factory(), path)
    n = 300
    while n <= 1200:
        trader.step({s: df.iloc[:n] for s, df in universe.items()})
        trader.save()
        trader = PaperTrader(strategies.create("tsmom"), config_factory(), path)
        n += 50

    trades = trader.engine.portfolio.trades
    assert len(trades) >= 5, "test potřebuje dost uzavřených obchodů"
    for trade in trades:
        both_legs = (trade.entry_price + trade.exit_price) * trade.quantity * 0.002
        assert trade.fees == pytest.approx(both_legs, rel=1e-6)


def test_vol_targeter_state_survives_restart(tmp_path, history):
    from trading.sizing import VolTargetConfig

    config = make_config(100_000.0, ZERO_COST, RiskConfig(max_daily_loss_pct=None))
    config.vol_target = VolTargetConfig(target_annual_vol=0.10)
    path = tmp_path / "v.json"

    trader = PaperTrader(strategies.create("sma_crossover"), config, path)
    trader.step({"X": history})
    trader.save()
    before = trader.engine._vol_targeter

    restored = PaperTrader(strategies.create("sma_crossover"), config, path)
    assert restored.engine._vol_targeter._count == before._count
    assert restored.engine._vol_targeter._scalar == pytest.approx(before._scalar)
    assert restored.engine._vol_targeter._variance == pytest.approx(before._variance)


def test_kelly_state_survives_restart(tmp_path, history):
    from trading.sizing import KellyConfig

    config = make_config(100_000.0, ZERO_COST, RiskConfig(max_daily_loss_pct=None))
    config.kelly = KellyConfig(fraction=0.25, min_trades=5)
    path = tmp_path / "k.json"

    trader = PaperTrader(strategies.create("sma_crossover"), config, path)
    trader.step({"X": history})
    trader.save()
    before = trader.engine._kelly.trade_count
    assert before > 0, "test potřebuje aspoň jeden zaznamenaný obchod"

    restored = PaperTrader(strategies.create("sma_crossover"), config, path)
    assert restored.engine._kelly.trade_count == before


def test_saved_file_is_valid_json(tmp_path, history):
    path = tmp_path / "s.json"
    trader = PaperTrader(strategies.create("sma_crossover"), config(), path)
    trader.step({"X": history})
    trader.save()

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 2
    assert "trades" in payload and "positions" in payload
    assert not (tmp_path / "s.tmp").exists(), "dočasný soubor se má přejmenovat, ne zůstat"
