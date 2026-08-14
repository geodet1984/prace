"""Příkazová řádka.

    python -m trading backtest --symbols AAPL,MSFT --start 2018-01-01
    python -m trading compare  --symbols SPY --start 2015-01-01
    python -m trading paper    --symbols AAPL --strategy sma_crossover
    python -m trading signals  --symbols AAPL,MSFT
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd

from . import data as data_module
from . import ensemble as ensemble_module
from . import stats as stats_module
from . import strategies
from .backtest import BacktestConfig, BacktestEngine
from .benchmark import buy_and_hold
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
    report = analyze(result, args.risk_free_rate, engine.portfolio.total_fees)

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
        rows.append(analyze(result, args.risk_free_rate, engine.portfolio.total_fees))

    bench_result, bench_fees = buy_and_hold(data, args.capital, COST_PRESETS[args.costs])
    rows.append(analyze(bench_result, args.risk_free_rate, bench_fees))

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
    blended, report = ensemble_module.blend(returns, args.scheme)
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
    except (ValueError, data_module.DataError) as exc:
        print(f"Chyba: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nPřerušeno.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
