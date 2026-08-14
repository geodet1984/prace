"""Výkonnostní metriky.

Samotný celkový výnos nic neříká. +40 % s propadem -60 % po cestě je
horší výsledek než +15 % s propadem -8 %, protože první variantu člověk
v reálu nevydrží a vypne ji na dně.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .models import BacktestResult, EquityPoint, Trade

TRADING_DAYS = 252
"""Obchodních dní v roce na akciovém trhu.

Krypto se obchoduje 365 dní v roce, takže tam je tahle konstanta špatně
a Sharpe by vyšel podhodnocený o faktor √(365/252) = 1,20. Proto všechny
funkce berou ``periods_per_year`` jako parametr.
"""

CRYPTO_DAYS = 365

MIN_STD = 1e-12
"""Mez, pod kterou je řada považovaná za konstantní — viz ``stats.MIN_STD``."""


@dataclass
class PerformanceReport:
    """Souhrn výsledků jednoho běhu."""

    strategy: str = ""
    initial_capital: float = 0.0
    final_equity: float = 0.0
    total_return_pct: float = 0.0
    cagr_pct: float = 0.0
    volatility_pct: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    max_drawdown_pct: float = 0.0
    max_drawdown_days: int = 0
    calmar: float = 0.0
    num_trades: int = 0
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    payoff_ratio: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0
    avg_holding_days: float = 0.0
    total_fees: float = 0.0
    exposure_pct: float = 0.0
    rejected_signals: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}

    def format(self) -> str:
        """Textový report pro terminál."""
        w = 34
        lines = [
            "─" * 52,
            f"  {self.strategy}",
            "─" * 52,
            f"{'Počáteční kapitál':<{w}}{self.initial_capital:>16,.0f}",
            f"{'Konečný kapitál':<{w}}{self.final_equity:>16,.0f}",
            f"{'Celkový výnos':<{w}}{self.total_return_pct:>15.2f} %",
            f"{'CAGR (roční)':<{w}}{self.cagr_pct:>15.2f} %",
            "",
            f"{'Sharpe ratio':<{w}}{self.sharpe:>16.2f}",
            f"{'Sortino ratio':<{w}}{self.sortino:>16.2f}",
            f"{'Volatilita (roční)':<{w}}{self.volatility_pct:>15.2f} %",
            f"{'Max. drawdown':<{w}}{-self.max_drawdown_pct:>15.2f} %",
            f"{'Délka max. drawdownu':<{w}}{self.max_drawdown_days:>13} dní",
            f"{'Calmar ratio':<{w}}{self.calmar:>16.2f}",
            "",
            f"{'Počet obchodů':<{w}}{self.num_trades:>16}",
            f"{'Úspěšnost':<{w}}{self.win_rate_pct:>15.1f} %",
            f"{'Profit factor':<{w}}{self.profit_factor:>16.2f}",
            f"{'Očekávaná hodnota / obchod':<{w}}{self.expectancy:>16,.2f}",
            f"{'Průměrný zisk':<{w}}{self.avg_win:>16,.2f}",
            f"{'Průměrná ztráta':<{w}}{self.avg_loss:>16,.2f}",
            f"{'Poměr zisk/ztráta':<{w}}{self.payoff_ratio:>16.2f}",
            f"{'Nejlepší obchod':<{w}}{self.best_trade:>16,.2f}",
            f"{'Nejhorší obchod':<{w}}{self.worst_trade:>16,.2f}",
            f"{'Průměrná držba':<{w}}{self.avg_holding_days:>13.1f} dní",
            "",
            f"{'Zaplacené poplatky':<{w}}{self.total_fees:>16,.2f}",
            f"{'Čas v trhu':<{w}}{self.exposure_pct:>15.1f} %",
            f"{'Zamítnuté signály (risk)':<{w}}{self.rejected_signals:>16}",
        ]
        if self.warnings:
            lines.append("")
            lines.append("  Upozornění:")
            lines.extend(f"   ! {warning}" for warning in self.warnings)
        lines.append("─" * 52)
        return "\n".join(lines)


def equity_series(curve: list[EquityPoint]) -> pd.Series:
    """Převede equity křivku na pandas Series."""
    if not curve:
        return pd.Series(dtype=float)
    return pd.Series(
        [p.equity for p in curve], index=pd.DatetimeIndex([p.timestamp for p in curve])
    )


def drawdown_series(equity: pd.Series) -> pd.Series:
    """Propad od dosavadního maxima v každém okamžiku (kladné = propad)."""
    if equity.empty:
        return equity
    peak = equity.cummax()
    return 1.0 - equity / peak


def max_drawdown(equity: pd.Series) -> tuple[float, int]:
    """Největší propad (podíl) a jeho délka ve dnech do návratu na maximum."""
    if len(equity) < 2:
        return 0.0, 0

    dd = drawdown_series(equity)
    max_dd = float(dd.max())

    # Nejdelší úsek pod předchozím maximem.
    peak = equity.cummax()
    under_water = equity < peak
    longest = 0
    start = None
    for timestamp, is_under in under_water.items():
        if is_under and start is None:
            start = timestamp
        elif not is_under and start is not None:
            longest = max(longest, (timestamp - start).days)
            start = None
    if start is not None:
        longest = max(longest, (under_water.index[-1] - start).days)

    return max_dd, longest


def sharpe_ratio(
    equity: pd.Series, risk_free_rate: float = 0.0, periods_per_year: int = TRADING_DAYS
) -> float:
    """Anualizovaný Sharpe. rf je roční bezriziková sazba (0.04 = 4 %)."""
    rets = equity.pct_change().dropna()
    if len(rets) < 2:
        return 0.0
    excess = rets - risk_free_rate / periods_per_year
    std = excess.std(ddof=1)
    if math.isnan(std) or std < MIN_STD:
        return 0.0
    return float(excess.mean() / std * math.sqrt(periods_per_year))


def sortino_ratio(
    equity: pd.Series, risk_free_rate: float = 0.0, periods_per_year: int = TRADING_DAYS
) -> float:
    """Jako Sharpe, ale trestá jen záporné odchylky."""
    rets = equity.pct_change().dropna()
    if len(rets) < 2:
        return 0.0
    excess = rets - risk_free_rate / periods_per_year
    downside = excess[excess < 0]
    if downside.empty:
        return float("inf") if excess.mean() > 0 else 0.0
    dstd = math.sqrt(float((downside**2).mean()))
    if dstd < MIN_STD:
        return 0.0
    return float(excess.mean() / dstd * math.sqrt(periods_per_year))


def cagr(equity: pd.Series) -> float:
    """Složená roční míra růstu jako podíl."""
    if len(equity) < 2 or equity.iloc[0] <= 0:
        return 0.0
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    if years <= 0:
        return 0.0
    ratio = equity.iloc[-1] / equity.iloc[0]
    if ratio <= 0:
        return -1.0
    return float(ratio ** (1.0 / years) - 1.0)


def trade_stats(trades: list[Trade]) -> dict:
    """Statistiky nad uzavřenými obchody."""
    if not trades:
        return {
            "num_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "expectancy": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "payoff_ratio": 0.0,
            "best": 0.0,
            "worst": 0.0,
            "avg_holding_days": 0.0,
        }

    pnls = np.array([t.net_pnl for t in trades], dtype=float)
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]

    gross_profit = float(wins.sum())
    gross_loss = float(-losses.sum())
    avg_win = float(wins.mean()) if wins.size else 0.0
    avg_loss = float(losses.mean()) if losses.size else 0.0

    return {
        "num_trades": len(trades),
        "win_rate": len(wins) / len(trades) * 100.0,
        # Profit factor = hrubý zisk / hrubá ztráta. Pod 1.0 systém prodělává.
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else float("inf"),
        "expectancy": float(pnls.mean()),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": abs(avg_win / avg_loss) if avg_loss else float("inf"),
        "best": float(pnls.max()),
        "worst": float(pnls.min()),
        "avg_holding_days": float(np.mean([t.holding_days for t in trades])),
    }


def _collect_warnings(report: PerformanceReport, result: BacktestResult) -> list[str]:
    """Upozorní na výsledky, kterým se nedá věřit."""
    warnings: list[str] = []

    if report.num_trades < 30:
        warnings.append(
            f"jen {report.num_trades} obchodů — statisticky bezcenný vzorek, "
            "výsledek je z velké části náhoda"
        )
    if report.max_drawdown_pct > 30:
        warnings.append(
            f"drawdown {report.max_drawdown_pct:.0f} % — takový propad většina lidí "
            "v reálu nevydrží a systém vypne na dně"
        )
    if report.profit_factor > 3 and report.num_trades < 100:
        warnings.append(
            "podezřele vysoký profit factor při málo obchodech — typický příznak "
            "přeoptimalizování na historii"
        )
    if report.total_fees > abs(report.final_equity - report.initial_capital) and report.num_trades:
        warnings.append("poplatky převyšují hrubý výsledek — strategie obchoduje příliš často")
    if report.exposure_pct < 5 and report.num_trades:
        warnings.append("kapitál je v trhu minimum času — většina peněz jen leží")

    return warnings


def analyze(
    result: BacktestResult,
    risk_free_rate: float = 0.0,
    total_fees: float = 0.0,
    periods_per_year: int = TRADING_DAYS,
) -> PerformanceReport:
    """Spočítá kompletní report z výsledku backtestu."""
    equity = equity_series(result.equity_curve)
    stats = trade_stats(result.trades)
    max_dd, dd_days = max_drawdown(equity)

    final_equity = float(equity.iloc[-1]) if not equity.empty else result.initial_capital
    total_return = (final_equity / result.initial_capital - 1.0) * 100.0
    annual = cagr(equity)

    rets = equity.pct_change().dropna()
    volatility = (
        float(rets.std(ddof=1) * math.sqrt(periods_per_year) * 100.0) if len(rets) > 1 else 0.0
    )

    exposure = 0.0
    if result.equity_curve:
        in_market = sum(1 for p in result.equity_curve if p.positions_value > 0)
        exposure = in_market / len(result.equity_curve) * 100.0

    report = PerformanceReport(
        strategy=result.strategy,
        initial_capital=result.initial_capital,
        final_equity=final_equity,
        total_return_pct=total_return,
        cagr_pct=annual * 100.0,
        volatility_pct=volatility,
        sharpe=sharpe_ratio(equity, risk_free_rate, periods_per_year),
        sortino=sortino_ratio(equity, risk_free_rate, periods_per_year),
        max_drawdown_pct=max_dd * 100.0,
        max_drawdown_days=dd_days,
        calmar=annual / max_dd if max_dd > 0 else 0.0,
        num_trades=stats["num_trades"],
        win_rate_pct=stats["win_rate"],
        profit_factor=stats["profit_factor"],
        expectancy=stats["expectancy"],
        avg_win=stats["avg_win"],
        avg_loss=stats["avg_loss"],
        payoff_ratio=stats["payoff_ratio"],
        best_trade=stats["best"],
        worst_trade=stats["worst"],
        avg_holding_days=stats["avg_holding_days"],
        total_fees=total_fees,
        exposure_pct=exposure,
        rejected_signals=result.rejected_signals,
    )
    report.warnings = _collect_warnings(report, result)
    return report
