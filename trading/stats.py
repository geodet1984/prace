"""Statistika, která říká pravdu o backtestu.

Sullivan, Timmermann a White (1999) prohnali 7846 technických pravidel přes
Dow Jones a S&P 500 s korekcí na data snooping a nenašli **žádné** ziskové.
Nešlo o to, že by pravidla v backtestu nevydělávala — vydělávala. Šlo o to,
že když vyzkoušíte tisíce variant, nejlepší z nich vypadá skvěle čirou
náhodou.

Z toho plyne nepříjemný závěr: schopnost vlastní strategii **zamítnout** má
větší hodnotu než další indikátor. Tenhle modul je na to.

Obsahuje:
  * Probabilistic Sharpe Ratio — jaká je pravděpodobnost, že skutečný Sharpe
    je nad prahem, se zohledněním šikmosti a špičatosti výnosů
  * Deflated Sharpe Ratio — totéž po korekci na počet vyzkoušených variant
  * Minimum Track Record Length — jak dlouhá historie je vůbec potřeba
  * Probability of Backtest Overfitting — přes CSCV
  * blokový bootstrap — intervaly spolehlivosti pro Sharpe

Reference: Bailey & López de Prado (2014), *The Deflated Sharpe Ratio*;
Bailey, Borwein, López de Prado & Zhu (2015), *The Probability of Backtest
Overfitting*.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations
from statistics import NormalDist

import numpy as np
import pandas as pd

TRADING_DAYS = 252
EULER_MASCHERONI = 0.5772156649015329

MIN_STD = 1e-12
"""Pod tuhle směrodatnou odchylku se řada považuje za konstantní.

Přesná nula z plovoucí čárky nevyjde skoro nikdy: konstantní řada dá std
řádu 1e-18 a podíl průměru a takového čísla vyrobí Sharpe v řádu 10¹⁵.
Reálné denní výnosy mají std nejméně 1e-5, takže tahle mez nic užitečného
neodfiltruje."""

_NORM = NormalDist()


def _cdf(x: float) -> float:
    return _NORM.cdf(x)


def _ppf(p: float) -> float:
    # NormalDist.inv_cdf není definovaná v 0 a 1; ořízneme na použitelný rozsah.
    return _NORM.inv_cdf(min(max(p, 1e-12), 1 - 1e-12))


def _as_array(returns) -> np.ndarray:
    values = np.asarray(pd.Series(returns).dropna(), dtype=float)
    return values[np.isfinite(values)]


# --- základní Sharpe ----------------------------------------------------


def period_sharpe(
    returns, risk_free_rate: float = 0.0, periods_per_year: int = TRADING_DAYS
) -> float:
    """Sharpe za jedno období (den), tedy **neanualizovaný**.

    Vzorce PSR a DSR pracují s výnosy za období, ne s ročním číslem. Míchání
    obojího je snadná a tichá chyba, proto to má vlastní funkci.
    """
    values = _as_array(returns)
    if len(values) < 2:
        return 0.0
    excess = values - risk_free_rate / periods_per_year
    std = excess.std(ddof=1)
    if not np.isfinite(std) or std < MIN_STD:
        return 0.0
    return float(excess.mean() / std)


def annualize_sharpe(sr_period: float, periods_per_year: int = TRADING_DAYS) -> float:
    return sr_period * math.sqrt(periods_per_year)


def deannualize_sharpe(sr_annual: float, periods_per_year: int = TRADING_DAYS) -> float:
    return sr_annual / math.sqrt(periods_per_year)


def _moments(values: np.ndarray) -> tuple[float, float]:
    """Šikmost a **nesnížená** špičatost (normální rozdělení má 3, ne 0)."""
    n = len(values)
    if n < 4:
        return 0.0, 3.0
    mean = values.mean()
    std = values.std(ddof=0)
    if std == 0:
        return 0.0, 3.0
    skew = float(((values - mean) ** 3).mean() / std**3)
    kurt = float(((values - mean) ** 4).mean() / std**4)
    return skew, kurt


# --- Probabilistic Sharpe Ratio -----------------------------------------


def probabilistic_sharpe_ratio(
    returns,
    benchmark_sharpe: float = 0.0,
    periods_per_year: int = TRADING_DAYS,
    risk_free_rate: float = 0.0,
) -> float:
    """Pravděpodobnost, že skutečný Sharpe převyšuje ``benchmark_sharpe``.

    ``benchmark_sharpe`` se zadává **anualizovaně** (0.5 = Sharpe 0,5 ročně).

    Vzorec (Bailey & López de Prado):

        PSR(SR*) = Φ( (SR - SR*)·√(T-1) / √(1 - γ₃·SR + (γ₄-1)/4·SR²) )

    kde SR i SR* jsou za jedno období, γ₃ šikmost a γ₄ špičatost.

    Záporná šikmost a vysoká špičatost — tedy přesně to, co dělají reálné
    výnosy strategií s prodejem opcí nebo s vzácnými velkými ztrátami —
    jmenovatel zvětšují, a tím pravděpodobnost snižují. Samotný Sharpe je
    přitom neovlivní vůbec. Proto se dvěma stejným Sharpe nedá věřit stejně.
    """
    values = _as_array(returns)
    n = len(values)
    if n < 4:
        return 0.0

    sr = period_sharpe(values, risk_free_rate, periods_per_year)
    sr_star = deannualize_sharpe(benchmark_sharpe, periods_per_year)
    skew, kurt = _moments(values)

    variance = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr**2
    if variance <= 0:
        return 0.0

    z = (sr - sr_star) * math.sqrt(n - 1) / math.sqrt(variance)
    return _cdf(z)


def min_track_record_length(
    returns,
    benchmark_sharpe: float = 0.0,
    confidence: float = 0.95,
    periods_per_year: int = TRADING_DAYS,
    risk_free_rate: float = 0.0,
) -> float:
    """Kolik období historie je potřeba, aby byl Sharpe průkazně nad prahem.

    Vrací ``inf``, když pozorovaný Sharpe práh vůbec nepřevyšuje — pak žádná
    délka historie nepomůže.

        MinTRL = 1 + [1 - γ₃·SR + (γ₄-1)/4·SR²] · (Z_α / (SR - SR*))²
    """
    values = _as_array(returns)
    if len(values) < 4:
        return float("inf")

    sr = period_sharpe(values, risk_free_rate, periods_per_year)
    sr_star = deannualize_sharpe(benchmark_sharpe, periods_per_year)
    if sr <= sr_star:
        return float("inf")

    skew, kurt = _moments(values)
    variance = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr**2
    z = _ppf(confidence)
    return 1.0 + variance * (z / (sr - sr_star)) ** 2


# --- Deflated Sharpe Ratio ----------------------------------------------


def expected_max_sharpe(n_trials: int, sharpe_std: float, mean_sharpe: float = 0.0) -> float:
    """Očekávaný nejlepší Sharpe z ``n_trials`` pokusů **bez jakékoli schopnosti**.

    Tohle je ta laťka, kterou musí strategie přeskočit. Když vyzkoušíte 100
    variant a Sharpe jednotlivých pokusů kolísá se směrodatnou odchylkou 0,5,
    nejlepší z nich bude mít Sharpe kolem 1,3 — čistě náhodou, i kdyby žádná
    z nich neuměla vůbec nic.

        E[max SR] ≈ μ + σ·[(1-γ)·Φ⁻¹(1 - 1/N) + γ·Φ⁻¹(1 - 1/(N·e))]

    kde γ je Eulerova–Mascheroniho konstanta. Hodnoty jsou ve stejné
    frekvenci jako ``sharpe_std``.
    """
    if n_trials < 1:
        raise ValueError("n_trials musí být aspoň 1")
    if n_trials == 1 or sharpe_std <= 0:
        return mean_sharpe

    z1 = _ppf(1.0 - 1.0 / n_trials)
    z2 = _ppf(1.0 - 1.0 / (n_trials * math.e))
    return mean_sharpe + sharpe_std * ((1.0 - EULER_MASCHERONI) * z1 + EULER_MASCHERONI * z2)


@dataclass
class DeflatedSharpeResult:
    """Výsledek deflace Sharpe ratia."""

    sharpe_annual: float
    """Pozorovaný anualizovaný Sharpe."""

    benchmark_annual: float
    """Laťka: očekávaný nejlepší Sharpe z čirého šumu při daném počtu pokusů."""

    deflated: float
    """Pravděpodobnost, že strategie je nad laťkou. Pod 0.95 = nepřesvědčivé."""

    n_trials: int
    n_observations: int
    skew: float
    kurtosis: float

    @property
    def is_significant(self) -> bool:
        """Obvyklý práh — DSR nad 0,95."""
        return self.deflated >= 0.95

    def format(self) -> str:
        verdict = "PRŮKAZNÉ" if self.is_significant else "NEPRŮKAZNÉ"
        return "\n".join(
            [
                f"  Pozorovaný Sharpe        {self.sharpe_annual:>10.2f}",
                f"  Laťka z {self.n_trials:>4} pokusů      {self.benchmark_annual:>10.2f}",
                f"  Deflated Sharpe Ratio    {self.deflated:>10.3f}   {verdict}",
                f"  Pozorování               {self.n_observations:>10}",
                f"  Šikmost / špičatost      {self.skew:>10.2f} / {self.kurtosis:.2f}",
            ]
        )


def deflated_sharpe_ratio(
    returns,
    n_trials: int,
    trial_sharpes=None,
    sharpe_std: float | None = None,
    periods_per_year: int = TRADING_DAYS,
    risk_free_rate: float = 0.0,
) -> DeflatedSharpeResult:
    """Sharpe po korekci na počet vyzkoušených variant.

    Je to prostě PSR, kde prahem není nula, ale očekávané maximum z ``n_trials``
    náhodných pokusů. Odpovídá na otázku „je tenhle výsledek lepší, než co bych
    dostal, kdybych zkoušel stejně dlouho a neuměl nic?".

    ``n_trials`` musí být **poctivé** číslo: kolik kombinací parametrů,
    strategií a variant jste ve skutečnosti vyzkoušeli, včetně těch
    zahozených. Podhodnotit ho znamená obelhat sám sebe.

    Rozptyl Sharpe napříč pokusy se odhadne z ``trial_sharpes`` (anualizované
    hodnoty jednotlivých pokusů), nebo se zadá přímo přes ``sharpe_std``.
    Když není ani jedno, použije se konzervativní odhad 1/√T.
    """
    values = _as_array(returns)
    n = len(values)
    if n < 4:
        raise ValueError(f"příliš málo pozorování: {n}")

    sr_period = period_sharpe(values, risk_free_rate, periods_per_year)
    skew, kurt = _moments(values)

    if trial_sharpes is not None:
        trials = np.asarray([deannualize_sharpe(s, periods_per_year) for s in trial_sharpes])
        std_period = float(trials.std(ddof=1)) if len(trials) > 1 else 0.0
    elif sharpe_std is not None:
        std_period = deannualize_sharpe(sharpe_std, periods_per_year)
    else:
        # Bez informace o rozptylu pokusů použijeme asymptotickou chybu
        # odhadu Sharpe, 1/√T. Je to spodní odhad, takže laťka vyjde nízko
        # a výsledek je spíš optimistický než přísný.
        std_period = 1.0 / math.sqrt(n)

    sr0_period = expected_max_sharpe(n_trials, std_period)

    variance = 1.0 - skew * sr_period + (kurt - 1.0) / 4.0 * sr_period**2
    deflated = 0.0
    if variance > 0:
        z = (sr_period - sr0_period) * math.sqrt(n - 1) / math.sqrt(variance)
        deflated = _cdf(z)

    return DeflatedSharpeResult(
        sharpe_annual=annualize_sharpe(sr_period, periods_per_year),
        benchmark_annual=annualize_sharpe(sr0_period, periods_per_year),
        deflated=deflated,
        n_trials=n_trials,
        n_observations=n,
        skew=skew,
        kurtosis=kurt,
    )


# --- Probability of Backtest Overfitting --------------------------------


@dataclass
class PBOResult:
    """Výsledek CSCV testu přeoptimalizování."""

    pbo: float
    """Pravděpodobnost, že vítěz z in-sample skončí out-of-sample pod mediánem."""

    n_configurations: int
    n_splits: int
    logits: list[float]
    oos_sharpes: list[float]
    """OOS Sharpe vítěze v každé kombinaci — ukazuje, o kolik výsledek spadne."""

    @property
    def is_acceptable(self) -> bool:
        """Pod 0,5 znamená, že výběr je aspoň lepší než hod mincí."""
        return self.pbo < 0.5

    def format(self) -> str:
        median_oos = float(np.median(self.oos_sharpes)) if self.oos_sharpes else 0.0
        if self.pbo < 0.25:
            verdict = "v pořádku"
        elif self.pbo < 0.5:
            verdict = "hraniční"
        else:
            verdict = "PŘEOPTIMALIZOVÁNO"
        return "\n".join(
            [
                f"  Testovaných konfigurací  {self.n_configurations:>10}",
                f"  Kombinací rozdělení      {len(self.logits):>10}",
                f"  PBO                      {self.pbo:>10.1%}   {verdict}",
                f"  Medián OOS Sharpe vítěze {median_oos:>10.2f}",
            ]
        )


def probability_of_backtest_overfitting(
    returns_matrix: pd.DataFrame, n_splits: int = 16
) -> PBOResult:
    """CSCV: jak často vítěz z in-sample propadne out-of-sample.

    Postup (Bailey, Borwein, López de Prado & Zhu):

      1. rozděl historii na S stejných částí
      2. projdi všechny způsoby, jak vybrat S/2 částí jako in-sample
      3. v každé vyber konfiguraci s nejlepším IS Sharpe
      4. podívej se, kde skončila na zbytku dat (out-of-sample)
      5. PBO = podíl případů, kdy skončila pod OOS mediánem

    Poctivý postup výběru by měl dát PBO výrazně pod 0,5. Hodnota kolem 0,5
    znamená, že vaše kritérium výběru nemá s budoucí výkonností nic
    společného — vybíráte šum.

    ``returns_matrix``: sloupec = jedna konfigurace strategie, řádek = období.
    """
    if n_splits % 2 != 0:
        raise ValueError("n_splits musí být sudé")
    if returns_matrix.shape[1] < 2:
        raise ValueError("potřeba aspoň 2 konfigurace k porovnání")

    data = returns_matrix.dropna(how="all").fillna(0.0)
    n_rows = len(data)
    if n_rows < n_splits * 2:
        raise ValueError(f"málo pozorování ({n_rows}) na {n_splits} částí")

    chunks = np.array_split(np.arange(n_rows), n_splits)
    half = n_splits // 2

    logits: list[float] = []
    oos_sharpes: list[float] = []

    for is_parts in combinations(range(n_splits), half):
        oos_parts = [i for i in range(n_splits) if i not in is_parts]
        is_rows = np.concatenate([chunks[i] for i in is_parts])
        oos_rows = np.concatenate([chunks[i] for i in oos_parts])

        is_sharpes = _column_sharpes(data.iloc[is_rows])
        oos_all = _column_sharpes(data.iloc[oos_rows])

        best = int(np.nanargmax(is_sharpes))
        oos_sharpes.append(float(oos_all[best]))

        # Relativní pořadí vítěze mezi OOS výsledky, v (0, 1).
        rank = float((oos_all < oos_all[best]).sum() + 1) / (len(oos_all) + 1)
        rank = min(max(rank, 1e-6), 1 - 1e-6)
        logits.append(math.log(rank / (1.0 - rank)))

    pbo = float(np.mean([1.0 if value <= 0 else 0.0 for value in logits]))

    return PBOResult(
        pbo=pbo,
        n_configurations=data.shape[1],
        n_splits=n_splits,
        logits=logits,
        oos_sharpes=oos_sharpes,
    )


def _column_sharpes(frame: pd.DataFrame) -> np.ndarray:
    """Sharpe každého sloupce; konstantní sloupec dostane -inf, ne NaN."""
    means = frame.mean(axis=0).to_numpy(dtype=float)
    stds = frame.std(axis=0, ddof=1).to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(stds > MIN_STD, means / stds, -np.inf)
    return np.nan_to_num(out, nan=-np.inf, neginf=-np.inf)


# --- bootstrap ----------------------------------------------------------


def block_bootstrap_sharpe(
    returns,
    n_samples: int = 2000,
    block_size: int | None = None,
    confidence: float = 0.95,
    periods_per_year: int = TRADING_DAYS,
    seed: int = 0,
) -> tuple[float, float, float]:
    """Interval spolehlivosti pro anualizovaný Sharpe blokovým bootstrapem.

    Bloky (ne jednotlivé dny) proto, že výnosy strategií jsou autokorelované —
    obyčejný bootstrap tuhle strukturu zničí a interval vyjde falešně úzký.

    Vrací (dolní mez, bodový odhad, horní mez). Když interval obsahuje nulu,
    není z čeho usuzovat na schopnost.
    """
    values = _as_array(returns)
    n = len(values)
    if n < 20:
        raise ValueError(f"příliš málo pozorování pro bootstrap: {n}")

    if block_size is None:
        # Politis & Romano: délka bloku řádově n^(1/3).
        block_size = max(2, int(round(n ** (1 / 3))))

    rng = np.random.default_rng(seed)
    n_blocks = int(math.ceil(n / block_size))
    starts_max = n - block_size + 1

    samples = np.empty(n_samples, dtype=float)
    for i in range(n_samples):
        starts = rng.integers(0, starts_max, size=n_blocks)
        drawn = np.concatenate([values[s : s + block_size] for s in starts])[:n]
        std = drawn.std(ddof=1)
        samples[i] = (
            drawn.mean() / std * math.sqrt(periods_per_year) if std >= MIN_STD else 0.0
        )

    alpha = (1.0 - confidence) / 2.0
    low, high = np.quantile(samples, [alpha, 1.0 - alpha])
    point = annualize_sharpe(
        period_sharpe(values, periods_per_year=periods_per_year), periods_per_year
    )
    return float(low), float(point), float(high)


def equity_to_returns(equity: pd.Series) -> pd.Series:
    """Převede equity křivku na výnosy období. Nulové a záporné body zahodí."""
    clean = equity[equity > 0]
    return clean.pct_change().dropna()
