"""Kalendář plánovaných událostí a blackout kolem nich.

Proč tohle na rozdíl od sentimentu ze zpráv obstojí: **nic nepředpovídá**.
Neptá se, co trh udělá, až vyjde inflace — ptá se, jestli dnes hrozí zvýšené
riziko mezery. To je fakt z veřejného kalendáře, ne odhad.

A hlavně: **data událostí jsou známá dopředu.** Fed publikuje termíny zasedání
rok předem, statistický úřad termíny zveřejnění taky. Použít je v backtestu
proto není pohled do budoucnosti — v den obchodu ta informace skutečně
existovala. Tím se to zásadně liší od makro *hodnot*, které se zveřejňují
se zpožděním a ještě se revidují.

Co blackout dělá: **blokuje nové vstupy**, nic víc. Otevřené pozice se dál
řídí svými stopy. Zavírat všechno před zasedáním Fedu by znamenalo platit
skluz dvakrát za rok osmkrát a připravit se o trendy, které přes událost
prošly bez úhony.

Zdroje termínů:
  * Fed / FOMC     federalreserve.gov/monetarypolicy/fomccalendars.htm
  * CPI, NFP       bls.gov/schedule/news_release
  * ECB            ecb.europa.eu → Governing Council meeting dates
  * výsledky firem  stránky emitenta nebo IR kalendář
"""

from __future__ import annotations

import csv
import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

WEEKDAYS = {"po": 0, "ut": 1, "st": 2, "ct": 3, "pa": 4, "so": 5, "ne": 6}


@dataclass(frozen=True)
class MarketEvent:
    """Jedna plánovaná událost."""

    day: date
    name: str

    symbol: str | None = None
    """``None`` znamená makro událost, která se týká všech titulů.

    Konkrétní ticker použijte u věcí vázaných na jednu firmu — typicky
    zveřejnění výsledků.
    """

    def affects(self, symbol: str) -> bool:
        return self.symbol is None or self.symbol == symbol

    def __str__(self) -> str:
        scope = self.symbol or "vše"
        return f"{self.day:%Y-%m-%d} {self.name} ({scope})"


def _as_date(value) -> date:
    """Převede cokoli rozumného na ``date``."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    return datetime.strptime(text, "%Y-%m-%d").date()


class EventCalendar:
    """Sada plánovaných událostí s dotazem na blackout.

    Prázdný kalendář je platný a znamená „blackout se nikdy neuplatní" —
    díky tomu jde funkce nechat vypnutou bez zvláštní větve v enginu.
    """

    def __init__(self, events: Iterable[MarketEvent] = ()) -> None:
        self.events: list[MarketEvent] = sorted(events, key=lambda e: (e.day, e.name))

    def __len__(self) -> int:
        return len(self.events)

    def __iter__(self) -> Iterator[MarketEvent]:
        return iter(self.events)

    def __bool__(self) -> bool:
        return bool(self.events)

    # --- načtení ---------------------------------------------------------

    @classmethod
    def from_csv(cls, path: str | Path) -> EventCalendar:
        """Načte kalendář z CSV se sloupci ``date,name[,symbol]``.

        Řádky začínající ``#`` se ignorují, takže do souboru jde psát
        poznámky a odkazy na zdroj.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"kalendář neexistuje: {path}")

        events: list[MarketEvent] = []
        with path.open(encoding="utf-8", newline="") as handle:
            rows = (line for line in handle if not line.lstrip().startswith("#"))
            for line_no, row in enumerate(csv.DictReader(rows), start=2):
                if not row.get("date"):
                    continue
                try:
                    day = _as_date(row["date"])
                except ValueError:
                    logger.warning(
                        "%s:%d: nečitelné datum %r, přeskakuji", path, line_no, row["date"]
                    )
                    continue
                symbol = (row.get("symbol") or "").strip().upper() or None
                events.append(MarketEvent(day, (row.get("name") or "událost").strip(), symbol))

        logger.info("kalendář %s: načteno %d událostí", path, len(events))
        return cls(events)

    @classmethod
    def merged(cls, *calendars: EventCalendar) -> EventCalendar:
        return cls(event for calendar in calendars for event in calendar)

    # --- dotazy ----------------------------------------------------------

    def blackout_reason(
        self, day, symbol: str, days_before: int = 1, days_after: int = 0
    ) -> str | None:
        """Vrátí popis události, kvůli které se dnes nevstupuje, nebo ``None``.

        Okno se počítá v **kalendářních** dnech, ne obchodních. Je to
        konzervativnější: pátek před pondělním zasedáním spadne do okna
        i při ``days_before=1``, což je správně, protože mezeru přes víkend
        stop-loss taky neochrání.
        """
        if days_before < 0 or days_after < 0:
            raise ValueError("okno blackoutu nemůže být záporné")

        today = _as_date(day)
        for event in self.events:
            if not event.affects(symbol):
                continue
            delta = (event.day - today).days
            if -days_after <= delta <= days_before:
                when = (
                    "dnes"
                    if delta == 0
                    else (f"za {delta} d" if delta > 0 else f"před {-delta} d")
                )
                return f"{event.name} {when}"
        return None

    def upcoming(self, day, within_days: int = 14, symbol: str | None = None) -> list[MarketEvent]:
        """Události v následujících dnech, volitelně jen pro jeden titul."""
        today = _as_date(day)
        horizon = today + timedelta(days=within_days)
        return [
            event
            for event in self.events
            if today <= event.day <= horizon and (symbol is None or event.affects(symbol))
        ]

    def describe(self, day=None, within_days: int = 30) -> str:
        """Textový přehled nejbližších událostí."""
        if not self.events:
            return "  Kalendář je prázdný — blackout se neuplatní."
        reference = _as_date(day) if day is not None else self.events[0].day
        rows = self.upcoming(reference, within_days)
        if not rows:
            return f"  Žádná událost v následujících {within_days} dnech."
        lines = [f"  Nejbližší události ({len(rows)}):"]
        lines.extend(f"    {event}" for event in rows)
        return "\n".join(lines)


# --- pravidelné události ------------------------------------------------


def nth_weekday(year: int, month: int, weekday: int, n: int = 1) -> date:
    """N-tý daný den v týdnu v měsíci. ``weekday`` je 0=pondělí.

    ``nth_weekday(2026, 3, 4, 1)`` je první pátek v březnu 2026.
    """
    if not 1 <= n <= 5:
        raise ValueError("n musí být v <1, 5>")
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    result = first + timedelta(days=offset + 7 * (n - 1))
    if result.month != month:
        raise ValueError(f"{n}. den {weekday} v {month}/{year} neexistuje")
    return result


def monthly_rule(
    start: date | str,
    end: date | str,
    weekday: int,
    n: int,
    name: str,
    symbol: str | None = None,
) -> EventCalendar:
    """Kalendář z pravidla „n-tý den v týdnu každý měsíc".

    Použitelné jen na události, které skutečně pravidlo mají — americké
    Nonfarm Payrolls vycházejí na první pátek v měsíci. Termíny zasedání
    Fedu ani zveřejnění CPI žádné takové pravidlo nemají a **musí se vzít
    z publikovaného kalendáře**; odhadovat je by znamenalo vyrábět si data.
    """
    first, last = _as_date(start), _as_date(end)
    events: list[MarketEvent] = []
    year, month = first.year, first.month

    while date(year, month, 1) <= last:
        try:
            day = nth_weekday(year, month, weekday, n)
        except ValueError:
            day = None
        if day is not None and first <= day <= last:
            events.append(MarketEvent(day, name, symbol))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)

    return EventCalendar(events)


def us_nonfarm_payrolls(start: date | str, end: date | str) -> EventCalendar:
    """Americké Nonfarm Payrolls — první pátek v měsíci.

    Jediná z hlavních amerických statistik, která má spolehlivé pravidlo.
    Výjimky existují (svátky), takže i tohle si u kritických dat ověřte
    proti kalendáři BLS.
    """
    return monthly_rule(start, end, WEEKDAYS["pa"], 1, "NFP")
