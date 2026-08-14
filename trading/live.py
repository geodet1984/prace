"""Paper trading — živý běh s fiktivními penězi proti reálným cenám.

Používá **stejné jádro** jako backtest (``BacktestEngine.process_bar``),
takže mezi testem a živým během neexistuje druhá implementace, která by
se mohla chovat jinak. Rozdíl je jediný: data přitékají postupně a stav
účtu se ukládá na disk, aby přežil restart.

Určeno pro denní bary: spouštět jednou denně po zavření burzy. Signál
z dnešního close se provede za zítřejší open — přesně jako v backtestu.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from .backtest import BacktestConfig, BacktestEngine
from .execution import CostModel
from .models import Fill, Order, Position, Side, Trade
from .risk import RiskConfig
from .strategies import Strategy

logger = logging.getLogger(__name__)

STATE_VERSION = 2
DEFAULT_STATE_PATH = Path("data/paper_state.json")


class PaperTrader:
    """Simulovaný účet, který si pamatuje stav mezi spuštěními."""

    def __init__(
        self,
        strategy: Strategy,
        config: BacktestConfig | None = None,
        state_path: str | Path = DEFAULT_STATE_PATH,
    ) -> None:
        self.strategy = strategy
        self.config = config or BacktestConfig()
        self.state_path = Path(state_path)
        self.engine = BacktestEngine(strategy, self.config)
        self.last_bar: pd.Timestamp | None = None
        self.created_at: str = datetime.now().isoformat(timespec="seconds")

        if self.state_path.exists():
            self.load()

    # --- perzistence -----------------------------------------------------

    def load(self) -> None:
        """Načte uložený stav účtu."""
        raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        if raw.get("version") != STATE_VERSION:
            raise ValueError(
                f"nekompatibilní verze stavu {raw.get('version')}, očekávána {STATE_VERSION}"
            )

        saved_strategy = raw.get("strategy")
        if saved_strategy and saved_strategy != self.strategy.describe():
            # Míchat obchody dvou různých strategií na jednom účtu by udělalo
            # ze statistiky nesmysl.
            raise ValueError(
                f"stav patří strategii {saved_strategy!r}, "
                f"spouštíte {self.strategy.describe()!r} — použijte jiný --state"
            )

        portfolio = self.engine.portfolio
        portfolio.cash = raw["cash"]
        portfolio.total_fees = raw.get("total_fees", 0.0)
        portfolio.positions = {
            p["symbol"]: Position(
                symbol=p["symbol"],
                quantity=p["quantity"],
                entry_price=p["entry_price"],
                entry_time=datetime.fromisoformat(p["entry_time"]),
                stop_loss=p.get("stop_loss"),
                take_profit=p.get("take_profit"),
                initial_risk=p.get("initial_risk", 0.0),
                entry_fees=p.get("entry_fees", 0.0),
            )
            for p in raw.get("positions", [])
        }
        portfolio.trades = [
            Trade(
                symbol=t["symbol"],
                quantity=t["quantity"],
                entry_time=datetime.fromisoformat(t["entry_time"]),
                entry_price=t["entry_price"],
                exit_time=datetime.fromisoformat(t["exit_time"]),
                exit_price=t["exit_price"],
                fees=t["fees"],
                exit_reason=t.get("exit_reason", ""),
            )
            for t in raw.get("trades", [])
        ]
        self.engine._pending = {
            o["symbol"]: Order(
                symbol=o["symbol"],
                side=Side(o["side"]),
                quantity=o["quantity"],
                reason=o.get("reason", ""),
                stop_loss=o.get("stop_loss"),
                take_profit=o.get("take_profit"),
            )
            for o in raw.get("pending", [])
        }
        self.engine._last_price = raw.get("last_price", {})
        self.engine._rejected = raw.get("rejected_signals", 0)
        self.engine._bar_index = raw.get("bar_index", 0)
        self.engine._cooldown_until = {
            str(k): int(v) for k, v in (raw.get("cooldown_until") or {}).items()
        }
        self.engine._last_equity = raw.get("last_equity")

        saved_vol = raw.get("vol_target")
        if saved_vol and self.engine._vol_targeter is not None:
            self.engine._vol_targeter._variance = saved_vol.get("variance")
            self.engine._vol_targeter._count = saved_vol.get("count", 0)
            self.engine._vol_targeter._scalar = saved_vol.get("scalar", 1.0)

        saved_kelly = raw.get("kelly_r_multiples")
        if saved_kelly and self.engine._kelly is not None:
            self.engine._kelly._r_multiples = [float(v) for v in saved_kelly]
        self.engine.risk._peak_equity = raw.get("peak_equity", portfolio.initial_capital)
        self.last_bar = pd.Timestamp(raw["last_bar"]) if raw.get("last_bar") else None
        self.created_at = raw.get("created_at", self.created_at)

        logger.info(
            "stav načten: hotovost %.2f, pozic %d, obchodů %d, poslední bar %s",
            portfolio.cash,
            len(portfolio.positions),
            len(portfolio.trades),
            self.last_bar,
        )

    def save(self) -> None:
        """Uloží stav účtu atomicky."""
        portfolio = self.engine.portfolio
        payload = {
            "version": STATE_VERSION,
            "created_at": self.created_at,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "strategy": self.strategy.describe(),
            "initial_capital": portfolio.initial_capital,
            "cash": portfolio.cash,
            "total_fees": portfolio.total_fees,
            "peak_equity": self.engine.risk.peak_equity,
            "rejected_signals": self.engine._rejected,
            "last_bar": self.last_bar.isoformat() if self.last_bar is not None else None,
            "last_price": self.engine._last_price,
            # Všechno, co engine nasčítal v průběhu běhu. Bez toho se to při
            # každém spuštění vynuluje a funkce jako pauza po stop-lossu nebo
            # cílování volatility tiše přestanou fungovat — což je horší než
            # kdyby vůbec nebyly, protože stav vypadá v pořádku.
            "bar_index": self.engine._bar_index,
            "cooldown_until": self.engine._cooldown_until,
            "last_equity": self.engine._last_equity,
            "vol_target": (
                {
                    "variance": self.engine._vol_targeter._variance,
                    "count": self.engine._vol_targeter._count,
                    "scalar": self.engine._vol_targeter._scalar,
                }
                if self.engine._vol_targeter is not None
                else None
            ),
            "kelly_r_multiples": (
                self.engine._kelly._r_multiples if self.engine._kelly is not None else None
            ),
            "positions": [
                {
                    "symbol": p.symbol,
                    "quantity": p.quantity,
                    "entry_price": p.entry_price,
                    "entry_time": p.entry_time.isoformat(),
                    "stop_loss": p.stop_loss,
                    "take_profit": p.take_profit,
                    "initial_risk": p.initial_risk,
                    "entry_fees": p.entry_fees,
                }
                for p in portfolio.positions.values()
            ],
            "pending": [
                {
                    "symbol": o.symbol,
                    "side": o.side.value,
                    "quantity": o.quantity,
                    "reason": o.reason,
                    "stop_loss": o.stop_loss,
                    "take_profit": o.take_profit,
                }
                for o in self.engine._pending.values()
            ],
            "trades": [
                {
                    "symbol": t.symbol,
                    "quantity": t.quantity,
                    "entry_time": t.entry_time.isoformat(),
                    "entry_price": t.entry_price,
                    "exit_time": t.exit_time.isoformat(),
                    "exit_price": t.exit_price,
                    "fees": t.fees,
                    "exit_reason": t.exit_reason,
                }
                for t in portfolio.trades
            ],
        }

        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.state_path)

    # --- běh -------------------------------------------------------------

    def step(self, data: dict[str, pd.DataFrame]) -> list[Fill]:
        """Zpracuje všechny bary novější než poslední uložený.

        Vrací seznam provedených příkazů z tohoto volání.
        """
        prepared = self.engine.prepare_data(data)
        symbols = sorted(prepared)
        timeline = sorted({ts for rows in prepared.values() for ts in rows})

        warmup = max(self.strategy.warmup, self.config.atr_window + 1)
        if len(timeline) <= warmup:
            raise ValueError(
                f"málo historie: {len(timeline)} barů, potřeba aspoň {warmup + 1}. "
                "Stáhněte delší období (--start)."
            )

        fills_before = len(self.engine.portfolio.fills)
        # Poprvé začínáme až za rozehřívacím oknem; potom navazujeme tam,
        # kde jsme minule skončili, ať se stejný bar nezpracuje dvakrát.
        cutoff = self.last_bar if self.last_bar is not None else timeline[warmup]
        processed = 0

        for timestamp in timeline:
            if timestamp <= cutoff:
                continue
            bars = {s: prepared[s][timestamp] for s in symbols if timestamp in prepared[s]}
            if not bars:
                continue
            self.engine.process_bar(timestamp, bars, allow_new_signals=True)
            self.last_bar = timestamp
            processed += 1

        if processed:
            logger.info("zpracováno %d nových barů do %s", processed, self.last_bar)
        else:
            logger.info("žádná nová data (poslední bar %s)", self.last_bar)

        return self.engine.portfolio.fills[fills_before:]

    # --- přehled ---------------------------------------------------------

    def status(self) -> str:
        """Textový přehled účtu."""
        portfolio = self.engine.portfolio
        prices = self.engine._last_price
        equity = portfolio.equity(prices)
        pnl = equity - portfolio.initial_capital
        pnl_pct = pnl / portfolio.initial_capital * 100.0

        lines = [
            "─" * 62,
            f"  PAPER ÚČET — {self.strategy.describe()}",
            f"  založen {self.created_at}, poslední bar {self.last_bar}",
            "─" * 62,
            f"  Hotovost      {portfolio.cash:>14,.2f}",
            f"  Pozice        {portfolio.positions_value(prices):>14,.2f}",
            f"  Equity        {equity:>14,.2f}",
            f"  P/L           {pnl:>14,.2f}   ({pnl_pct:+.2f} %)",
            f"  Poplatky      {portfolio.total_fees:>14,.2f}",
        ]

        halt = self.engine.risk.trading_blocked(equity)
        if halt:
            lines.append(f"  ⚠ nové vstupy zastaveny: {halt}")

        if portfolio.positions:
            lines.append("")
            lines.append("  Otevřené pozice:")
            lines.append(
                f"    {'symbol':<8}{'ks':>10}{'vstup':>10}{'cena':>10}{'P/L':>12}{'stop':>10}"
            )
            for pos in portfolio.positions.values():
                price = prices.get(pos.symbol, pos.entry_price)
                stop = f"{pos.stop_loss:.2f}" if pos.stop_loss else "—"
                lines.append(
                    f"    {pos.symbol:<8}{pos.quantity:>10.4f}{pos.entry_price:>10.2f}"
                    f"{price:>10.2f}{pos.unrealized_pnl(price):>12,.2f}{stop:>10}"
                )

        if self.engine._pending:
            lines.append("")
            lines.append("  Příkazy na příští otevření trhu:")
            for order in self.engine._pending.values():
                lines.append(
                    f"    {order.side.value:<5}{order.symbol:<8}{order.quantity:>10.4f}"
                    f"   {order.reason}"
                )

        closed = portfolio.trades
        if closed:
            wins = sum(1 for t in closed if t.is_win)
            realized = sum(t.net_pnl for t in closed)
            lines.append("")
            lines.append(
                f"  Uzavřeno {len(closed)} obchodů, úspěšnost {wins / len(closed) * 100:.0f} %, "
                f"realizováno {realized:,.2f}"
            )

        lines.append("─" * 62)
        return "\n".join(lines)


def make_config(
    initial_capital: float = 100_000.0,
    costs: CostModel | None = None,
    risk: RiskConfig | None = None,
) -> BacktestConfig:
    """Konfigurace pro živý běh — pozice se na konci nezavírají."""
    return BacktestConfig(
        initial_capital=initial_capital,
        costs=costs or CostModel(),
        risk=risk or RiskConfig(),
        close_at_end=False,
    )
