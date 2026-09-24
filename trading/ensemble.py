"""Diverzifikace — jediný oběd zdarma, který na trhu existuje.

Když zprůměrujete N **nekorelovaných** strategií se stejnou volatilitou,
výnos zůstane a volatilita klesne √N-krát. Sharpe se tím vynásobí √N.
Grinoldův fundamentální zákon aktivní správy říká totéž obecněji:

    IR = IC · √breadth

Informační koeficient (schopnost předpovídat) roste těžko — je to boj proti
zbytku trhu. Breadth (počet nezávislých sázek) roste snadno: víc titulů, víc
tříd aktiv, víc rodin signálů. Pět strategií se Sharpe 0,4, které spolu
nekorelují, dá dohromady Sharpe 0,89. To je ta nejlevnější matematika
v celém oboru.

Háček je ve slově *nezávislých*. Pět variant klouzavého průměru není pět
sázek, je to jedna sázka pětkrát. Proto tenhle modul kromě vážení počítá
i diagnostiku: korelace, efektivní počet sázek a diverzifikační poměr.

Rodiny signálů, které podle literatury korelují slabě nebo záporně:
hodnota vs. momentum (korelace kolem −0,4 až −0,7), trend-following vs.
mean-reversion, různé časové horizonty.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .models import HOLD, Signal, SignalType
from .strategies import BarContext, Strategy

OHLCV = ("open", "high", "low", "close", "volume", "_atr")


# --- diagnostika --------------------------------------------------------


def _with_unit_diagonal(corr: pd.DataFrame) -> pd.DataFrame:
    """Kopie korelační matice s jedničkami na diagonále.

    Konstantní sloupec dá po ``fillna`` nulu i na diagonále, což rozbije
    vzdálenostní matici pro shlukování.
    """
    # pandas 3 vrací z .values pohled jen pro čtení, proto přes numpy kopii.
    matrix = corr.to_numpy(dtype=float, copy=True)
    np.fill_diagonal(matrix, 1.0)
    return pd.DataFrame(matrix, index=corr.index, columns=corr.columns)


def average_correlation(corr: pd.DataFrame) -> float:
    """Průměrná párová korelace (mimo diagonálu)."""
    n = corr.shape[0]
    if n < 2:
        return 0.0
    mask = ~np.eye(n, dtype=bool)
    return float(np.nanmean(corr.to_numpy()[mask]))


def diversification_ratio(weights: pd.Series, cov: pd.DataFrame) -> float:
    """Vážený součet volatilit děleno volatilita portfolia.

    Hodnota 1,0 znamená žádnou diverzifikaci (všechno se hýbe společně).
    Čím vyšší, tím líp. Nad 2,0 je u sady obchodních strategií velmi dobré.
    """
    w = weights.reindex(cov.index).fillna(0.0).to_numpy()
    vols = np.sqrt(np.diag(cov.to_numpy()))
    portfolio_vol = math.sqrt(max(w @ cov.to_numpy() @ w, 1e-18))
    return float((w @ vols) / portfolio_vol)


def effective_number_of_bets(weights: pd.Series, cov: pd.DataFrame) -> float:
    """Kolik **nezávislých** sázek portfolio ve skutečnosti drží.

    Počítá se jako druhá mocnina diverzifikačního poměru. Kontrola na dvou
    krajních případech: N nezávislých složek se stejnou volatilitou dá
    přesně N, N dokonale korelovaných dá 1.

    Deset strategií s efektivním počtem sázek 1,8 znamená, že jich devět
    dělá skoro totéž — a že √N zisk, se kterým počítáte, nedostanete.

    Proč ne přes hlavní komponenty: rozklad rizika do vlastních směrů
    kovarianční matice vypadá elegantněji, ale u blízkých vlastních čísel
    je vlastní báze prakticky libovolná, takže výsledek řídí šum. Zrovna
    u nezávislých složek — kde je odpověď nejjasnější — dává ta varianta
    nejrozkolísanější čísla. Diverzifikační poměr žádnou volbu báze
    neobsahuje.
    """
    return float(diversification_ratio(weights, cov) ** 2)


def blend_sharpe(sharpes: np.ndarray | list[float], corr: pd.DataFrame | np.ndarray) -> float:
    """Teoretický Sharpe rovnoměrné směsi při dané korelaci.

        SR_blend = mean(SR) · N / √(1ᵀ C 1)

    Za předpokladu stejných volatilit. Užitečné pro odhad *předem*: kolik
    Sharpe by přinesla další strategie, když víme, jak koreluje se stávajícími.
    """
    sr = np.asarray(sharpes, dtype=float)
    c = corr.to_numpy() if isinstance(corr, pd.DataFrame) else np.asarray(corr, dtype=float)
    n = len(sr)
    if n == 0:
        return 0.0
    if n == 1:
        return float(sr[0])
    ones = np.ones(n)
    denominator = math.sqrt(max(ones @ c @ ones, 1e-18))
    return float(sr.mean() * n / denominator)


# --- schémata vážení ----------------------------------------------------


def equal_weights(returns: pd.DataFrame) -> pd.Series:
    """Rovnoměrné váhy 1/N.

    Nudné a překvapivě těžko porazitelné. DeMiguel a spol. ukázali, že
    optimalizované portfolio často 1/N nepřekoná, protože chyba v odhadu
    vstupů převáží teoretickou výhodu.
    """
    n = returns.shape[1]
    return pd.Series(1.0 / n, index=returns.columns)


def inverse_volatility_weights(returns: pd.DataFrame) -> pd.Series:
    """Váhy nepřímo úměrné volatilitě — každá strategie přispěje stejným rizikem.

    Optimální, když mají složky podobný Sharpe a podobné vzájemné korelace.
    """
    vols = returns.std(ddof=1)
    inverse = 1.0 / vols.replace(0.0, np.nan)
    inverse = inverse.fillna(0.0)
    total = inverse.sum()
    if total <= 0:
        return equal_weights(returns)
    return inverse / total


def _cluster_variance(cov: pd.DataFrame, items: list) -> float:
    """Rozptyl podshluku při vážení nepřímo úměrném rozptylu."""
    sub = cov.loc[items, items]
    diagonal = np.diag(sub.to_numpy())
    with np.errstate(divide="ignore"):
        ivp = np.where(diagonal > 0, 1.0 / diagonal, 0.0)
    total = ivp.sum()
    ivp = np.full(len(items), 1.0 / len(items)) if total <= 0 else ivp / total
    return float(ivp @ sub.to_numpy() @ ivp)


def _single_linkage_order(dist: pd.DataFrame) -> list:
    """Pořadí listů po aglomerativním shlukování metodou nejbližšího souseda.

    Vlastní implementace, ať projekt nepotřebuje scipy. Pro desítky strategií
    je kvadratická složitost naprosto dostačující.
    """
    labels = list(dist.index)
    clusters: list[list] = [[label] for label in labels]
    matrix = dist.to_numpy(dtype=float).copy()
    np.fill_diagonal(matrix, np.inf)

    while len(clusters) > 1:
        i, j = np.unravel_index(np.argmin(matrix), matrix.shape)
        i, j = (int(i), int(j)) if i < j else (int(j), int(i))

        clusters[i] = clusters[i] + clusters[j]
        del clusters[j]

        # Nejbližší soused: vzdálenost ke sloučenému shluku je minimum obou.
        matrix[i, :] = np.minimum(matrix[i, :], matrix[j, :])
        matrix[:, i] = matrix[i, :]
        matrix[i, i] = np.inf
        matrix = np.delete(np.delete(matrix, j, axis=0), j, axis=1)

    return clusters[0]


def hierarchical_risk_parity_weights(returns: pd.DataFrame) -> pd.Series:
    """Hierarchical Risk Parity (López de Prado, 2016).

    Klasická Markowitzova optimalizace potřebuje invertovat kovarianční
    matici. Ta je u podobných strategií skoro singulární, takže drobná chyba
    v odhadu vyrobí obrovské a nesmyslné váhy. Portfolio pak má nejnižší
    volatilitu v backtestu a jednu z nejvyšších v realitě.

    HRP se inverzi vyhne: strategie seřadí podle korelačního stromu a rekurzivně
    dělí kapitál mezi větve podle jejich rizika. Nepotřebuje odhad výnosů, což
    je zdaleka nejšumovější vstup ze všech.

    Poznámka: HRP není univerzálně nejlepší. Ve srovnávacích studiích ho
    prosté 1/N někdy porazí. Když je strategií málo a jsou si podobné, začněte
    rovnoměrnými váhami.
    """
    if returns.shape[1] == 1:
        return pd.Series(1.0, index=returns.columns)

    cov = returns.cov()
    corr = _with_unit_diagonal(returns.corr().fillna(0.0))

    distance = ((1.0 - corr) / 2.0).clip(lower=0.0) ** 0.5
    order = _single_linkage_order(distance)

    weights = pd.Series(1.0, index=order, dtype=float)
    clusters: list[list] = [order]

    while clusters:
        children: list[list] = []
        for cluster in clusters:
            if len(cluster) <= 1:
                continue
            mid = len(cluster) // 2
            left, right = cluster[:mid], cluster[mid:]

            var_left = _cluster_variance(cov, left)
            var_right = _cluster_variance(cov, right)
            total = var_left + var_right
            alpha = 1.0 - var_left / total if total > 0 else 0.5

            weights[left] *= alpha
            weights[right] *= 1.0 - alpha
            children.extend([left, right])
        clusters = children

    return weights.reindex(returns.columns).fillna(0.0)


WEIGHT_SCHEMES = {
    "equal": equal_weights,
    "inverse_vol": inverse_volatility_weights,
    "hrp": hierarchical_risk_parity_weights,
}


# --- souhrn -------------------------------------------------------------


@dataclass
class BlendReport:
    """Diagnostika směsi strategií."""

    weights: pd.Series
    component_sharpes: pd.Series
    blended_sharpe: float
    best_component_sharpe: float
    average_correlation: float
    effective_bets: float
    diversification_ratio: float
    theoretical_max_sharpe: float
    """Sharpe, kterého by směs dosáhla, kdyby složky nekorelovaly vůbec."""

    @property
    def sharpe_gain(self) -> float:
        """Kolikrát je směs lepší než její nejlepší jednotlivá složka."""
        if self.best_component_sharpe <= 0:
            return 0.0
        return self.blended_sharpe / self.best_component_sharpe

    def format(self) -> str:
        lines = [
            f"  {'Sharpe směsi':<30}{self.blended_sharpe:>10.2f}",
            f"  {'Nejlepší jednotlivá složka':<30}{self.best_component_sharpe:>10.2f}",
            f"  {'Zlepšení':<30}{self.sharpe_gain:>9.2f}x",
            f"  {'Strop při nulové korelaci':<30}{self.theoretical_max_sharpe:>10.2f}",
            "",
            f"  {'Průměrná korelace':<30}{self.average_correlation:>10.2f}",
            f"  {'Efektivní počet sázek':<30}{self.effective_bets:>10.2f}"
            f"  z {len(self.weights)}",
            f"  {'Diverzifikační poměr':<30}{self.diversification_ratio:>10.2f}",
            "",
            "  Váhy:",
        ]
        for name, weight in self.weights.sort_values(ascending=False).items():
            lines.append(f"    {str(name):<28}{weight:>10.1%}")

        if self.average_correlation > 0.7:
            lines.append("")
            lines.append(
                "  ! Složky jsou silně korelované — držíte jednu sázku vícekrát,"
            )
            lines.append("    ne portfolio. Přidejte jinou rodinu signálů, ne další parametr.")
        return "\n".join(lines)


def blend(
    returns: pd.DataFrame,
    scheme: str = "inverse_vol",
    periods_per_year: int = 252,
) -> tuple[pd.Series, BlendReport]:
    """Zkombinuje výnosy strategií a vrátí (výnosy směsi, diagnostiku).

    ``returns``: sloupec = strategie, řádek = období.
    """
    if scheme not in WEIGHT_SCHEMES:
        raise ValueError(f"neznámé schéma {scheme!r}; dostupné: {', '.join(WEIGHT_SCHEMES)}")

    data = returns.dropna(how="all").fillna(0.0)
    if data.shape[1] == 0:
        raise ValueError("žádné strategie ke kombinaci")

    weights = WEIGHT_SCHEMES[scheme](data)
    blended = (data * weights).sum(axis=1)

    annual = math.sqrt(periods_per_year)
    stds = data.std(ddof=1).replace(0.0, np.nan)
    component_sharpes = (data.mean() / stds * annual).fillna(0.0)

    blended_std = blended.std(ddof=1)
    blended_sharpe = float(blended.mean() / blended_std * annual) if blended_std > 0 else 0.0

    corr = _with_unit_diagonal(data.corr().fillna(0.0))
    cov = data.cov()

    n = data.shape[1]
    identity = pd.DataFrame(np.eye(n), index=corr.index, columns=corr.columns)

    report = BlendReport(
        weights=weights,
        component_sharpes=component_sharpes,
        blended_sharpe=blended_sharpe,
        best_component_sharpe=float(component_sharpes.max()),
        average_correlation=average_correlation(corr),
        effective_bets=effective_number_of_bets(weights, cov),
        diversification_ratio=diversification_ratio(weights, cov),
        theoretical_max_sharpe=blend_sharpe(component_sharpes.to_numpy(), identity),
    )
    return blended, report


# --- hlasovací ensemble jako strategie ----------------------------------


class _PrefixedRow(Mapping):
    """Pohled na řádek, který dílčí strategii ukáže její vlastní názvy sloupců.

    Ensemble ukládá indikátory s prefixem, aby si dvě strategie nepřepsaly
    stejně pojmenovaný sloupec. Tenhle obal prefix schová, takže dílčí
    strategie funguje beze změny.
    """

    __slots__ = ("_row", "_prefix")

    def __init__(self, row: Mapping[str, Any], prefix: str) -> None:
        self._row = row
        self._prefix = prefix

    def __getitem__(self, key: str) -> Any:
        if key in OHLCV:
            return self._row[key]
        return self._row[self._prefix + key]

    def __iter__(self) -> Iterator[str]:
        for key in self._row:
            if key in OHLCV:
                yield key
            elif key.startswith(self._prefix):
                yield key[len(self._prefix) :]

    def __len__(self) -> int:
        return sum(1 for _ in self)


class VotingEnsemble(Strategy):
    """Spojí několik strategií do jedné — vstup až při shodě více z nich.

    Síla signálu odpovídá podílu strategií, které hlasovaly pro vstup, takže
    risk manager při vlažné shodě sám zmenší pozici.

    Pozor na to, co se sem vkládá. Tři varianty klouzavého průměru nejsou
    tři nezávislé názory a hlasování mezi nimi nepřinese nic než pomalejší
    reakci. Smysl to dává u odlišných rodin signálů — trend, návrat k průměru,
    proražení kanálu — které se mýlí v jiných situacích.

    Výstup je záměrně citlivější než vstup: k prodeji stačí ``min_exit_votes``
    (výchozí 1) hlasů. Do pozice se vchází po dohodě, ven při první pochybnosti.
    """

    name = "ensemble"

    def __init__(
        self,
        strategies: list[Strategy],
        min_entry_votes: int | None = None,
        min_exit_votes: int = 1,
    ) -> None:
        if not strategies:
            raise ValueError("ensemble potřebuje aspoň jednu strategii")
        self.strategies = strategies
        # Výchozí prostá většina.
        self.min_entry_votes = min_entry_votes or (len(strategies) // 2 + 1)
        self.min_exit_votes = min_exit_votes

        if not 1 <= self.min_entry_votes <= len(strategies):
            raise ValueError(
                f"min_entry_votes musí být v <1, {len(strategies)}>, "
                f"dostal jsem {self.min_entry_votes}"
            )
        if not 1 <= self.min_exit_votes <= len(strategies):
            raise ValueError(f"min_exit_votes musí být v <1, {len(strategies)}>")

    def _prefix(self, index: int) -> str:
        return f"s{index}__"

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        base = set(df.columns)
        for i, strategy in enumerate(self.strategies):
            prepared = strategy.prepare(df)
            for column in prepared.columns:
                if column not in base:
                    out[self._prefix(i) + column] = prepared[column]
        return out

    @property
    def warmup(self) -> int:
        return max(strategy.warmup for strategy in self.strategies)

    def on_bar(self, ctx: BarContext) -> Signal:
        entry_votes: list[str] = []
        exit_votes: list[str] = []

        for i, strategy in enumerate(self.strategies):
            view = _PrefixedRow(ctx.row, self._prefix(i))
            signal = strategy.on_bar(BarContext(ctx.symbol, ctx.timestamp, view, ctx.position))
            if signal.type is SignalType.ENTER_LONG:
                entry_votes.append(strategy.name)
            elif signal.type is SignalType.EXIT_LONG:
                exit_votes.append(strategy.name)

        total = len(self.strategies)

        if ctx.in_position:
            if len(exit_votes) >= self.min_exit_votes:
                return Signal(
                    SignalType.EXIT_LONG,
                    1.0,
                    f"výstup {len(exit_votes)}/{total}: {', '.join(exit_votes)}",
                )
            return HOLD

        if len(entry_votes) >= self.min_entry_votes:
            return Signal(
                SignalType.ENTER_LONG,
                len(entry_votes) / total,
                f"shoda {len(entry_votes)}/{total}: {', '.join(entry_votes)}",
            )
        return HOLD

    def describe(self) -> str:
        parts = "+".join(s.name for s in self.strategies)
        return f"{self.name}({parts}, vstup>={self.min_entry_votes}, výstup>={self.min_exit_votes})"
