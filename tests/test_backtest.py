"""Testy jádra backtestu.

Těžiště je na poctivosti simulace: žádný pohled do budoucnosti, plnění za
otevírací cenu dalšího baru, konzistentní účetnictví. Chyba v kterékoli
z těchto věcí vyrobí strategii, která vydělává jen v tabulce.
"""

import pandas as pd
import pytest

from trading import data as data_module
from trading import strategies
from trading.backtest import BacktestConfig, BacktestEngine
from trading.execution import ZERO_COST, CostModel
from trading.models import HOLD, Side, Signal, SignalType
from trading.risk import RiskConfig
from trading.strategies import Strategy


def make_frame(closes, opens=None, highs=None, lows=None, start="2024-01-01"):
    n = len(closes)
    opens = opens or closes
    highs = highs or [max(o, c) for o, c in zip(opens, closes, strict=True)]
    lows = lows or [min(o, c) for o, c in zip(opens, closes, strict=True)]
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1_000_000.0] * n,
        },
        index=pd.bdate_range(start, periods=n),
    )


class ScriptedStrategy(Strategy):
    """Strategie, která vydá předem daný signál na baru dané pozice.

    Klíč ve ``script`` je pořadí baru ve vstupním DataFrame, ne pořadí
    volání ``on_bar`` — engine prvních pár barů přeskakuje kvůli rozehřátí
    indikátorů a testy by se o ten posun jinak opíraly.
    """

    name = "scripted"

    def __init__(self, script: dict[int, Signal]):
        self.script = script
        self._bar_number: dict = {}

    def prepare(self, df):
        self._bar_number = {ts: i for i, ts in enumerate(df.index)}
        return df.copy()

    @property
    def warmup(self) -> int:
        return 0

    def on_bar(self, ctx):
        return self.script.get(self._bar_number.get(ctx.timestamp, -1), HOLD)


ENTER = Signal(SignalType.ENTER_LONG, 1.0, "test vstup")
EXIT = Signal(SignalType.EXIT_LONG, 1.0, "test výstup")


def loose_config(**overrides):
    """Konfigurace bez omezení, aby testy měřily engine, ne risk limity."""
    risk = RiskConfig(
        risk_per_trade=0.02,
        stop_loss_atr_mult=2.0,
        max_position_pct=1.0,
        max_open_positions=10,
        max_daily_loss_pct=None,
        max_drawdown_pct=None,
        min_position_value=0.0,
    )
    base = {"initial_capital": 100_000.0, "costs": ZERO_COST, "risk": risk, "atr_window": 3}
    base.update(overrides)
    return BacktestConfig(**base)


# --- plnění příkazů -----------------------------------------------------


def test_order_fills_at_next_bar_open_not_current_close():
    """Signál z close dne T se plní za open dne T+1."""
    closes = [100.0] * 20
    opens = [100.0] * 20
    opens[10] = 123.0  # nápadná otevírací cena, ať je poznat, kde se plnilo

    df = make_frame(closes, opens=opens, highs=[130.0] * 20, lows=[90.0] * 20)
    engine = BacktestEngine(ScriptedStrategy({9: ENTER}), loose_config())
    engine.run({"X": df})

    entry_fills = [f for f in engine.portfolio.fills if f.side is Side.BUY]
    assert len(entry_fills) == 1
    assert entry_fills[0].price == pytest.approx(123.0)
    assert entry_fills[0].timestamp == df.index[10]


def test_slippage_makes_buy_more_expensive_and_sell_cheaper():
    df = make_frame([100.0] * 20, highs=[130.0] * 20, lows=[90.0] * 20)
    costs = CostModel(slippage_bps=100.0)  # 1 %
    engine = BacktestEngine(ScriptedStrategy({5: ENTER, 10: EXIT}), loose_config(costs=costs))
    engine.run({"X": df})

    buy = next(f for f in engine.portfolio.fills if f.side is Side.BUY)
    sell = next(f for f in engine.portfolio.fills if f.side is Side.SELL)
    assert buy.price == pytest.approx(101.0)
    assert sell.price == pytest.approx(99.0)


def test_commission_is_charged_on_both_legs():
    df = make_frame([100.0] * 20, highs=[130.0] * 20, lows=[90.0] * 20)
    costs = CostModel(commission_pct=0.001, slippage_bps=0.0)
    engine = BacktestEngine(ScriptedStrategy({5: ENTER, 10: EXIT}), loose_config(costs=costs))
    engine.run({"X": df})

    trade = engine.portfolio.trades[0]
    assert trade.fees == pytest.approx(trade.quantity * 100.0 * 0.001 * 2)


# --- žádný pohled do budoucnosti ----------------------------------------


def test_future_bars_do_not_change_past_decisions():
    """Nejsilnější test proti lookahead biasu.

    Spustíme stejnou strategii nad zkrácenými daty a nad plnými daty.
    Obchody, které se odehrály v překryvu, musí být identické — jinak
    engine někde nakoukl dopředu.
    """
    full = data_module.synthetic("X", days=400, seed=11)
    truncated = full.iloc[:250]

    strategy_a = strategies.create("sma_crossover", fast=10, slow=30)
    strategy_b = strategies.create("sma_crossover", fast=10, slow=30)

    engine_short = BacktestEngine(strategy_a, loose_config(close_at_end=False))
    engine_short.run({"X": truncated})

    engine_long = BacktestEngine(strategy_b, loose_config(close_at_end=False))
    engine_long.run({"X": full})

    cutoff = truncated.index[-1]
    short_trades = [(t.entry_time, t.exit_time, t.quantity) for t in engine_short.portfolio.trades]
    long_trades = [
        (t.entry_time, t.exit_time, t.quantity)
        for t in engine_long.portfolio.trades
        if t.exit_time <= cutoff
    ]
    assert short_trades == long_trades


def test_backtest_is_deterministic():
    df = data_module.synthetic("X", days=300, seed=3)
    runs = []
    for _ in range(2):
        engine = BacktestEngine(strategies.create("donchian_breakout"), loose_config())
        result = engine.run({"X": df})
        runs.append([(t.entry_time, t.exit_price, t.net_pnl) for t in result.trades])
    assert runs[0] == runs[1]


# --- účetnictví ---------------------------------------------------------


def test_final_equity_equals_capital_plus_realized_pnl():
    df = data_module.synthetic("X", days=300, seed=5)
    engine = BacktestEngine(strategies.create("sma_crossover"), loose_config())
    result = engine.run({"X": df})

    realized = sum(t.net_pnl for t in result.trades)
    assert not engine.portfolio.positions, "close_at_end měl zavřít vše"
    assert result.final_equity == pytest.approx(result.initial_capital + realized, abs=0.01)


def test_open_positions_are_closed_at_the_end():
    """Nerealizovaná ztráta se nesmí schovat mimo statistiku obchodů."""
    df = make_frame([100.0] * 25, highs=[101.0] * 25, lows=[99.0] * 25)
    engine = BacktestEngine(ScriptedStrategy({22: ENTER}), loose_config(close_at_end=True))
    result = engine.run({"X": df})

    assert not engine.portfolio.positions
    assert [t.exit_reason for t in result.trades] == ["konec testovaného období"]


def test_close_at_end_disabled_leaves_position_open():
    df = make_frame([100.0] * 25, highs=[130.0] * 25, lows=[90.0] * 25)
    engine = BacktestEngine(ScriptedStrategy({18: ENTER}), loose_config(close_at_end=False))
    engine.run({"X": df})
    assert engine.portfolio.positions


def test_equity_curve_has_exactly_one_point_per_bar():
    """Závěrečné uzavření pozic poslední bod přepíše, nepřidává druhý.

    Regrese: duplicitní časové razítko rozbíjelo skládání out-of-sample
    úseků ve walk-forwardu.
    """
    df = data_module.synthetic("X", days=120, seed=2)
    engine = BacktestEngine(strategies.create("sma_crossover", fast=5, slow=10), loose_config())
    result = engine.run({"X": df})

    assert len(result.equity_curve) == len(df)
    timestamps = [p.timestamp for p in result.equity_curve]
    assert len(set(timestamps)) == len(timestamps)


def test_final_equity_point_reflects_the_closing_trades():
    """Přepsaný poslední bod musí obsahovat výsledek závěrečného prodeje."""
    df = make_frame([100.0] * 25, highs=[101.0] * 25, lows=[99.0] * 25)
    engine = BacktestEngine(ScriptedStrategy({22: ENTER}), loose_config(close_at_end=True))
    result = engine.run({"X": df})

    assert result.equity_curve[-1].positions_value == 0.0
    assert result.equity_curve[-1].cash == pytest.approx(result.final_equity)


# --- stop-loss v enginu -------------------------------------------------


def test_stop_loss_exit_is_recorded_with_reason():
    # Stabilní cena, pak prudký propad, který musí spustit stop.
    closes = [100.0] * 15 + [70.0] * 5
    lows = [99.0] * 15 + [65.0] * 5
    df = make_frame(closes, lows=lows, highs=[101.0] * 20)

    engine = BacktestEngine(ScriptedStrategy({12: ENTER}), loose_config())
    result = engine.run({"X": df})

    assert result.trades
    assert result.trades[0].exit_reason == "stop-loss"
    assert result.trades[0].net_pnl < 0


def test_position_size_respects_risk_budget():
    """Ztráta na stop-lossu nesmí výrazně přesáhnout nastavené riziko."""
    closes = [100.0] * 15 + [95.0] * 5
    df = make_frame(closes, lows=[99.0] * 15 + [80.0] * 5, highs=[101.0] * 20)

    config = loose_config()
    config.risk.risk_per_trade = 0.01
    engine = BacktestEngine(ScriptedStrategy({12: ENTER}), config)
    result = engine.run({"X": df})

    assert result.trades
    loss = -result.trades[0].net_pnl
    # Mezera přes noc může ztrátu zvětšit, ale ne řádově.
    assert loss <= config.initial_capital * 0.01 * 3


def test_reentry_cooldown_delays_repurchase_after_stop():
    """Po stopu se do stejného titulu nesmí vlézt dřív než po dané pauze."""
    closes = [100.0] * 10 + [90.0] + [100.0] * 15
    df = make_frame(closes, lows=[99.0] * 10 + [85.0] + [99.0] * 15, highs=[101.0] * 26)
    script = {i: ENTER for i in range(5, 25)}

    def run(cooldown):
        config = loose_config()
        config.risk.reentry_cooldown_bars = cooldown
        engine = BacktestEngine(ScriptedStrategy(dict(script)), config)
        engine.run({"X": df})
        return engine.portfolio.trades

    without = run(0)
    with_cooldown = run(10)

    stop_out = next(t for t in without if t.exit_reason == "stop-loss")
    fast_reentry = next(t for t in without if t.entry_time > stop_out.exit_time)
    slow_reentry = next(t for t in with_cooldown if t.entry_time > stop_out.exit_time)

    assert fast_reentry.entry_time < slow_reentry.entry_time
    assert (slow_reentry.entry_time - stop_out.exit_time).days >= 10


# --- limity portfolia ---------------------------------------------------


def test_max_open_positions_caps_concurrent_holdings():
    data = {s: make_frame([100.0] * 30, highs=[130.0] * 30, lows=[90.0] * 30) for s in "ABCDE"}

    config = loose_config()
    config.risk.max_open_positions = 2
    config.risk.max_position_pct = 0.2

    engine = BacktestEngine(ScriptedStrategy({i: ENTER for i in range(5, 25)}), config)
    engine.run({"X": data["A"]} | {k: v for k, v in data.items()})

    peak = max(
        len([f for f in engine.portfolio.fills[: i + 1] if f.side is Side.BUY])
        - len([f for f in engine.portfolio.fills[: i + 1] if f.side is Side.SELL])
        for i in range(len(engine.portfolio.fills))
    )
    assert peak <= 2


def test_cash_never_goes_negative():
    data = {s: data_module.synthetic(s, days=250, seed=i) for i, s in enumerate("ABCD")}
    config = loose_config()
    config.risk.max_open_positions = 4
    config.risk.max_position_pct = 0.5
    config.risk.risk_per_trade = 0.05

    engine = BacktestEngine(strategies.create("sma_crossover", fast=5, slow=15), config)
    engine.run(data)
    assert engine.portfolio.cash >= -1e-6


def test_drawdown_kill_switch_stops_new_entries():
    """Po překročení limitu drawdownu se už nesmí otevírat nové pozice."""
    closes = [100.0] * 5 + [40.0] * 25
    df = make_frame(closes, lows=[99.0] * 5 + [35.0] * 25, highs=[101.0] * 30)

    config = loose_config()
    config.risk.max_drawdown_pct = 0.02
    config.risk.risk_per_trade = 0.5
    config.risk.max_position_pct = 1.0

    engine = BacktestEngine(ScriptedStrategy({i: ENTER for i in range(3, 28)}), config)
    result = engine.run({"X": df})

    assert result.rejected_signals > 0


# --- vstupní kontroly ---------------------------------------------------


def test_too_little_data_raises():
    df = make_frame([100.0] * 5)
    engine = BacktestEngine(strategies.create("sma_crossover"), loose_config())
    with pytest.raises(ValueError, match="málo dat"):
        engine.run({"X": df})


def test_empty_data_raises():
    engine = BacktestEngine(strategies.create("sma_crossover"), loose_config())
    with pytest.raises(ValueError):
        engine.run({})
