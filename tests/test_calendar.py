"""Testy kalendáře událostí a blackoutu.

Blackout je jediná forma „sledování dění ve světě", která v tomhle projektu
obstojí — protože nic nepředpovídá. Testy proto hlídají hlavně to, že
zůstane u blokování vstupů a nesáhne na otevřené pozice.
"""

from datetime import date

import pandas as pd
import pytest

from trading import data as data_module
from trading import strategies
from trading.backtest import BacktestConfig, BacktestEngine
from trading.calendar import (
    EventCalendar,
    MarketEvent,
    monthly_rule,
    nth_weekday,
    us_nonfarm_payrolls,
)
from trading.execution import ZERO_COST
from trading.risk import RiskConfig

FOMC = date(2026, 3, 18)


@pytest.fixture
def calendar():
    return EventCalendar(
        [
            MarketEvent(FOMC, "FOMC"),
            MarketEvent(date(2026, 3, 25), "vysledky", "AAPL"),
        ]
    )


# --- základ -------------------------------------------------------------


def test_empty_calendar_is_falsy_and_never_blocks():
    empty = EventCalendar()
    assert not empty
    assert len(empty) == 0
    assert empty.blackout_reason("2026-03-18", "SPY") is None


def test_events_are_sorted_by_date():
    calendar = EventCalendar(
        [MarketEvent(date(2026, 5, 1), "b"), MarketEvent(date(2026, 1, 1), "a")]
    )
    assert [e.name for e in calendar] == ["a", "b"]


def test_macro_event_affects_every_symbol():
    event = MarketEvent(FOMC, "FOMC")
    assert event.affects("SPY") and event.affects("BTC-USD")


def test_symbol_event_affects_only_that_symbol():
    event = MarketEvent(FOMC, "vysledky", "AAPL")
    assert event.affects("AAPL")
    assert not event.affects("MSFT")


# --- okno blackoutu -----------------------------------------------------


def test_day_before_and_of_event_are_blocked(calendar):
    assert calendar.blackout_reason("2026-03-17", "SPY") is not None
    assert calendar.blackout_reason("2026-03-18", "SPY") is not None


def test_outside_the_window_is_clear(calendar):
    assert calendar.blackout_reason("2026-03-16", "SPY") is None
    assert calendar.blackout_reason("2026-03-19", "SPY") is None


def test_days_after_extends_the_window(calendar):
    assert calendar.blackout_reason("2026-03-19", "SPY", days_after=0) is None
    assert calendar.blackout_reason("2026-03-19", "SPY", days_after=1) is not None


def test_wider_window_blocks_earlier(calendar):
    assert calendar.blackout_reason("2026-03-14", "SPY", days_before=1) is None
    assert calendar.blackout_reason("2026-03-14", "SPY", days_before=5) is not None


def test_zero_window_blocks_only_the_day_itself(calendar):
    assert calendar.blackout_reason("2026-03-17", "SPY", days_before=0) is None
    assert calendar.blackout_reason("2026-03-18", "SPY", days_before=0) is not None


def test_window_counts_calendar_days_not_trading_days():
    """Pátek před pondělní událostí musí spadnout do okna už při 3 dnech.

    Přes víkend se neobchoduje, ale mezera vzniká — a stop-loss ji nezachytí.
    """
    monday = date(2026, 3, 23)
    calendar = EventCalendar([MarketEvent(monday, "FOMC")])
    friday = "2026-03-20"
    assert calendar.blackout_reason(friday, "SPY", days_before=1) is None
    assert calendar.blackout_reason(friday, "SPY", days_before=3) is not None


def test_symbol_event_does_not_block_others(calendar):
    assert calendar.blackout_reason("2026-03-24", "AAPL") is not None
    assert calendar.blackout_reason("2026-03-24", "MSFT") is None


def test_reason_mentions_the_event(calendar):
    assert "FOMC" in calendar.blackout_reason("2026-03-18", "SPY")


def test_negative_window_is_rejected(calendar):
    with pytest.raises(ValueError):
        calendar.blackout_reason("2026-03-18", "SPY", days_before=-1)


def test_accepts_dates_timestamps_and_strings(calendar):
    for day in (FOMC, pd.Timestamp("2026-03-18"), "2026-03-18", "2026-03-18 14:30:00"):
        assert calendar.blackout_reason(day, "SPY") is not None


# --- načtení z CSV ------------------------------------------------------


def _write(tmp_path, text):
    path = tmp_path / "udalosti.csv"
    path.write_text(text, encoding="utf-8")
    return path


def test_csv_round_trip(tmp_path):
    path = _write(tmp_path, "date,name,symbol\n2026-03-18,FOMC,\n2026-03-25,vysledky,AAPL\n")
    calendar = EventCalendar.from_csv(path)
    assert len(calendar) == 2
    assert calendar.events[0].symbol is None
    assert calendar.events[1].symbol == "AAPL"


def test_csv_ignores_comments_and_blank_dates(tmp_path):
    path = _write(
        tmp_path,
        "# zdroj: federalreserve.gov\ndate,name,symbol\n2026-03-18,FOMC,\n,prazdne,\n",
    )
    assert len(EventCalendar.from_csv(path)) == 1


def test_csv_skips_unparseable_dates(tmp_path):
    path = _write(tmp_path, "date,name,symbol\n18.3.2026,spatne,\n2026-03-18,FOMC,\n")
    calendar = EventCalendar.from_csv(path)
    assert [e.name for e in calendar] == ["FOMC"]


def test_csv_uppercases_symbols(tmp_path):
    path = _write(tmp_path, "date,name,symbol\n2026-03-25,vysledky,aapl\n")
    assert EventCalendar.from_csv(path).events[0].symbol == "AAPL"


def test_missing_csv_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        EventCalendar.from_csv(tmp_path / "neexistuje.csv")


def test_shipped_sample_calendar_parses():
    from pathlib import Path

    sample = Path(__file__).resolve().parent.parent / "data" / "udalosti-vzor.csv"
    assert len(EventCalendar.from_csv(sample)) >= 1


# --- pravidelné události ------------------------------------------------


def test_first_friday_of_march_2026():
    assert nth_weekday(2026, 3, 4, 1) == date(2026, 3, 6)


def test_third_wednesday():
    assert nth_weekday(2026, 3, 2, 3) == date(2026, 3, 18)


def test_nth_weekday_rejects_impossible_n():
    with pytest.raises(ValueError):
        nth_weekday(2026, 3, 4, 6)


def test_nonfarm_payrolls_are_first_fridays():
    calendar = us_nonfarm_payrolls("2026-01-01", "2026-12-31")
    assert len(calendar) == 12
    for event in calendar:
        assert event.day.weekday() == 4
        assert event.day.day <= 7


def test_monthly_rule_respects_bounds():
    calendar = monthly_rule("2026-03-01", "2026-05-31", 4, 1, "NFP")
    assert all(date(2026, 3, 1) <= e.day <= date(2026, 5, 31) for e in calendar)


def test_merged_calendars_keep_all_events():
    merged = EventCalendar.merged(
        us_nonfarm_payrolls("2026-01-01", "2026-06-30"),
        EventCalendar([MarketEvent(FOMC, "FOMC")]),
    )
    assert len(merged) == 7


# --- přehled ------------------------------------------------------------


def test_upcoming_respects_horizon(calendar):
    assert len(calendar.upcoming("2026-03-01", within_days=20)) == 1
    assert len(calendar.upcoming("2026-03-01", within_days=30)) == 2


def test_upcoming_can_filter_by_symbol(calendar):
    assert len(calendar.upcoming("2026-03-01", 60, symbol="MSFT")) == 1
    assert len(calendar.upcoming("2026-03-01", 60, symbol="AAPL")) == 2


def test_describe_renders(calendar):
    assert "FOMC" in calendar.describe("2026-03-01")
    assert "prázdný" in EventCalendar().describe()


# --- napojení na engine -------------------------------------------------


def _config(calendar=None, **overrides):
    base = {
        "initial_capital": 100_000.0,
        "costs": ZERO_COST,
        "risk": RiskConfig(
            risk_per_trade=0.02,
            max_position_pct=1.0,
            max_daily_loss_pct=None,
            max_drawdown_pct=None,
            allow_fractional=True,
        ),
        "calendar": calendar,
    }
    base.update(overrides)
    return BacktestConfig(**base)


def _run(calendar=None, **overrides):
    history = data_module.synthetic("X", days=900, seed=6)
    engine = BacktestEngine(
        strategies.create("sma_crossover", fast=10, slow=30), _config(calendar, **overrides)
    )
    return engine, engine.run({"X": history}), history


def test_no_calendar_means_no_blackout():
    engine, result, _ = _run()
    assert engine._blackout_reason(pd.Timestamp("2026-03-18"), "X") is None
    assert result.trades


def test_blackout_prevents_entries_on_event_days():
    """Blackout blokuje den, kdy vzniká signál — ne den plnění.

    Příkaz se plní za otevírací cenu následujícího baru, takže událost
    umístěná na den vstupu by přišla o bar pozdě. Test proto staví kalendář
    na bar *před* vstupem.
    """
    _, baseline, history = _run()
    entries = sorted({t.entry_time for t in baseline.trades})
    assert entries, "test potřebuje aspoň jeden vstup bez blackoutu"

    positions = {ts: i for i, ts in enumerate(history.index)}
    signal_days = {
        history.index[positions[ts] - 1].date() for ts in entries if positions.get(ts, 0) > 0
    }
    calendar = EventCalendar([MarketEvent(day, "test") for day in signal_days])

    _, blocked, _ = _run(calendar, blackout_days_before=0)
    assert not ({t.entry_time for t in blocked.trades} & set(entries))


def test_blackout_reduces_trades_but_does_not_stop_everything():
    _, baseline, history = _run()
    every_month = EventCalendar(
        [
            MarketEvent(d.date(), "mesicni")
            for d in pd.date_range(history.index[0], history.index[-1], freq="MS")
        ]
    )
    _, blocked, _ = _run(every_month, blackout_days_before=2)

    assert len(blocked.trades) < len(baseline.trades)
    assert blocked.rejected_signals > baseline.rejected_signals


def test_blackout_never_closes_open_positions():
    """Blokují se jen vstupy — zavírat vše před událostí by stálo skluz navíc."""
    history = data_module.synthetic("X", days=900, seed=6)
    all_days = EventCalendar([MarketEvent(d.date(), "vzdy") for d in history.index])

    engine = BacktestEngine(
        strategies.create("sma_crossover", fast=10, slow=30),
        _config(all_days, blackout_days_before=0, close_at_end=False),
    )
    result = engine.run({"X": history})

    # Kalendář blokuje každý den, takže nesmí vzniknout žádný nový obchod...
    assert not result.trades
    # ...a zároveň se nesmí objevit výstup z důvodu "událost".
    assert all("událost" not in f.reason for f in engine.portfolio.fills)


def test_symbol_specific_blackout_leaves_other_symbols_alone():
    universe = {s: data_module.synthetic(s, days=900, seed=i + 4) for i, s in enumerate("XY")}
    days = [d.date() for d in universe["X"].index]
    calendar = EventCalendar([MarketEvent(day, "vysledky", "X") for day in days])

    engine = BacktestEngine(
        strategies.create("sma_crossover", fast=10, slow=30),
        _config(calendar, blackout_days_before=0),
    )
    result = engine.run(universe)

    traded = {t.symbol for t in result.trades}
    assert "X" not in traded
    assert "Y" in traded
