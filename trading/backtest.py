"""Událostní backtest engine.

Prochází data bar po baru a nikdy nenahlédne dopředu. Konkrétně:

  1. na baru *t* se provedou příkazy zadané na baru *t-1*, a to za
     **otevírací cenu** baru *t* — protože v okamžiku rozhodnutí (close
     předchozího dne) žádnou jinou cenu obchodovat nešlo
  2. zkontrolují se stop-loss a take-profit proti low/high baru
  3. strategie dostane bar *t* a může zadat příkaz na *t+1*
  4. zapíše se equity podle závěrečných cen

Stejnou smyčku používá i živý paper trading (``trading.live``), takže
mezi testem a ostrým během není žádný jiný kód, který by se mohl chovat
odlišně.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from . import indicators as ind
from .calendar import EventCalendar
from .execution import CostModel
from .models import BacktestResult, Order, Side, SignalType
from .portfolio import InsufficientFunds, Portfolio
from .risk import RiskConfig, RiskManager
from .sizing import KellyConfig, KellySizer, VolatilityTargeter, VolTargetConfig
from .strategies import BarContext, Strategy

logger = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    """Nastavení běhu."""

    initial_capital: float = 100_000.0
    costs: CostModel = field(default_factory=CostModel)
    risk: RiskConfig = field(default_factory=RiskConfig)
    atr_window: int = 14
    risk_free_rate: float = 0.0

    periods_per_year: int = 252
    """Kolik barů má rok. 252 pro akcie, 365 pro krypto (obchoduje se i o víkendu).

    Ovlivňuje jen anualizaci metrik, ne samotné obchodování. Nechat 252
    u krypta znamená podhodnotit Sharpe o faktor 1,20.
    """
    close_at_end: bool = True
    """Uzavřít otevřené pozice na posledním baru, ať ztráta nezůstane schovaná."""

    vol_target: VolTargetConfig | None = None
    """Cílování volatility portfolia. None = vypnuto.

    Škáluje velikost nových pozic tak, aby volatilita equity křivky mířila
    na cílovou hodnotu. Podle Harveyho a spol. (2018) to u akcií zvyšuje
    Sharpe a napříč všemi třídami aktiv omezuje extrémní výnosy.
    """

    kelly: KellyConfig | None = None
    """Zlomkové Kellyho škálování podle realizovaných obchodů. None = vypnuto.

    Zapíná se až po ``min_trades`` obchodech; do té doby drží základní
    velikost pozice.
    """

    calendar: EventCalendar | None = None
    """Kalendář plánovaných událostí. None = blackout vypnutý."""

    blackout_days_before: int = 1
    """Kolik dní před událostí se neotvírají nové pozice."""

    blackout_days_after: int = 0
    """Kolik dní po události se neotvírají nové pozice."""


class BacktestEngine:
    """Spouští strategii nad historickými daty."""

    def __init__(self, strategy: Strategy, config: BacktestConfig | None = None) -> None:
        self.strategy = strategy
        self.config = config or BacktestConfig()
        self.portfolio = Portfolio(self.config.initial_capital)
        self.risk = RiskManager(self.config.risk)
        self._pending: dict[str, Order] = {}
        self._last_price: dict[str, float] = {}
        self._rejected = 0
        self._bar_index = 0
        self._cooldown_until: dict[str, int] = {}
        self._vol_targeter = (
            VolatilityTargeter(self.config.vol_target) if self.config.vol_target else None
        )
        self._kelly = KellySizer(self.config.kelly) if self.config.kelly else None
        self._last_equity: float | None = None

    # --- příprava --------------------------------------------------------

    def _prepare(self, data: dict[str, pd.DataFrame]) -> dict[str, dict]:
        """Doplní indikátory strategie i ATR pro risk manager."""
        prepared: dict[str, dict] = {}
        for symbol, df in data.items():
            if df.empty:
                logger.warning("%s: prázdná data, přeskakuji", symbol)
                continue
            enriched = self.strategy.prepare(df)
            enriched["_atr"] = ind.atr(
                enriched["high"], enriched["low"], enriched["close"], self.config.atr_window
            )
            prepared[symbol] = enriched.to_dict("index")
        if not prepared:
            raise ValueError("žádná použitelná data")
        return prepared

    # --- hlavní smyčka ---------------------------------------------------

    def run(self, data: dict[str, pd.DataFrame]) -> BacktestResult:
        """Odsimuluje strategii a vrátí výsledek."""
        prepared = self._prepare(data)
        symbols = sorted(prepared)

        timeline = sorted({ts for rows in prepared.values() for ts in rows})
        warmup = max(self.strategy.warmup, self.config.atr_window + 1)
        if len(timeline) <= warmup:
            raise ValueError(
                f"příliš málo dat: {len(timeline)} barů, strategie potřebuje aspoň {warmup + 1}"
            )

        self.risk.update_equity(self.config.initial_capital)

        for i, timestamp in enumerate(timeline):
            bars = {s: prepared[s][timestamp] for s in symbols if timestamp in prepared[s]}
            if bars:
                self.process_bar(timestamp, bars, allow_new_signals=i >= warmup)

        if self.config.close_at_end and self.portfolio.positions:
            self.portfolio.close_all(
                timeline[-1],
                self._last_price,
                lambda qty, price: self.config.costs.commission(qty, price),
                reason="konec testovaného období",
            )
            # Poslední bod se přepíše, nepřidává se nový. Druhý zápis na
            # stejné časové razítko by vyrobil equity křivku s duplicitním
            # indexem a rozbil všechno, co ji spojuje podle času —
            # například skládání out-of-sample úseků ve walk-forwardu.
            if self.portfolio.equity_curve:
                self.portfolio.equity_curve.pop()
            self.portfolio.mark_to_market(timeline[-1], self._last_price)

        return BacktestResult(
            strategy=self.strategy.describe(),
            symbols=symbols,
            initial_capital=self.config.initial_capital,
            trades=self.portfolio.trades,
            equity_curve=self.portfolio.equity_curve,
            rejected_signals=self._rejected,
        )

    # --- kroky smyčky ----------------------------------------------------

    def process_bar(
        self, timestamp, bars: dict[str, dict], allow_new_signals: bool = True
    ) -> None:
        """Zpracuje jeden bar. Sdílené jádro backtestu i živého běhu.

        ``bars`` mapuje symbol na řádek dat obohacený o indikátory a ``_atr``.
        """
        self._bar_index += 1
        self.risk.start_day(timestamp.date(), self.portfolio.equity(self._last_price))

        self._execute_pending(timestamp, bars)
        self._check_exits(timestamp, bars)

        for symbol, row in bars.items():
            self._last_price[symbol] = float(row["close"])

        if allow_new_signals:
            self._collect_signals(timestamp, bars)

        self.portfolio.mark_to_market(timestamp, self._last_price)
        equity = self.portfolio.equity(self._last_price)
        self.risk.update_equity(equity)
        self._update_vol_target(equity)

    def _update_vol_target(self, equity: float) -> None:
        """Předá cílovači volatility dnešní výnos equity křivky."""
        if self._vol_targeter is not None and self._last_equity and self._last_equity > 0:
            self._vol_targeter.update(equity / self._last_equity - 1.0)
        self._last_equity = equity

    def _size_multiplier(self) -> float:
        """Součin škálování z cílování volatility a z Kellyho odhadu."""
        multiplier = 1.0
        if self._vol_targeter is not None:
            multiplier *= self._vol_targeter.scalar
        if self._kelly is not None:
            multiplier *= self._kelly.multiplier(self.config.risk.risk_per_trade)
        return multiplier

    def _close_position(self, symbol: str, price: float, timestamp, reason: str) -> None:
        """Uzavře pozici a zaznamená výsledek pro Kellyho odhad."""
        position = self.portfolio.get_position(symbol)
        if position is None:
            return
        risked = position.initial_risk
        fees = self.config.costs.commission(position.quantity, price)
        _, trade = self.portfolio.sell(symbol, price, fees, timestamp, reason)
        if self._kelly is not None and risked > 0:
            self._kelly.record(trade.net_pnl, risked)

    def prepare_data(self, data: dict[str, pd.DataFrame]) -> dict[str, dict]:
        """Veřejný obal nad přípravou indikátorů (používá živý běh)."""
        return self._prepare(data)

    def _execute_pending(self, timestamp, bars: dict[str, dict]) -> None:
        """Provede příkazy z předchozího baru za otevírací cenu."""
        for symbol, order in list(self._pending.items()):
            row = bars.get(symbol)
            if row is None:
                continue  # titul dnes neobchoduje; příkaz zůstává čekat
            del self._pending[symbol]

            open_price = float(row["open"])
            fill_price = self.config.costs.fill_price(order.side, open_price)
            fees = self.config.costs.commission(order.quantity, fill_price)

            if order.side is Side.BUY:
                # Stop se posouvá o stejný skluz, jaký nás potkal na vstupu,
                # aby riskovaná částka odpovídala skutečné vstupní ceně.
                shift = fill_price - open_price
                try:
                    self.portfolio.buy(
                        symbol,
                        order.quantity,
                        fill_price,
                        fees,
                        timestamp,
                        stop_loss=order.stop_loss + shift if order.stop_loss else None,
                        take_profit=order.take_profit + shift if order.take_profit else None,
                        reason=order.reason,
                        initial_risk=order.risked_amount,
                    )
                except (InsufficientFunds, ValueError) as exc:
                    self._rejected += 1
                    logger.debug("%s: nákup neproveden — %s", symbol, exc)
            elif self.portfolio.has_position(symbol):
                self._close_position(symbol, fill_price, timestamp, order.reason)

    def _check_exits(self, timestamp, bars: dict[str, dict]) -> None:
        """Vyhodnotí stop-loss / take-profit proti rozpětí baru."""
        for symbol, row in bars.items():
            position = self.portfolio.get_position(symbol)
            if position is None:
                continue

            hit = self.risk.check_stop_hit(
                position, float(row["open"]), float(row["low"]), float(row["high"])
            )
            if hit is not None:
                price, reason = hit
                fill_price = self.config.costs.fill_price(Side.SELL, price)
                self._close_position(symbol, fill_price, timestamp, reason)
                # Čekající příkaz na stejný titul je po výstupu bezpředmětný.
                self._pending.pop(symbol, None)
                if reason == "stop-loss" and self.config.risk.reentry_cooldown_bars:
                    self._cooldown_until[symbol] = (
                        self._bar_index + self.config.risk.reentry_cooldown_bars
                    )
                continue

            self.risk.update_trailing_stop(position, float(row["close"]), _atr_of(row))

    def _collect_signals(self, timestamp, bars: dict[str, dict]) -> None:
        """Zeptá se strategie na každý titul a připraví příkazy na další bar."""
        equity = self.portfolio.equity({**self._last_price})

        for symbol, row in bars.items():
            if symbol in self._pending:
                continue

            position = self.portfolio.get_position(symbol)
            signal = self.strategy.on_bar(BarContext(symbol, timestamp, row, position))
            if not signal.is_actionable:
                continue

            if signal.type is SignalType.EXIT_LONG:
                if position is not None:
                    self._pending[symbol] = Order(
                        symbol, Side.SELL, position.quantity, signal.reason
                    )
                continue

            if position is not None:
                continue

            if self._bar_index < self._cooldown_until.get(symbol, 0):
                self._rejected += 1
                continue

            blackout = self._blackout_reason(timestamp, symbol)
            if blackout is not None:
                self._rejected += 1
                logger.debug("%s: vstup odložen — %s", symbol, blackout)
                continue

            decision = self.risk.size_position(
                equity=equity,
                cash=self.portfolio.cash - self._reserved_cash(bars),
                price=float(row["close"]),
                atr=_atr_of(row),
                open_positions=self.portfolio.open_position_count + self._pending_buys(),
                strength=signal.strength,
                size_multiplier=self._size_multiplier(),
            )
            if not decision.approved:
                self._rejected += 1
                logger.debug("%s: signál zamítnut — %s", symbol, decision.rejected_reason)
                continue

            self._pending[symbol] = Order(
                symbol,
                Side.BUY,
                decision.quantity,
                signal.reason,
                stop_loss=decision.stop_loss,
                take_profit=decision.take_profit,
                risked_amount=decision.risked_amount,
            )

    # --- pomocné ---------------------------------------------------------

    def _blackout_reason(self, timestamp, symbol: str) -> str | None:
        """Brání blackout kolem plánované události dnešnímu vstupu?

        Blokuje jen **vstupy**. Otevřené pozice se dál řídí svými stopy —
        zavírat všechno před zasedáním Fedu by znamenalo platit skluz navíc
        a přijít o trendy, které přes událost prošly bez úhony.
        """
        if self.config.calendar is None:
            return None
        return self.config.calendar.blackout_reason(
            timestamp,
            symbol,
            self.config.blackout_days_before,
            self.config.blackout_days_after,
        )

    def _pending_buys(self) -> int:
        return sum(1 for o in self._pending.values() if o.side is Side.BUY)

    def _reserved_cash(self, bars: dict[str, dict]) -> float:
        """Hotovost už slíbená čekajícím nákupům.

        Bez téhle rezervy by engine na jednom baru vygeneroval pět nákupů,
        každý za "všechny volné peníze", a čtyři z nich by druhý den
        selhaly na nedostatek hotovosti.
        """
        reserved = 0.0
        for symbol, order in self._pending.items():
            if order.side is not Side.BUY:
                continue
            row = bars.get(symbol)
            price = float(row["close"]) if row else self._last_price.get(symbol, 0.0)
            reserved += order.quantity * price
        return reserved


def _atr_of(row) -> float | None:
    value = row.get("_atr")
    if value is None or pd.isna(value):
        return None
    return float(value)


def run_backtest(
    strategy: Strategy,
    data: dict[str, pd.DataFrame],
    config: BacktestConfig | None = None,
) -> tuple[BacktestResult, float]:
    """Pohodlný obal: vrátí výsledek a celkové poplatky."""
    engine = BacktestEngine(strategy, config)
    result = engine.run(data)
    return result, engine.portfolio.total_fees
