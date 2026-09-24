"""Příkazová řádka.

    python -m trading backtest --symbols AAPL,MSFT --start 2018-01-01
    python -m trading compare  --symbols SPY --start 2015-01-01
    python -m trading paper    --symbols AAPL --strategy sma_crossover
    python -m trading signals  --symbols AAPL,MSFT
    python -m trading dashboard
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from . import dashboard as dashboard_module
from . import data as data_module
from . import ensemble as ensemble_module
from . import fx as fx_module
from . import hlidac as hlidac_module
from . import isin as isin_module
from . import ledger as ledger_module
from . import pozice_investoru as pozice_module
from . import stats as stats_module
from . import stav_trhu as stav_trhu_module
from . import strategies
from . import vypis as vypis_module
from .backtest import BacktestConfig, BacktestEngine
from .benchmark import buy_and_hold
from .calendar import EventCalendar
from .execution import COST_PRESETS
from .live import PaperTrader, make_config
from .metrics import analyze, equity_series
from .models import SignalType
from .risk import RiskConfig
from .sizing import KellyConfig, VolTargetConfig
from .strategies import BarContext
from .validation import configuration_returns, parameter_grid, walk_forward

logger = logging.getLogger("trading")


# --- pomocné ------------------------------------------------------------


def _parse_params(pairs: list[str] | None) -> dict:
    """Převede ``--param fast=10 --param slow=40`` na slovník s typy."""
    out: dict = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"parametr musí být ve tvaru klíč=hodnota, dostal jsem {pair!r}")
        key, _, value = pair.partition("=")
        out[key.strip()] = _coerce(value.strip())
    return out


def _coerce(value: str):
    if value.lower() in {"none", "null", ""}:
        return None
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _risk_from_args(args) -> RiskConfig:
    return RiskConfig(
        risk_per_trade=args.risk_per_trade,
        stop_loss_atr_mult=args.stop_atr,
        take_profit_atr_mult=args.target_atr,
        trailing_stop_atr_mult=args.trailing_atr,
        max_position_pct=args.max_position,
        max_open_positions=args.max_positions,
        max_daily_loss_pct=args.max_daily_loss,
        max_drawdown_pct=args.max_drawdown,
        allow_fractional=args.fractional,
        reentry_cooldown_bars=args.reentry_cooldown,
        min_position_value=args.min_position_value,
    )


def _config_from_args(args, close_at_end: bool = True) -> BacktestConfig:
    vol_target = (
        VolTargetConfig(target_annual_vol=args.vol_target, max_leverage=args.max_leverage)
        if args.vol_target
        else None
    )
    kelly = KellyConfig(fraction=args.kelly) if args.kelly else None

    calendar = None
    if getattr(args, "calendar", None):
        try:
            calendar = EventCalendar.from_csv(args.calendar)
        except FileNotFoundError as exc:
            raise SystemExit(str(exc)) from None
        if not calendar:
            logger.warning("kalendář %s je prázdný — blackout se neuplatní", args.calendar)

    # Nejtišší způsob, jak si vyrobit mrtvý systém: nechat výchozí minimální
    # hodnotu pozice na malém účtu. Engine pak jen zamítá signál za signálem
    # a report ukáže nula obchodů, aniž by řekl proč.
    largest = args.capital * args.max_position
    if largest < args.min_position_value:
        logger.warning(
            "největší možná pozice je %.2f, ale minimum je %.2f — takhle "
            "neproběhne ani jeden obchod. Snižte --min-position-value "
            "(u účtu do 500 USD zkuste 2) nebo zvyšte --max-position.",
            largest,
            args.min_position_value,
        )

    return BacktestConfig(
        initial_capital=args.capital,
        costs=COST_PRESETS[args.costs],
        risk=_risk_from_args(args),
        risk_free_rate=args.risk_free_rate,
        close_at_end=close_at_end,
        vol_target=vol_target,
        kelly=kelly,
        periods_per_year=args.periods_per_year,
        calendar=calendar,
        blackout_days_before=getattr(args, "blackout_before", 1),
        blackout_days_after=getattr(args, "blackout_after", 0),
    )


def _grid_from_args(pairs: list[str] | None) -> list[dict]:
    """Přeloží ``--grid fast=5,10,20`` na mřížku parametrů."""
    options: dict[str, list] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"mřížka musí být ve tvaru klíč=h1,h2,h3, dostal jsem {pair!r}")
        key, _, values = pair.partition("=")
        options[key.strip()] = [_coerce(v.strip()) for v in values.split(",") if v.strip()]
    return parameter_grid(**options)


def _strategy_returns(args, data) -> pd.DataFrame:
    """Spustí každou strategii zvlášť a vrátí jejich denní výnosy vedle sebe."""
    columns = {}
    for name in sorted(strategies.REGISTRY):
        engine = BacktestEngine(strategies.create(name), _config_from_args(args))
        try:
            result = engine.run(data)
        except ValueError as exc:
            logger.warning("%s: přeskakuji — %s", name, exc)
            continue
        equity = equity_series(result.equity_curve)
        returns = stats_module.equity_to_returns(equity)
        if len(returns) > 10 and returns.std() > 0:
            columns[name] = returns
    if len(columns) < 2:
        raise SystemExit("k sestavení směsi je potřeba aspoň 2 funkční strategie")
    return pd.DataFrame(columns).dropna(how="all").fillna(0.0)


def _load_data(args) -> dict[str, pd.DataFrame]:
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        raise SystemExit("zadejte aspoň jeden titul přes --symbols")

    if args.synthetic:
        logger.warning(
            "SYNTETICKÁ DATA — výsledek nevypovídá nic o kvalitě strategie, "
            "jen o tom, že kód běží"
        )
        return {
            s: data_module.synthetic(s, days=args.synthetic_days, seed=i + 4)
            for i, s in enumerate(symbols)
        }

    if args.csv_dir:
        out = {}
        for symbol in symbols:
            path = Path(args.csv_dir) / f"{symbol}.csv"
            out[symbol] = data_module.load_csv(path, symbol)
        return out

    return data_module.fetch_many(
        symbols, args.start, args.end, args.interval, use_cache=not args.no_cache
    )


def _export_equity(result, path: str) -> None:
    series = equity_series(result.equity_curve)
    series.name = "equity"
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    series.to_csv(path, index_label="date")
    print(f"\nEquity křivka uložena do {path}")


def _print_trades(result, limit: int = 20) -> None:
    if not result.trades:
        print("\nŽádné uzavřené obchody.")
        return
    print(f"\nPoslední obchody (max {limit}):")
    print(f"  {'symbol':<8}{'vstup':>12}{'výstup':>12}{'ks':>9}{'P/L':>11}  důvod")
    for trade in result.trades[-limit:]:
        entry = f"{trade.entry_time:%Y-%m-%d}"
        exit_ = f"{trade.exit_time:%Y-%m-%d}"
        print(
            f"  {trade.symbol:<8}{entry:>12}{exit_:>12}"
            f"{trade.quantity:>9.2f}{trade.net_pnl:>11,.2f}  {trade.exit_reason}"
        )


# --- příkazy ------------------------------------------------------------


def cmd_backtest(args) -> int:
    data = _load_data(args)
    strategy = strategies.create(args.strategy, **_parse_params(args.param))
    engine = BacktestEngine(strategy, _config_from_args(args))
    result = engine.run(data)
    report = analyze(
        result, args.risk_free_rate, engine.portfolio.total_fees, args.periods_per_year
    )

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False, default=str))
    else:
        print(report.format())
        if args.trades:
            _print_trades(result)

    if args.equity_csv:
        _export_equity(result, args.equity_csv)
    return 0


def cmd_compare(args) -> int:
    data = _load_data(args)
    rows = []

    for name in sorted(strategies.REGISTRY):
        strategy = strategies.create(name)
        engine = BacktestEngine(strategy, _config_from_args(args))
        try:
            result = engine.run(data)
        except ValueError as exc:
            logger.warning("%s: přeskakuji — %s", name, exc)
            continue
        rows.append(
            analyze(
                result, args.risk_free_rate, engine.portfolio.total_fees, args.periods_per_year
            )
        )

    bench_result, bench_fees = buy_and_hold(data, args.capital, COST_PRESETS[args.costs])
    rows.append(analyze(bench_result, args.risk_free_rate, bench_fees, args.periods_per_year))

    if args.json:
        print(json.dumps([r.to_dict() for r in rows], indent=2, ensure_ascii=False, default=str))
        return 0

    print()
    print(
        f"{'strategie':<26}{'výnos':>9}{'CAGR':>8}{'Sharpe':>8}"
        f"{'maxDD':>8}{'obch.':>7}{'úspěš.':>8}{'PF':>7}"
    )
    print("─" * 81)
    for r in rows:
        pf = "∞" if r.profit_factor == float("inf") else f"{r.profit_factor:.2f}"
        print(
            f"{r.strategy[:25]:<26}{r.total_return_pct:>8.1f}%{r.cagr_pct:>7.1f}%"
            f"{r.sharpe:>8.2f}{-r.max_drawdown_pct:>7.1f}%{r.num_trades:>7}"
            f"{r.win_rate_pct:>7.0f}%{pf:>7}"
        )
    print("─" * 81)
    print(
        "\nPozor: nejvyšší výnos není nejlepší výsledek. Srovnávejte Sharpe\n"
        "a maximální drawdown — ten rozhoduje, jestli se systém dá vydržet."
    )
    return 0


def cmd_paper(args) -> int:
    data = _load_data(args)
    strategy = strategies.create(args.strategy, **_parse_params(args.param))
    config = make_config(args.capital, COST_PRESETS[args.costs], _risk_from_args(args))

    trader = PaperTrader(strategy, config, args.state)
    fills = trader.step(data)

    if fills:
        print("\nProvedené příkazy:")
        for fill in fills:
            print(
                f"  {fill.timestamp:%Y-%m-%d}  {fill.side.value:<5}{fill.symbol:<8}"
                f"{fill.quantity:>10.4f} @ {fill.price:>9.2f}   {fill.reason}"
            )
    else:
        print("\nŽádné nové příkazy.")

    if not args.dry_run:
        trader.save()
    else:
        print("\n(--dry-run: stav se neukládá)")

    print()
    print(trader.status())
    return 0


def cmd_dashboard(args) -> int:
    """Rozjede přehled účtu v prohlížeči. Nic neobchoduje a nic neukládá."""
    state = Path(args.state)
    if args.papirovy_ucet and not state.exists():
        # Server by to řekl taky, ale až v prohlížeči. Tady je to vidět hned.
        print(
            f"Varování: stav účtu {state} zatím neexistuje. Přehled se rozjede, "
            "ale bude prázdný, dokud účet nezaložíte přes ./scripts/start-demo.sh",
            file=sys.stderr,
        )
    kniha = Path(args.kniha) if args.kniha else None
    if kniha and not kniha.exists():
        # Volitelný oddíl: chybějící kniha se prostě neukáže. Říct to ale
        # nahlas je lepší než nechat uživatele hledat, proč tam nic není.
        print(
            f"Varování: kniha {kniha} neexistuje, oddíl se skutečným portfoliem "
            "se v přehledu neukáže.",
            file=sys.stderr,
        )
    return dashboard_module.serve(state, host=args.host, port=args.port, ledger_path=kniha,
                                  papirovy_ucet=args.papirovy_ucet, s_rodicem=args.s_rodicem)


def _posledni_ceny(kniha) -> dict[str, float]:
    """Poslední známé ceny držených titulů.

    Titul, ke kterému se cena nezjistí, se do slovníku prostě nedostane —
    ocenění si s tím poradí a přizná to. Dosadit sem cokoli náhradního by
    znamenalo odhad vydávaný za skutečnost.
    """
    ceny: dict[str, float] = {}
    for symbol in kniha.holdings():
        try:
            df = data_module.fetch(symbol)
            ceny[symbol] = float(df["close"].iloc[-1])
        except (data_module.DataError, KeyError, IndexError) as exc:
            logger.warning("%s: cenu se nepodařilo zjistit (%s)", symbol, exc)
    return ceny


def _sledovane(args) -> list[str]:
    """Co rozebrat: zadané tituly, jinak to, co držíte podle knihy."""
    if args.symbols:
        return [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    kniha = Path(args.kniha)
    if kniha.exists():
        return sorted(ledger_module.Ledger.from_csv(kniha).holdings())
    raise SystemExit("zadejte --symbols, nebo založte knihu (python -m trading zapis …)")


def cmd_trh(args) -> int:
    """Co se na titulech děje a jak podobné situace dopadaly. Nic nedoporučuje."""
    try:
        udalosti = EventCalendar.from_csv(args.calendar)
    except FileNotFoundError:
        udalosti = ()
    for symbol in _sledovane(args):
        try:
            r = stav_trhu_module.rozbor(symbol, stav_trhu_module.nacti(symbol), udalosti,
                                        horizont=args.horizont)
        except (ValueError, data_module.DataError) as exc:
            print(f"\n  {symbol}: {exc}")
            continue
        print(f"\n  {symbol} — k {r['den']}, cena {r['cena']:,.2f}")
        print(f"  {'─' * 60}")
        for veta in r["vety"]:
            print(f"  • {veta}")
        print(f"\n  Historie: {r['srovnani']['veta']}")
        for zdroj in pozice_module.rozbor(symbol, stav_trhu_module.nacti(symbol)):
            print(f"\n  Co dělají investoři ({zdroj['zdroj']}):")
            for veta in zdroj["vety"]:
                print(f"  • {veta}")
            if zdroj.get("srovnani"):
                print(f"    Historie: {zdroj['srovnani']}")
        if r["udalosti"]:
            print("\n  Kalendář za posledních 30 dní (souslednost, ne příčina):")
            for u in r["udalosti"]:
                reakce = "—" if u["reakce"] is None else f"{u['reakce'] * 100:+.1f} %"
                print(f"    {u['den']}  {u['udalost']:<10} první den po: {reakce}")
    print("\n  Popis a historická četnost, ne předpověď ani doporučení.\n")
    return 0


def cmd_hlidac(args) -> int:
    """Ozve se, jen když je co říct. Nic nedoporučuje.

    Návratový kód **10** znamená „něco nového", 0 „nic". Skript kolem toho
    se pak nemusí hrabat ve výpisu.
    """
    kniha = _nacti_knihu(Path(args.kniha))
    prehled = kniha.valuation_czk(_posledni_ceny(kniha), fx_module.Kurzovnik())

    stav_cesta = Path(args.stav)
    stav = hlidac_module.Stav.nacti(stav_cesta)
    # Stav trhu u držených a sledovaných titulů; selhání nesmí zastavit
    # hlídání lhůt a chyb v knize.
    try:
        trh = dashboard_module.trh_payload(
            dashboard_module.sledovane_tituly(Path(args.kniha)))["tituly"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("stav trhu se nepodařilo zjistit: %s", exc)
        trh = []
    novinky = hlidac_module.serad(
        hlidac_module.nove(hlidac_module.zpravy(kniha, prehled, stav, trh=trh), stav)
    )

    if args.strucne:
        # Jediný řádek do oznámení; delší text se do něj stejně nevejde.
        print(hlidac_module.shrnuti(novinky))
    elif not novinky:
        print("\n  Nic nového od minule.\n")
    else:
        print(f"\n  Portfolio — {len(novinky)} nových zpráv\n")
        for z in novinky:
            znacka = {"chyba": "CHYBA ", "pozor": "pozor "}.get(z.zavaznost, "      ")
            print(f"  {znacka} {z.text}")
        print(
            "\n  Hlídač nic nedoporučuje. Říká, co se stalo — co s tím, "
            "rozhodujete vy.\n"
        )

    if not args.nezapisovat:
        hlidac_module.zapamatuj(novinky, stav, prehled.get("hodnota"), trh=trh)
        stav.uloz(stav_cesta)

    return 10 if novinky else 0


def cmd_ledger(args) -> int:
    """Přehled skutečného portfolia. Nic neobchoduje, jen počítá.

    Ceny se stahují jen pro ocenění otevřených pozic; není-li síť, použije
    se cache a chybějící tituly se přiznají místo tichého odhadu. Totéž
    platí pro kurzy s ``--czk``: kurzovník má vlastní cache a offline
    sáhne po nejbližším starším lístku.
    """
    kniha = _nacti_knihu(Path(args.kniha))
    ceny = _posledni_ceny(kniha)

    if args.czk:
        v = kniha.valuation_czk(ceny, fx_module.Kurzovnik())
        mena = v["mena"]
    else:
        v = kniha.valuation(ceny)
        meny = {t.currency for t in kniha.transactions}
        mena = kniha.transactions[0].currency if kniha.transactions else ""
        if len(meny) > 1:
            # Bez přepočtu se tady sčítají dolary s korunami. Součet je
            # nesmysl a nejde to poznat z čísla — jen z toho, že kniha má
            # víc měn. Říct to nahlas je jediná obrana.
            print(
                f"Varování: kniha míchá měny ({', '.join(sorted(meny))}), ale počítá se bez "
                f"přepočtu — součty pod hlavičkou {mena} sčítají různé měny dohromady. "
                "Použijte --czk.",
                file=sys.stderr,
            )

    print(f"\n  Portfolio — {len(kniha.transactions)} pohybů, {v['pozic']} titulů\n")
    print(f"  Vloženo vlastních peněz   {v['vlozeno']:>12,.2f} {mena}")
    print(f"  Hodnota portfolia         {v['hodnota']:>12,.2f} {mena}")
    if args.czk:
        print(f"  Hotovost                  {v['hotovost']:>12,.2f} {mena}")
    print(f"  {'-' * 44}")
    print(f"  Nerealizovaný zisk        {v['nerealizovany_zisk']:>12,.2f} {mena}")
    print(f"  Realizovaný zisk          {v['realizovany_zisk']:>12,.2f} {mena}")
    print(f"  Dividendy                 {v['dividendy']:>12,.2f} {mena}")
    print(f"  Daně                      {v['dane']:>12,.2f} {mena}")
    print(f"  Poplatky mimo obchody     {v['poplatky']:>12,.2f} {mena}")
    if args.czk:
        # Nevyměněná cizí hotovost je pořád měnová sázka, a tenhle řádek je
        # jediné místo, kde je to vidět. Bez něj by se dolarová dividenda
        # ponechaná v dolarech tvářila, že se od výplaty nehnula.
        print(f"  Kurz na hotovosti         {v['kurzovy_rozdil_hotovosti']:>12,.2f} {mena}")
        print(f"  Spread na směnách         {v['smeny']:>12,.2f} {mena}")
    print(f"  {'=' * 44}")
    print(f"  Celkem                    {v['celkem']:>12,.2f} {mena}")
    if v["vlozeno"]:
        print(f"  {'':26}{v['celkem'] / v['vlozeno'] * 100:>11.1f} % z vloženého")
    if v["poplatky_v_obchodech"]:
        # Zvlášť, a pod čarou: v součtu už jsou (uvnitř pořizovací ceny
        # a výnosu). Přičíst je znovu by je odečetlo dvakrát.
        print(
            f"\n  Z toho poplatky za obchody {v['poplatky_v_obchodech']:>11,.2f} {mena}"
            "  (už započtené v cenách)"
        )

    if args.czk:
        # Druhý rozpad TÉHOŽ zhodnocení z pozic — podle příčiny, ne podle
        # druhu. Proto stojí odděleně a proti jinému součtu: sečte se na
        # nerealizovaný + realizovaný zisk, ne na "celkem". Přičíst ho
        # k rozpadu výše by tytéž peníze započetlo dvakrát.
        z_pozic = v["nerealizovany_zisk"] + v["realizovany_zisk"]
        print(f"\n  Z čeho vznikl zisk z pozic ({z_pozic:,.2f} {mena}):")
        print(f"    Výkon aktiv             {v['vykon_aktiva']:>12,.2f} {mena}")
        print(f"    Pohyb kurzu             {v['kurzovy_rozdil']:>12,.2f} {mena}")
        print("    (dividend, daní ani poplatků se pohyb ceny titulu netýká)")

        if v["hotovost_meny"]:
            zustatky = ", ".join(
                f"{z['zustatek']:,.2f} {z['mena']}" for z in v["hotovost_meny"]
            )
            print(f"\n  Hotovost po měnách: {zustatky}")
        if v["dopoctene_smeny"]:
            # Kniha, ve které chybí zápis směny, se spočítat dá — jen se
            # k ní musí přidat předpoklad. Říct ho nahlas je povinnost.
            print(
                f"  U {len(v['dopoctene_smeny'])} pohybů chyběl v knize převod měny a dopočítal "
                "se kurzem ČNB toho dne.\n  Skutečný kurz od brokera byl horší — zapište směnu "
                "(typ smena), ať to sedí.",
                file=sys.stderr,
            )

        if v["kurzy"]:
            kurzy_text = ", ".join(f"{m} {k:.3f}" for m, k in sorted(v["kurzy"].items()))
            print(f"\n  Kurzy k {v['k_datu']}: {kurzy_text}")
        if v["bez_kurzu"]:
            print(
                f"  Bez kurzu, a proto mimo součty: {', '.join(v['bez_kurzu'])}",
                file=sys.stderr,
            )

    if v["bez_ceny"]:
        print(f"\n  Bez aktuální ceny (oceněno pořizovací): {', '.join(v['bez_ceny'])}")

    hodiny = [r for r in kniha.tax_clock() if not r["splneno"]]
    if hodiny:
        print("\n  Časový test — kdy dávky projdou tříletou lhůtou:")
        for r in hodiny[:10]:
            print(
                f"    {r['symbol']:<8} {r['quantity']:>10,.3f} ks   "
                f"nakoupeno {r['nakoupeno']}   osvobozeno {r['osvobozeno_od']}"
                f"   (za {r['dni_zbyva']} dní)"
            )
        print("\n  Není to daňové poradenství — jen rozdíl dat z vašich zápisů.")
    print()
    return 0


def _nacti_knihu(cesta: Path, *, zalozit: bool = False) -> ledger_module.Ledger:
    """Kniha z disku, nebo prázdná při zakládání.

    Zakládá se jen tam, kde to dává smysl (zápis a import). Přehled nad
    neexistující knihou je překlep v cestě, ne prázdné portfolio — a mlčky
    ukázat nuly by uživatele nechalo hledat chybu v evidenci.
    """
    if cesta.exists():
        return ledger_module.Ledger.from_csv(cesta)
    if not zalozit:
        raise ledger_module.LedgerError(f"kniha {cesta} neexistuje")
    print(f"Kniha {cesta} zatím není, zakládám novou.")
    return ledger_module.Ledger()


def _po_zapisu(kniha: ledger_module.Ledger) -> None:
    """Připomene kontrolu, našla-li kniha po zápisu něco podezřelého.

    Levnější varianta téhož: nechat člověka zjistit až za rok, že prodal
    titul, který v knize nikdy nekoupil.
    """
    nalezy = kniha.kontrola()
    if nalezy:
        chyb = sum(1 for n in nalezy if n.zavaznost == "chyba")
        print(
            f"\nKontrola knihy: {len(nalezy)} nálezů, z toho {chyb} chyb. "
            "Podrobnosti: python -m trading kontrola --kniha ...",
            file=sys.stderr,
        )


def cmd_zapis(args) -> int:
    """Připíše jeden pohyb do knihy.

    Ruční editace CSV je nejjistější cesta k překlepu — a k tomu, že to
    člověk po třetím obchodu vzdá. Zápis přes příkaz projde stejnou
    validací jako import, pozná duplicitu a soubor přepisuje atomicky.
    """
    cesta = Path(args.kniha)
    kniha = _nacti_knihu(cesta, zalozit=True)

    druh = ledger_module.TxType(args.typ)
    poznamka = args.poznamka or ""
    if args.symbol and isin_module.je_isin(args.symbol):
        ticker = isin_module.preloz(args.symbol, args.mena)
        if not ticker:
            raise SystemExit(f"k ISIN {args.symbol} se nenašel ticker kotovaný v {args.mena}; "
                             "zadejte ticker přímo, nebo ho doplňte do data/isin_tickery.csv")
        print(f"ISIN {args.symbol} → {ticker}")
        poznamka = f"ISIN {args.symbol}" + (f"; {poznamka}" if poznamka else "")
        args.symbol = ticker
    pohyb = ledger_module.Transaction(
        day=vypis_module.datum(args.den) if args.den else date.today(),
        type=druh,
        amount=vypis_module.castka(druh, args.castka or 0.0, args.pocet, args.cena),
        symbol=args.symbol,
        quantity=args.pocet,
        price=args.cena,
        fee=args.poplatek,
        currency=args.mena,
        note=poznamka,
    )

    if not kniha.add(pohyb):
        # Idempotence: dvakrát spuštěný zápis nesmí zdvojit obchod. Návratový
        # kód je nula schválně — opakované spuštění není chyba.
        print(f"Tenhle pohyb už v knize je ({pohyb.day} {pohyb.type.value}), nic se nezapsalo.")
        return 0

    kniha.to_csv(cesta)
    print(
        f"Zapsáno do {cesta}: {pohyb.day} {pohyb.type.value} "
        f"{pohyb.symbol or ''} {pohyb.amount:,.2f} {pohyb.currency}".replace("  ", " ")
    )
    print(f"Kniha má {len(kniha.transactions)} pohybů.")
    _po_zapisu(kniha)
    return 0


def cmd_import(args) -> int:
    """Nahraje cizí CSV výpis. Bez ``--ulozit`` jen ukáže, co by udělal."""
    vysledek = vypis_module.nacti_vypis(
        args.vypis,
        vypis_module.parse_mapovani(args.mapovani),
        vychozi_mena=args.mena,
        datum_format=args.datum_format,
    )
    cesta = Path(args.kniha)
    kniha = _nacti_knihu(cesta, zalozit=True)
    nova, pridano, preskoceno = ledger_module.merge(kniha, vysledek.pohyby)

    print(f"\n  Výpis {args.vypis} → kniha {cesta}\n")
    print("  Mapování sloupců:")
    for nas in ledger_module.Ledger.SLOUPCE:
        cizi = vysledek.mapovani.get(nas)
        print(f"    {nas:<10} {cizi if cizi else '— není, nechá se prázdné'}")

    print(f"\n  Přečteno {len(vysledek.pohyby)} pohybů: {pridano} nových, {preskoceno} už v knize.")
    if vysledek.prevedene:
        print("\n  ISIN převedené na ticker (podle měny nákupu):")
        for isin_, ticker in vysledek.prevedene.items():
            print(f"    {isin_} → {ticker}")
    if vysledek.neprevedene:
        print("  ISIN bez tickeru — ceny se nestáhnou, doplňte je do data/isin_tickery.csv:")
        for isin_ in vysledek.neprevedene:
            print(f"    {isin_}")
    if vysledek.preskocene:
        print(f"  Nepřečteno {len(vysledek.preskocene)} řádků:")
        for cislo_radku, duvod in vysledek.preskocene[:10]:
            print(f"    řádek {cislo_radku}: {duvod}")

    ukazka = [t for t in nova.transactions if t not in kniha.transactions][:15]
    if ukazka:
        print("\n  Přibude:")
        for t in ukazka:
            print(
                f"    {t.day}  {t.type.value:<10}{(t.symbol or ''):<8}"
                f"{t.quantity or '':>10}{t.price or '':>10}{t.amount:>12,.2f} {t.currency}"
            )

    if not args.ulozit:
        # Náhled je výchozí schválně. Cizí soubor je jediná cesta, kterou do
        # knihy teče něco, co uživatel nepsal — a špatně uhodnuté mapování
        # se pozná z náhledu, ne z přepsané evidence.
        print("\n  NÁHLED — nic se neuložilo. Sedí-li mapování, spusťte znovu s --ulozit.\n")
        return 0

    if pridano:
        nova.to_csv(cesta)
        print(f"\n  Uloženo, kniha má {len(nova.transactions)} pohybů.\n")
        _po_zapisu(nova)
    else:
        print("\n  Nic nového — kniha zůstala beze změny.\n")
    return 0


def cmd_kontrola(args) -> int:
    """Prověří knihu a vypíše, co v ní nesedí."""
    kniha = _nacti_knihu(Path(args.kniha))
    nalezy = kniha.kontrola()

    print(f"\n  Kontrola knihy {args.kniha} — {len(kniha.transactions)} pohybů\n")
    if not nalezy:
        print("  Nic podezřelého. Neznamená to, že je kniha správně — jen že si\n"
              "  neodporuje a nic v ní nevybočuje z řady.\n")
        return 0

    for nalez in nalezy:
        print(f"  {nalez}")
    chyb = sum(1 for n in nalezy if n.zavaznost == "chyba")
    print(f"\n  Celkem {len(nalezy)} nálezů, z toho {chyb} chyb.")
    print(
        "  „pozor\" nemusí být chyba — dividenda po prodeji celé pozice nebo\n"
        "  jednorázový velký vklad jsou v pořádku. Podívejte se na ně a nechte být.\n"
    )
    # Nenulový kód jen u skutečného rozporu, aby šlo kontrolu zapojit do
    # skriptu, aniž by ho shodilo každé upozornění.
    return 1 if chyb else 0


def cmd_signals(args) -> int:
    """Vyhodnotí aktuální signály bez jakéhokoli obchodování."""
    data = _load_data(args)
    strategy = strategies.create(args.strategy, **_parse_params(args.param))
    engine = BacktestEngine(strategy, _config_from_args(args))
    prepared = engine.prepare_data(data)

    print(f"\nSignály — {strategy.describe()}")
    print(f"  {'symbol':<8}{'datum':<13}{'close':>10}{'ATR':>9}  signál")
    print("─" * 62)

    for symbol in sorted(prepared):
        rows = prepared[symbol]
        timestamp = max(rows)
        row = rows[timestamp]
        signal = strategy.on_bar(BarContext(symbol, timestamp, row, None))
        marker = {
            SignalType.ENTER_LONG: "NÁKUP",
            SignalType.EXIT_LONG: "PRODEJ",
            SignalType.HOLD: "—",
        }[signal.type]
        atr_value = row.get("_atr")
        atr_text = f"{atr_value:.2f}" if atr_value == atr_value else "—"
        print(
            f"  {symbol:<8}{timestamp:%Y-%m-%d}   {row['close']:>10.2f}{atr_text:>9}  "
            f"{marker:<7}{signal.reason}"
        )
    print("─" * 62)
    print("Signál je návrh, ne příkaz. Velikost pozice a stop dopočítá risk manager.")

    config = _config_from_args(args)
    if config.calendar:
        latest = max(max(rows) for rows in prepared.values())
        print()
        print(config.calendar.describe(latest, within_days=21))
    return 0


def cmd_fetch(args) -> int:
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    loaded = data_module.fetch_many(symbols, args.start, args.end, args.interval, use_cache=False)
    for symbol, df in loaded.items():
        print(f"  {symbol:<8}{len(df):>6} barů   {df.index[0]:%Y-%m-%d} .. {df.index[-1]:%Y-%m-%d}")
    print(f"\nUloženo do {data_module.DEFAULT_CACHE_DIR}")
    return 0


def cmd_validate(args) -> int:
    """Walk-forward, deflated Sharpe a test přeoptimalizování v jednom."""
    data = _load_data(args)
    grid = _grid_from_args(args.grid)
    factory = lambda **params: strategies.create(args.strategy, **params)  # noqa: E731

    print(f"\nVALIDACE — {args.strategy}, {len(grid)} konfigurací v mřížce")

    result = walk_forward(
        factory,
        data,
        grid,
        _config_from_args(args),
        train_size=args.train_size,
        test_size=args.test_size,
        embargo=args.embargo,
        anchored=not args.rolling,
        strategy_name=args.strategy,
    )

    print("\n1) WALK-FORWARD — jediná poctivá equity křivka")
    print("─" * 62)
    print(result.format())
    if args.verbose:
        print("\n  Vybrané parametry po oknech:")
        for i, params in enumerate(result.chosen_params, 1):
            print(f"    okno {i}: {params}")

    print("\n2) DEFLATED SHARPE — obstojí to proti počtu pokusů?")
    print("─" * 62)
    dsr = stats_module.deflated_sharpe_ratio(result.oos_returns, n_trials=max(result.n_trials, 1))
    print(dsr.format())

    low, point, high = stats_module.block_bootstrap_sharpe(result.oos_returns, n_samples=1000)
    print(f"  95% interval Sharpe      {low:>10.2f} .. {high:.2f}")
    if low <= 0 <= high:
        print("  ! Interval obsahuje nulu — data neumí odlišit schopnost od náhody.")

    needed = stats_module.min_track_record_length(result.oos_returns)
    if needed == float("inf"):
        print("  Potřebná délka historie          nekonečno (Sharpe není nad prahem)")
    else:
        print(f"  Potřebná délka historie  {needed:>10.0f} dní ({needed / 252:.1f} let)")

    print("\n3) PBO — vybírá to signál, nebo šum?")
    print("─" * 62)
    try:
        matrix = configuration_returns(factory, data, grid, _config_from_args(args))
        pbo = stats_module.probability_of_backtest_overfitting(matrix, n_splits=args.pbo_splits)
        print(pbo.format())
    except ValueError as exc:
        print(f"  PBO nespočítáno: {exc}")

    print("\n" + "─" * 62)
    verdict = (
        "Strategie prošla. To pořád není záruka, jen absence důkazu o opaku."
        if dsr.is_significant
        else "Strategie neprošla. Výsledek se nedá odlišit od šťastného hledání."
    )
    print(verdict)
    return 0


def cmd_ensemble(args) -> int:
    """Zkombinuje strategie a ukáže, kolik na tom diverzifikace vydělá."""
    data = _load_data(args)
    returns = _strategy_returns(args, data)

    print(f"\nSMĚS {len(returns.columns)} STRATEGIÍ — vážení: {args.scheme}")
    print("─" * 62)
    blended, report = ensemble_module.blend(returns, args.scheme, args.periods_per_year)
    print(report.format())

    print("\n  Korelační matice:")
    corr = returns.corr()
    header = "".join(f"{c[:9]:>11}" for c in corr.columns)
    print(f"    {'':<20}{header}")
    for name, row in corr.iterrows():
        values = "".join(f"{v:>11.2f}" for v in row)
        print(f"    {str(name):<20}{values}")

    print("\n" + "─" * 62)
    if report.sharpe_gain > 1.1:
        print(
            f"Směs překonává nejlepší jednotlivou složku {report.sharpe_gain:.2f}x. "
            "Tohle je ta\nnejlevnější matematika v oboru — nestojí za ní žádná předpověď."
        )
    else:
        print(
            "Směs nepřináší proti nejlepší složce prakticky nic. Složky jsou si\n"
            "příliš podobné — chce to jinou rodinu signálu, ne další parametr."
        )
    return 0


# --- parser -------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trading",
        description="Backtest a paper trading akciových strategií.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Žádná strategie není vždy zisková. Cílem je kladná očekávaná\n"
            "hodnota při drawdownu, který se dá ustát — ne nulové ztráty."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="podrobné logování")

    sub = parser.add_subparsers(dest="command", required=True)

    def add_data_args(p):
        p.add_argument("--symbols", default="SPY", help="tickery oddělené čárkou")
        p.add_argument("--start", help="začátek období, YYYY-MM-DD")
        p.add_argument("--end", help="konec období, YYYY-MM-DD")
        p.add_argument("--interval", default="1d", help="granularita barů (1d, 1h, ...)")
        p.add_argument("--csv-dir", help="načíst data z adresáře s CSV místo stahování")
        p.add_argument("--no-cache", action="store_true", help="ignorovat lokální cache")
        p.add_argument(
            "--synthetic",
            action="store_true",
            help="vygenerovat data místo stahování (offline demo)",
        )
        p.add_argument(
            "--synthetic-days", type=int, default=750, help="délka syntetické řady"
        )

    def add_run_args(p):
        p.add_argument("--capital", type=float, default=100_000.0, help="počáteční kapitál")
        p.add_argument(
            "--costs", choices=sorted(COST_PRESETS), default="default", help="model poplatků"
        )
        p.add_argument("--risk-free-rate", type=float, default=0.0, help="roční bezriziková sazba")
        p.add_argument(
            "--periods-per-year",
            type=int,
            default=252,
            help="barů v roce pro anualizaci metrik: 252 akcie, 365 krypto",
        )
        p.add_argument(
            "--risk-per-trade", type=float, default=0.01, help="riziko na obchod (0.01 = 1 %%)"
        )
        p.add_argument("--stop-atr", type=float, default=2.0, help="stop-loss v násobcích ATR")
        p.add_argument(
            "--target-atr", type=float, default=None, help="take-profit v násobcích ATR"
        )
        p.add_argument(
            "--trailing-atr", type=float, default=None, help="posuvný stop v násobcích ATR"
        )
        p.add_argument(
            "--max-position", type=float, default=0.25, help="max. podíl kapitálu v titulu"
        )
        p.add_argument("--max-positions", type=int, default=5, help="max. počet otevřených pozic")
        p.add_argument("--max-daily-loss", type=float, default=0.03, help="denní stop účtu")
        p.add_argument("--max-drawdown", type=float, default=0.20, help="kill-switch na drawdown")
        p.add_argument("--fractional", action="store_true", help="povolit zlomkové akcie")
        p.add_argument(
            "--min-position-value",
            type=float,
            default=50.0,
            help="pod tuhle hodnotu se pozice neotevře; u malých účtů snižte",
        )
        p.add_argument(
            "--vol-target",
            type=float,
            default=None,
            help="cílová roční volatilita equity křivky, např. 0.10 (vypnuto = bez cílování)",
        )
        p.add_argument(
            "--max-leverage",
            type=float,
            default=1.5,
            help="strop škálování při cílování volatility",
        )
        p.add_argument(
            "--kelly",
            type=float,
            default=None,
            help="zlomkové Kelly, např. 0.25 pro čtvrtinové (vypnuto = pevná velikost)",
        )
        p.add_argument(
            "--calendar",
            help="CSV s plánovanými událostmi (sloupce date,name[,symbol]) pro blackout",
        )
        p.add_argument(
            "--blackout-before",
            type=int,
            default=1,
            help="dní před událostí, kdy se neotvírají nové pozice",
        )
        p.add_argument(
            "--blackout-after", type=int, default=0, help="dní po události bez nových pozic"
        )
        p.add_argument(
            "--reentry-cooldown",
            type=int,
            default=0,
            help="pauza v barech po vyražení stop-lossem, než se smí do titulu znovu vstoupit",
        )

    def add_strategy_args(p):
        p.add_argument(
            "--strategy", default="sma_crossover", choices=sorted(strategies.REGISTRY)
        )
        p.add_argument(
            "--param", action="append", help="parametr strategie, např. --param fast=10"
        )

    p_bt = sub.add_parser("backtest", help="spustit strategii na historických datech")
    add_data_args(p_bt)
    add_run_args(p_bt)
    add_strategy_args(p_bt)
    p_bt.add_argument("--trades", action="store_true", help="vypsat jednotlivé obchody")
    p_bt.add_argument("--equity-csv", help="uložit equity křivku do CSV")
    p_bt.add_argument("--json", action="store_true", help="strojově čitelný výstup")
    p_bt.set_defaults(func=cmd_backtest)

    p_cmp = sub.add_parser("compare", help="porovnat všechny strategie a benchmark")
    add_data_args(p_cmp)
    add_run_args(p_cmp)
    p_cmp.add_argument("--json", action="store_true", help="strojově čitelný výstup")
    p_cmp.set_defaults(func=cmd_compare)

    p_paper = sub.add_parser("paper", help="posunout paper trading účet o nová data")
    add_data_args(p_paper)
    add_run_args(p_paper)
    add_strategy_args(p_paper)
    p_paper.add_argument("--state", default="data/paper_state.json", help="soubor se stavem účtu")
    p_paper.add_argument("--dry-run", action="store_true", help="nic neukládat")
    p_paper.set_defaults(func=cmd_paper)

    p_sig = sub.add_parser("signals", help="zobrazit aktuální signály bez obchodování")
    add_data_args(p_sig)
    add_run_args(p_sig)
    add_strategy_args(p_sig)
    p_sig.set_defaults(func=cmd_signals)

    p_val = sub.add_parser(
        "validate", help="walk-forward + deflated Sharpe + test přeoptimalizování"
    )
    add_data_args(p_val)
    add_run_args(p_val)
    p_val.add_argument("--strategy", default="sma_crossover", choices=sorted(strategies.REGISTRY))
    p_val.add_argument(
        "--grid",
        action="append",
        help="mřížka parametrů, např. --grid fast=5,10,20 --grid slow=50,100",
    )
    p_val.add_argument(
        "--train-size", type=int, default=756, help="délka trénovacího okna v barech"
    )
    p_val.add_argument(
        "--test-size", type=int, default=252, help="délka testovacího okna v barech"
    )
    p_val.add_argument("--embargo", type=int, default=5, help="mezera mezi tréninkem a testem")
    p_val.add_argument(
        "--rolling", action="store_true", help="posouvat i začátek tréninku místo prodlužování okna"
    )
    p_val.add_argument(
        "--pbo-splits", type=int, default=10, help="počet částí pro CSCV (sudé číslo)"
    )
    p_val.set_defaults(func=cmd_validate)

    p_ens = sub.add_parser("ensemble", help="zkombinovat strategie a změřit přínos diverzifikace")
    add_data_args(p_ens)
    add_run_args(p_ens)
    p_ens.add_argument(
        "--scheme",
        choices=sorted(ensemble_module.WEIGHT_SCHEMES),
        default="inverse_vol",
        help="schéma vážení složek",
    )
    p_ens.set_defaults(func=cmd_ensemble)

    p_dash = sub.add_parser("dashboard", help="přehled paper účtu v prohlížeči (jen ke čtení)")
    p_dash.add_argument("--state", default="data/paper_state.json", help="soubor se stavem účtu")
    p_dash.add_argument(
        "--host",
        default="127.0.0.1",
        help="adresa k poslouchání; výchozí loopback, do sítě stav účtu nepatří",
    )
    p_dash.add_argument("--port", type=int, default=8765, help="port (0 = vybrat volný)")
    p_dash.add_argument(
        "--kniha",
        default=None,
        help="CSV se skutečným portfoliem; bez něj se oddíl neukáže",
    )
    p_dash.add_argument(
        "--papirovy-ucet", action="store_true",
        help="ukázat i papírový účet strategie (fiktivní peníze); bez něj jen skutečné portfolio",
    )
    p_dash.add_argument("--s-rodicem", action="store_true",
                        help="skončit spolu s procesem, který přehled spustil (pro aplikaci)")
    p_dash.set_defaults(func=cmd_dashboard)

    p_led = sub.add_parser("portfolio", help="přehled skutečného portfolia z účetní knihy")
    p_led.add_argument("--kniha", default="data/portfolio.csv", help="CSV s pohyby")
    p_led.add_argument(
        "--czk",
        action="store_true",
        help="přepočíst na koruny kurzy ČNB ke dni každého pohybu; "
        "u knihy s víc měnami je to jediný způsob, jak dostat smysluplný součet",
    )
    p_led.set_defaults(func=cmd_ledger)

    p_zap = sub.add_parser(
        "zapis",
        help="připsat jeden pohyb do knihy",
        description=(
            "Připíše pohyb do účetní knihy. Příklady:\n"
            "  python -m trading zapis nakup --symbol IWDA.AS --pocet 4 --cena 88.20 "
            "--poplatek 0.5 --mena EUR\n"
            "  python -m trading zapis vklad --castka 50000 --mena CZK\n"
            "  python -m trading zapis smena --castka -24500 --mena CZK --den 2026-08-14\n"
            "  python -m trading zapis smena --castka 1000 --mena USD --den 2026-08-14\n\n"
            "Směna se zapisuje jako dvojice řádků, jeden za každou měnu."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_zap.add_argument(
        "typ", choices=[t.value for t in ledger_module.TxType], help="druh pohybu"
    )
    p_zap.add_argument("--kniha", default="data/portfolio.csv", help="CSV s pohyby")
    p_zap.add_argument("--den", help="datum pohybu (YYYY-MM-DD nebo DD.MM.RRRR); výchozí dnes")
    p_zap.add_argument("--symbol", help="ticker; povinný u nákupu, prodeje a dividendy")
    p_zap.add_argument("--pocet", type=float, default=0.0, help="počet kusů (sloupec quantity)")
    p_zap.add_argument("--cena", type=float, default=0.0, help="cena za kus (sloupec price)")
    p_zap.add_argument(
        "--castka",
        type=float,
        default=None,
        help="peněžní tok (sloupec amount); u nákupu a prodeje se dopočte z počtu a ceny. "
        "Znaménko určuje druh pohybu, ne vy — jen u směny se bere, jak ho zadáte",
    )
    p_zap.add_argument("--poplatek", type=float, default=0.0, help="poplatek za obchod")
    p_zap.add_argument("--mena", default="CZK", help="měna toho pohybu (sloupec currency)")
    p_zap.add_argument("--poznamka", help="cokoli pro dohledání")
    p_zap.set_defaults(func=cmd_zapis)

    p_imp = sub.add_parser(
        "import",
        help="nahrát cizí CSV výpis do knihy (bez --ulozit jen náhled)",
    )
    p_imp.add_argument("--vypis", required=True, help="CSV od brokera")
    p_imp.add_argument("--kniha", default="data/portfolio.csv", help="cílová kniha")
    p_imp.add_argument(
        "--mapovani",
        action="append",
        help="ruční mapování sloupců, např. --mapovani day=Datum,type=Akce; "
        "doplňuje a přebíjí to uhodnuté podle hlavičky",
    )
    p_imp.add_argument(
        "--mena", default="USD", help="měna pro řádky, kde výpis měnu neuvádí"
    )
    p_imp.add_argument(
        "--datum-format",
        dest="datum_format",
        help="formát data pro strptime, např. '%%d/%%m/%%Y'; nutný u dat s lomítky",
    )
    p_imp.add_argument(
        "--ulozit", action="store_true", help="opravdu zapsat do knihy (bez toho jen náhled)"
    )
    p_imp.set_defaults(func=cmd_import)

    p_kon = sub.add_parser("kontrola", help="prověřit knihu a vypsat, co v ní nesedí")
    p_kon.add_argument("--kniha", default="data/portfolio.csv", help="CSV s pohyby")
    p_kon.set_defaults(func=cmd_kontrola)

    p_hlid = sub.add_parser(
        "hlidac", help="ozve se, jen když je u portfolia co říct (pro denní běh)"
    )
    p_hlid.add_argument("--kniha", default="data/portfolio.csv", help="CSV s pohyby")
    p_hlid.add_argument(
        "--stav", default="data/hlidac_stav.json",
        help="kde si hlídač pamatuje, co už oznámil",
    )
    p_hlid.add_argument(
        "--strucne", action="store_true", help="jen jedna věta, pro oznámení"
    )
    p_hlid.add_argument(
        "--nezapisovat", action="store_true",
        help="nezapamatovat si tenhle běh — na vyzkoušení, aniž by to umlčelo příště",
    )
    p_hlid.set_defaults(func=cmd_hlidac)

    p_trh = sub.add_parser("trh", help="co se na titulech děje a jak podobné stavy dopadaly")
    p_trh.add_argument("--symbols", help="tituly oddělené čárkou; bez nich ty z knihy")
    p_trh.add_argument("--kniha", default="data/portfolio.csv", help="CSV s pohyby")
    p_trh.add_argument("--calendar", default="data/udalosti.csv", help="kalendář událostí")
    p_trh.add_argument("--horizont", type=int, default=63,
                       help="za kolik obchodních dní se dívat, co následovalo (63 ≈ čtvrtletí)")
    p_trh.set_defaults(func=cmd_trh)

    p_fetch = sub.add_parser("fetch", help="stáhnout a nacachovat data")
    add_data_args(p_fetch)
    p_fetch.set_defaults(func=cmd_fetch)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-8s %(message)s",
    )

    try:
        return args.func(args)
    except (ValueError, data_module.DataError, ledger_module.LedgerError) as exc:
        print(f"Chyba: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nPřerušeno.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
