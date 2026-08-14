"""Validace, která se nedá obejít.

Backtest na celé historii je jen popis minulosti. Otázka zní, jestli by
postup, kterým jste strategii vybrali, fungoval i na datech, která jste
při výběru neviděli. Na to jsou tady dva nástroje:

**Walk-forward.** Na prvním úseku se vyladí parametry, na navazujícím se
bez úprav otestují, pak se okno posune. Sečtené out-of-sample úseky dají
equity křivku, kterou by strategie skutečně vyrobila, kdyby ji člověk takhle
provozoval. To je jediné číslo, které má smysl někomu ukazovat.

**Purging a embargo.** Když se trénovací a testovací úsek dotýkají, informace
prosakuje: obchod otevřený na konci trénovacího okna se uzavírá už v testovacím.
Purging takové překryvy odstraní a embargo přidá mezeru navíc kvůli
autokorelaci. Bez toho vypadá i náhodná strategie schopně.

Pozor na jednu věc: každý průchod walk-forwardem je další pokus. Když vyzkoušíte
200 kombinací parametrů, výsledek nejlepší z nich patří do ``deflated_sharpe_ratio``
s ``n_trials=200``, ne k obdivování.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import product

import numpy as np
import pandas as pd

from .backtest import BacktestConfig, BacktestEngine
from .metrics import equity_series
from .strategies import Strategy

logger = logging.getLogger(__name__)


# --- mřížka parametrů ---------------------------------------------------


def parameter_grid(**options) -> list[dict]:
    """Kartézský součin parametrů.

        parameter_grid(fast=[5, 10], slow=[30, 50])
        -> [{'fast': 5, 'slow': 30}, {'fast': 5, 'slow': 50}, ...]
    """
    if not options:
        return [{}]
    keys = list(options)
    return [dict(zip(keys, values, strict=True)) for values in product(*(options[k] for k in keys))]


# --- rozdělení v čase ---------------------------------------------------


@dataclass(frozen=True)
class Split:
    """Jedno rozdělení historie na trénovací a testovací část."""

    train_start: int
    train_end: int
    test_start: int
    test_end: int

    @property
    def train_slice(self) -> slice:
        return slice(self.train_start, self.train_end)

    @property
    def test_slice(self) -> slice:
        return slice(self.test_start, self.test_end)


def walk_forward_splits(
    n_rows: int,
    train_size: int,
    test_size: int,
    embargo: int = 0,
    anchored: bool = False,
) -> list[Split]:
    """Postupně posouvaná okna trénink → test.

    ``anchored=True`` nechá začátek tréninku na nule (okno se jen prodlužuje),
    což odpovídá tomu, jak se strategie provozuje v reálu: historie přibývá,
    nezahazuje se.

    ``embargo`` je počet barů zahozených mezi trénink a test.
    """
    if train_size < 2 or test_size < 1:
        raise ValueError("train_size musí být aspoň 2 a test_size aspoň 1")
    if n_rows < train_size + embargo + test_size:
        raise ValueError(
            f"málo dat: {n_rows} barů nestačí na trénink {train_size} "
            f"+ embargo {embargo} + test {test_size}"
        )

    splits: list[Split] = []
    train_start = 0
    train_end = train_size

    while True:
        test_start = train_end + embargo
        test_end = min(test_start + test_size, n_rows)
        if test_start >= n_rows or test_end - test_start < max(1, test_size // 2):
            break

        splits.append(Split(train_start, train_end, test_start, test_end))

        train_end = test_end
        if not anchored:
            train_start = max(0, train_end - train_size)

    return splits


# --- walk-forward -------------------------------------------------------


@dataclass
class WalkForwardResult:
    """Výstup walk-forward validace."""

    strategy_name: str = ""
    oos_returns: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    """Spojené out-of-sample výnosy — jediná poctivá equity křivka."""

    chosen_params: list[dict] = field(default_factory=list)
    """Parametry vybrané v každém okně. Když se divoce mění, nejde o edge."""

    is_sharpes: list[float] = field(default_factory=list)
    oos_sharpes: list[float] = field(default_factory=list)
    n_trials: int = 0
    """Kolik konfigurací se celkem vyhodnotilo — vstup pro deflated Sharpe."""

    @property
    def oos_equity(self) -> pd.Series:
        return (1.0 + self.oos_returns).cumprod()

    @property
    def parameter_stability(self) -> float:
        """Podíl oken, ve kterých vyhrály stejné parametry jako v předchozím.

        Stabilní volba znamená, že kritérium vybírá signál. Skákání sem a tam
        znamená, že vybírá šum, i kdyby OOS výsledek náhodou vyšel dobře.
        """
        if len(self.chosen_params) < 2:
            return 1.0
        # strict=False záměrně: porovnáváme sousední dvojice, druhá řada je
        # o prvek kratší.
        same = sum(
            1
            for a, b in zip(self.chosen_params, self.chosen_params[1:], strict=False)
            if a == b
        )
        return same / (len(self.chosen_params) - 1)

    @property
    def degradation(self) -> float:
        """O kolik Sharpe spadne z in-sample do out-of-sample.

        Nějaký pokles je normální. Pokles z 2,0 na 0,1 znamená, že in-sample
        číslo bylo z velké části vyladěný šum.
        """
        if not self.is_sharpes or not self.oos_sharpes:
            return 0.0
        return float(np.mean(self.is_sharpes) - np.mean(self.oos_sharpes))

    def format(self) -> str:
        is_mean = float(np.mean(self.is_sharpes)) if self.is_sharpes else 0.0
        oos_mean = float(np.mean(self.oos_sharpes)) if self.oos_sharpes else 0.0
        positive = sum(1 for s in self.oos_sharpes if s > 0)
        total = len(self.oos_sharpes) or 1
        return "\n".join(
            [
                f"  {'Oken':<30}{len(self.oos_sharpes):>10}",
                f"  {'Vyhodnocených konfigurací':<30}{self.n_trials:>10}",
                f"  {'Průměrný IS Sharpe':<30}{is_mean:>10.2f}",
                f"  {'Průměrný OOS Sharpe':<30}{oos_mean:>10.2f}",
                f"  {'Propad IS -> OOS':<30}{self.degradation:>10.2f}",
                f"  {'Kladných oken':<30}{positive:>7}/{total}",
                f"  {'Stabilita parametrů':<30}{self.parameter_stability:>10.1%}",
            ]
        )


def _run_and_score(
    strategy_factory: Callable[..., Strategy],
    params: dict,
    data: dict[str, pd.DataFrame],
    config: BacktestConfig,
) -> tuple[pd.Series, float]:
    """Spustí jeden backtest a vrátí (výnosy, Sharpe). Selhání dá prázdno."""
    try:
        engine = BacktestEngine(strategy_factory(**params), config)
        result = engine.run(data)
    except (ValueError, KeyError) as exc:
        logger.debug("konfigurace %s selhala: %s", params, exc)
        return pd.Series(dtype=float), float("-inf")

    equity = equity_series(result.equity_curve)
    if len(equity) < 3:
        return pd.Series(dtype=float), float("-inf")

    returns = equity[equity > 0].pct_change().dropna()
    std = returns.std(ddof=1)
    if len(returns) < 2 or std == 0 or not np.isfinite(std):
        return returns, float("-inf")

    return returns, float(returns.mean() / std * np.sqrt(252))


def walk_forward(
    strategy_factory: Callable[..., Strategy],
    data: dict[str, pd.DataFrame],
    grid: list[dict],
    config: BacktestConfig | None = None,
    train_size: int = 756,
    test_size: int = 252,
    embargo: int = 5,
    anchored: bool = True,
    strategy_name: str = "",
) -> WalkForwardResult:
    """Vyladí parametry na trénovacím okně a otestuje je na navazujícím.

    ``strategy_factory`` je volatelný objekt, který z parametrů vyrobí
    strategii — typicky ``lambda **p: strategies.create("sma_crossover", **p)``.

    Vrací spojené out-of-sample výnosy. Ty jsou to jediné, co o strategii
    skutečně něco vypovídá.
    """
    if not grid:
        raise ValueError("prázdná mřížka parametrů")

    config = config or BacktestConfig()
    symbols = sorted(data)
    timeline = sorted({ts for df in data.values() for ts in df.index})
    splits = walk_forward_splits(len(timeline), train_size, test_size, embargo, anchored)
    if not splits:
        raise ValueError("z dané délky historie nevzniklo žádné okno")

    result = WalkForwardResult(strategy_name=strategy_name or "walk-forward")
    oos_chunks: list[pd.Series] = []

    for split in splits:
        train_index = timeline[split.train_slice]
        test_index = timeline[split.test_slice]

        train_data = {s: data[s].loc[data[s].index.isin(train_index)] for s in symbols}

        best_params, best_sharpe = None, float("-inf")
        for params in grid:
            _, sharpe = _run_and_score(strategy_factory, params, train_data, config)
            result.n_trials += 1
            if sharpe > best_sharpe:
                best_params, best_sharpe = params, sharpe

        if best_params is None or not np.isfinite(best_sharpe):
            logger.warning("okno %s: žádná konfigurace neprošla, přeskakuji", split)
            continue

        # Test dostane i trénovací historii, aby se indikátory měly z čeho
        # rozehřát — ale započítají se jen výnosy z testovacího úseku.
        warm_index = set(train_index) | set(test_index)
        test_data = {s: data[s].loc[data[s].index.isin(warm_index)] for s in symbols}

        returns, _ = _run_and_score(strategy_factory, best_params, test_data, config)
        test_only = returns[returns.index.isin(test_index)]
        if test_only.empty:
            continue

        std = test_only.std(ddof=1)
        oos_sharpe = (
            float(test_only.mean() / std * np.sqrt(252)) if std > 0 and len(test_only) > 1 else 0.0
        )

        result.chosen_params.append(best_params)
        result.is_sharpes.append(best_sharpe)
        result.oos_sharpes.append(oos_sharpe)
        oos_chunks.append(test_only)

    if not oos_chunks:
        raise ValueError("walk-forward nevyprodukoval žádné out-of-sample výnosy")

    result.oos_returns = pd.concat(oos_chunks).sort_index()
    return result


# --- matice pro PBO -----------------------------------------------------


def configuration_returns(
    strategy_factory: Callable[..., Strategy],
    data: dict[str, pd.DataFrame],
    grid: list[dict],
    config: BacktestConfig | None = None,
) -> pd.DataFrame:
    """Výnosy všech konfigurací vedle sebe — vstup pro test přeoptimalizování.

    Sloupec = jedna kombinace parametrů, řádek = období. Předává se do
    ``stats.probability_of_backtest_overfitting``.
    """
    config = config or BacktestConfig()
    columns: dict[str, pd.Series] = {}

    for params in grid:
        returns, sharpe = _run_and_score(strategy_factory, params, data, config)
        if returns.empty or not np.isfinite(sharpe):
            continue
        label = ", ".join(f"{k}={v}" for k, v in params.items()) or "default"
        columns[label] = returns

    if len(columns) < 2:
        raise ValueError("z mřížky prošly méně než 2 konfigurace, PBO nemá co porovnat")

    return pd.DataFrame(columns).sort_index()
