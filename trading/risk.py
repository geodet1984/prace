"""Řízení rizika.

Tahle vrstva rozhoduje o tom, jestli se účet přežije. Signál rozhoduje
*kdy* obchodovat, risk manager *za kolik* a *kdy to utnout*.

Použité principy:
  * fixní frakce rizika — na jeden obchod se riskuje pevné % kapitálu,
    velikost pozice se dopočítá ze vzdálenosti stop-lossu (ne naopak)
  * stop-loss podle volatility (ATR), ne podle kulatého procenta
  * strop na expozici jednoho titulu i na počet otevřených pozic
  * kill-switch na denní ztrátu a na celkový drawdown
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .models import Position

logger = logging.getLogger(__name__)


@dataclass
class RiskConfig:
    """Parametry řízení rizika.

    Výchozí hodnoty jsou konzervativní schválně. Zvyšovat ``risk_per_trade``
    nad ~0.02 znamená, že série deseti ztrát (což se stane) ukousne pětinu
    účtu.
    """

    risk_per_trade: float = 0.01
    """Podíl kapitálu riskovaný na jednom obchodu (0.01 = 1 %)."""

    stop_loss_atr_mult: float = 2.0
    """Vzdálenost stop-lossu v násobcích ATR."""

    take_profit_atr_mult: float | None = None
    """Volitelný výstupní cíl v násobcích ATR. None = nechat běžet."""

    trailing_stop_atr_mult: float | None = None
    """Volitelný posuvný stop v násobcích ATR od nejvyššího dosaženého close."""

    max_position_pct: float = 0.25
    """Maximální podíl kapitálu v jednom titulu."""

    max_open_positions: int = 5
    """Maximální počet současně otevřených pozic."""

    max_daily_loss_pct: float | None = 0.03
    """Denní stop účtu: po překročení se ten den už neotvírají pozice."""

    max_drawdown_pct: float | None = 0.20
    """Kill-switch: propad od maxima, po kterém se přestává obchodovat."""

    min_position_value: float = 50.0
    """Pod tuhle hodnotu se pozice neotvírá — poplatky by ji sežraly."""

    reentry_cooldown_bars: int = 0
    """Kolik barů po vyražení stop-lossem se do stejného titulu nevstupuje.

    Nula znamená, že strategie smí vstoupit hned. U signálů založených na
    stavu (a ne na události) je nenulová pauza důležitá — jinak se pozice
    den po stopu obnoví a ochrana kapitálu je jen na papíře.
    """

    allow_fractional: bool = False
    """Povolit zlomkové akcie (podporuje např. Alpaca)."""

    def __post_init__(self) -> None:
        if not 0 < self.risk_per_trade <= 1:
            raise ValueError("risk_per_trade musí být v (0, 1]")
        if self.stop_loss_atr_mult <= 0:
            raise ValueError("stop_loss_atr_mult musí být kladný")
        if not 0 < self.max_position_pct <= 1:
            raise ValueError("max_position_pct musí být v (0, 1]")
        if self.max_open_positions < 1:
            raise ValueError("max_open_positions musí být aspoň 1")


@dataclass(frozen=True)
class SizingDecision:
    """Výsledek dotazu na velikost pozice."""

    quantity: float
    stop_loss: float | None
    take_profit: float | None
    rejected_reason: str | None = None
    risked_amount: float = 0.0
    """Kolik peněz je v sázce, dojde-li na stop. Vstup pro Kellyho odhad."""

    @property
    def approved(self) -> bool:
        return self.quantity > 0 and self.rejected_reason is None


REJECTED = SizingDecision(0.0, None, None)


class RiskManager:
    """Vyhodnocuje velikost pozice a hlídá limity účtu."""

    def __init__(self, config: RiskConfig | None = None) -> None:
        self.config = config or RiskConfig()
        self._peak_equity: float = 0.0
        self._day_start_equity: float | None = None
        self._current_day = None
        self.halted_reason: str | None = None

    # --- stav účtu -----------------------------------------------------

    def start_day(self, day, equity: float) -> None:
        """Zaznamená otevírací kapitál dne kvůli dennímu limitu ztráty."""
        if day != self._current_day:
            self._current_day = day
            self._day_start_equity = equity

    def update_equity(self, equity: float) -> None:
        """Aktualizuje vrchol equity pro výpočet drawdownu."""
        self._peak_equity = max(self._peak_equity, equity)

    @property
    def peak_equity(self) -> float:
        return self._peak_equity

    def current_drawdown(self, equity: float) -> float:
        """Aktuální propad od maxima jako kladný podíl (0.15 = -15 %)."""
        if self._peak_equity <= 0:
            return 0.0
        return max(0.0, 1.0 - equity / self._peak_equity)

    def trading_blocked(self, equity: float) -> str | None:
        """Vrátí důvod, proč se nesmí otevírat nové pozice, nebo None.

        Blokuje jen *vstupy*. Existující pozice se dál řídí svými stopy —
        panické zavření všeho na dně je spolehlivý způsob, jak z papírové
        ztráty udělat skutečnou.
        """
        cfg = self.config

        if cfg.max_drawdown_pct is not None:
            dd = self.current_drawdown(equity)
            if dd >= cfg.max_drawdown_pct:
                self.halted_reason = (
                    f"drawdown {dd:.1%} dosáhl limitu {cfg.max_drawdown_pct:.1%}"
                )
                return self.halted_reason

        if cfg.max_daily_loss_pct is not None and self._day_start_equity:
            day_loss = 1.0 - equity / self._day_start_equity
            if day_loss >= cfg.max_daily_loss_pct:
                return f"denní ztráta {day_loss:.1%} dosáhla limitu {cfg.max_daily_loss_pct:.1%}"

        return None

    # --- velikost pozice -----------------------------------------------

    def size_position(
        self,
        *,
        equity: float,
        cash: float,
        price: float,
        atr: float | None,
        open_positions: int,
        strength: float = 1.0,
        size_multiplier: float = 1.0,
    ) -> SizingDecision:
        """Spočítá velikost nové pozice a úrovně stop-loss / take-profit.

        Postup je opačný, než jak to dělá většina začátečníků: nejdřív se
        určí, kolik peněz je přijatelné ztratit, pak kde bude stop, a až
        z toho vyjde počet kusů.
        """
        cfg = self.config

        if price <= 0:
            return SizingDecision(0.0, None, None, "neplatná cena")
        if open_positions >= cfg.max_open_positions:
            return SizingDecision(0.0, None, None, f"limit {cfg.max_open_positions} pozic")

        blocked = self.trading_blocked(equity)
        if blocked:
            return SizingDecision(0.0, None, None, blocked)

        if atr is None or atr <= 0 or not _is_finite(atr):
            # Bez míry volatility nemáme jak umístit stop; radši neobchodovat.
            return SizingDecision(0.0, None, None, "chybí ATR pro výpočet stop-lossu")

        stop_distance = atr * cfg.stop_loss_atr_mult
        if stop_distance >= price:
            return SizingDecision(
                0.0, None, None, "stop-loss by byl pod nulou (extrémní volatilita)"
            )

        # ``size_multiplier`` je vstup z nadřazených vrstev — cílování
        # volatility a Kellyho škálování. Aplikuje se na riskovanou částku
        # *před* stropy na expozici a hotovost, takže žádný limit neobejde.
        risk_amount = (
            equity * cfg.risk_per_trade * _clamp(strength, 0.0, 1.0) * max(size_multiplier, 0.0)
        )
        quantity = risk_amount / stop_distance

        # Strop na expozici — i při těsném stopu nechceme celý účet v jednom titulu.
        max_by_exposure = (equity * cfg.max_position_pct) / price
        quantity = min(quantity, max_by_exposure)

        # A samozřejmě nelze koupit za peníze, které nemáme.
        max_by_cash = cash / price
        quantity = min(quantity, max_by_cash)

        if not cfg.allow_fractional:
            quantity = float(int(quantity))

        if quantity <= 0:
            return SizingDecision(0.0, None, None, "nedostatek hotovosti na celou akcii")

        if quantity * price < cfg.min_position_value:
            return SizingDecision(
                0.0, None, None, f"pozice pod minimem {cfg.min_position_value:.0f}"
            )

        stop_loss = price - stop_distance
        take_profit = (
            price + atr * cfg.take_profit_atr_mult if cfg.take_profit_atr_mult else None
        )
        return SizingDecision(
            quantity, stop_loss, take_profit, risked_amount=quantity * stop_distance
        )

    # --- výstupy z pozice ------------------------------------------------

    def update_trailing_stop(self, position: Position, close: float, atr: float | None) -> None:
        """Posune stop nahoru za rostoucí cenou. Nikdy dolů."""
        cfg = self.config
        if not cfg.trailing_stop_atr_mult or atr is None or atr <= 0:
            return
        candidate = close - atr * cfg.trailing_stop_atr_mult
        if position.stop_loss is None or candidate > position.stop_loss:
            position.stop_loss = candidate

    def check_stop_hit(
        self, position: Position, bar_open: float, bar_low: float, bar_high: float
    ) -> tuple[float, str] | None:
        """Zjistí, jestli bar aktivoval stop-loss nebo take-profit.

        Vrací (realizační cena, důvod). Mezera přes noc se řeší poctivě:
        když trh otevře pod stopem, plní se za otevírací cenu, ne za stop.
        Opačný předpoklad je nejčastější důvod, proč backtest vypadá lépe
        než realita.
        """
        stop, target = position.stop_loss, position.take_profit

        if stop is not None and bar_low <= stop:
            fill = min(bar_open, stop) if bar_open <= stop else stop
            return fill, "stop-loss"

        if target is not None and bar_high >= target:
            fill = max(bar_open, target) if bar_open >= target else target
            return fill, "take-profit"

        return None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _is_finite(value: float) -> bool:
    return value == value and value not in (float("inf"), float("-inf"))
