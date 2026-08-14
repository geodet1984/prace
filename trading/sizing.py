"""Velikost pozice: volatility targeting a Kellyho kritérium.

Dvě myšlenky, které podle dat zvedají výsledek nejvíc — a ani jedna z nich
není o tom, *co* koupit.

**Volatility targeting.** Harvey a spol. (2018) prohnali 60 aktiv s daty od
roku 1926 a zjistili, že škálování expozice na konstantní cílovou volatilitu
zvyšuje Sharpe u akcií a kreditu a u všech tříd aktiv snižuje pravděpodobnost
extrémních výnosů. U dluhopisů, měn a komodit je vliv na Sharpe zanedbatelný,
ale omezení chvostů zůstává. Funguje to přes tzv. leverage effect: volatilita
a výnosy jsou u akcií záporně korelované, takže snížení expozice při rostoucí
volatilitě má vedlejší efekt momentum overlaye.

**Kellyho kritérium.** Určuje podíl kapitálu maximalizující dlouhodobý růst.
Plné Kelly je ale optimální jen v matematické limitě, kde znáte pravděpodobnost
výhry přesně. V praxi ji odhadujete, a sázet plnou frakci na nadhodnocený edge
je učebnicový způsob, jak přijít o účet. Poloviční Kelly zachová zhruba 75 %
růstu při přibližně polovičním drawdownu — proto se v praxi používá skoro
výhradně zlomkové Kelly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

TRADING_DAYS = 252


# --- odhad volatility ---------------------------------------------------


def realized_volatility(
    returns: pd.Series, window: int = 60, periods_per_year: int = TRADING_DAYS
) -> pd.Series:
    """Anualizovaná volatilita z klouzavého okna prostých výnosů."""
    return returns.rolling(window=window, min_periods=max(2, window // 2)).std(
        ddof=1
    ) * math.sqrt(periods_per_year)


def ewma_volatility(
    returns: pd.Series, lambda_: float = 0.94, periods_per_year: int = TRADING_DAYS
) -> pd.Series:
    """Anualizovaná volatilita exponenciálním vážením (RiskMetrics).

    ``lambda_ = 0.94`` je standard J.P. Morgan pro denní data; odpovídá
    poločasu zhruba 11 dní. Reaguje na změnu režimu rychleji než prosté
    okno, což je přesně to, co má volatility targeting dělat.
    """
    if not 0 < lambda_ < 1:
        raise ValueError("lambda_ musí být v (0, 1)")
    alpha = 1.0 - lambda_
    variance = returns.pow(2).ewm(alpha=alpha, adjust=False, min_periods=10).mean()
    return variance.pow(0.5) * math.sqrt(periods_per_year)


# --- volatility targeting -----------------------------------------------


@dataclass
class VolTargetConfig:
    """Nastavení cílování volatility na úrovni portfolia."""

    target_annual_vol: float = 0.15
    """Cílová roční volatilita equity křivky (0.15 = 15 %)."""

    lambda_: float = 0.94
    """Vyhlazení EWMA odhadu volatility."""

    min_observations: int = 40
    """Než je tolik pozorování, škálování se nepoužije (odhad by byl nesmysl)."""

    max_leverage: float = 1.5
    """Strop škálovacího faktoru. Bez něj by nástroj v klidném trhu napákoval."""

    min_leverage: float = 0.2
    """Podlaha — úplně vypnout obchodování kvůli jedné bouřce nedává smysl."""

    rebalance_band: float = 0.20
    """Škálování se změní, až když se od posledního liší o víc než tenhle podíl.

    Bez pásma vyrábí denní cílování volatility velké obraty a poplatky sežerou
    přínos dřív, než se projeví.
    """

    def __post_init__(self) -> None:
        if self.target_annual_vol <= 0:
            raise ValueError("target_annual_vol musí být kladná")
        if self.max_leverage < self.min_leverage:
            raise ValueError("max_leverage musí být >= min_leverage")
        if not 0 <= self.rebalance_band < 1:
            raise ValueError("rebalance_band musí být v <0, 1)")


class VolatilityTargeter:
    """Průběžně počítá, kolikrát zvětšit nebo zmenšit expozici.

    Používá se online: každý bar se předá výnos equity křivky a nástroj vrátí
    aktuální škálovací faktor. Pracuje jen s minulostí, takže je bezpečný
    v backtestu i naživo.
    """

    def __init__(self, config: VolTargetConfig | None = None) -> None:
        self.config = config or VolTargetConfig()
        self._variance: float | None = None
        self._count = 0
        self._scalar = 1.0

    def update(self, period_return: float) -> float:
        """Zaznamená výnos období a vrátí aktuální škálovací faktor."""
        cfg = self.config

        if not math.isfinite(period_return):
            return self._scalar

        squared = period_return**2
        if self._variance is None:
            self._variance = squared
        else:
            self._variance = cfg.lambda_ * self._variance + (1.0 - cfg.lambda_) * squared
        self._count += 1

        if self._count < cfg.min_observations:
            return self._scalar

        annual_vol = math.sqrt(self._variance * TRADING_DAYS)
        target = (
            cfg.max_leverage if annual_vol <= 1e-9 else cfg.target_annual_vol / annual_vol
        )
        target = min(max(target, cfg.min_leverage), cfg.max_leverage)

        # Pásmo necitlivosti: drobné změny ignorujeme kvůli obratu.
        if self._scalar <= 0 or abs(target - self._scalar) / self._scalar > cfg.rebalance_band:
            self._scalar = target
        return self._scalar

    @property
    def scalar(self) -> float:
        return self._scalar

    @property
    def current_volatility(self) -> float | None:
        """Aktuální odhad anualizované volatility, nebo None při zahřívání."""
        if self._variance is None or self._count < self.config.min_observations:
            return None
        return math.sqrt(self._variance * TRADING_DAYS)


def vol_target_series(
    returns: pd.Series, config: VolTargetConfig | None = None
) -> pd.Series:
    """Vektorová varianta pro analýzu: škálovací faktor v každém okamžiku.

    Faktor je posunutý o jeden bar, takže se na výnos dne T aplikuje odhad
    volatility z dat do T-1 včetně.
    """
    config = config or VolTargetConfig()
    targeter = VolatilityTargeter(config)
    scalars = [targeter.update(value) for value in returns.fillna(0.0)]
    return pd.Series(scalars, index=returns.index).shift(1).fillna(1.0)


def apply_vol_target(returns: pd.Series, config: VolTargetConfig | None = None) -> pd.Series:
    """Přeškáluje řadu výnosů na cílovou volatilitu (bez transakčních nákladů).

    Slouží k rychlému odhadu, jestli má cílování u dané strategie smysl.
    Skutečný přínos bude nižší — reálné přeskládání něco stojí.
    """
    return returns * vol_target_series(returns, config)


# --- Kellyho kritérium --------------------------------------------------


def kelly_continuous(mean_return: float, variance: float) -> float:
    """Kellyho frakce pro spojité výnosy: f* = μ / σ².

    Pro normální výnosy platí f* = SR / σ, takže Kelly je vlastně Sharpe
    přepočtený na jednotky volatility. Strategie se Sharpe 1 a volatilitou
    15 % chce podle plného Kelly páku 6,7× — a to je přesně to číslo, kvůli
    kterému se plné Kelly nepoužívá.
    """
    if variance <= 0:
        return 0.0
    return mean_return / variance


def kelly_from_sharpe(sharpe_annual: float, annual_vol: float) -> float:
    """Kellyho páka ze Sharpe a volatility: f* = SR / σ."""
    if annual_vol <= 0:
        return 0.0
    return sharpe_annual / annual_vol


def kelly_discrete(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """Kellyho frakce pro sázku se dvěma výsledky: f* = p/a − q/b.

    ``avg_win`` (b) a ``avg_loss`` (a) jsou výnos a ztráta **na jednotku
    vsazené částky**, oba jako kladná čísla. Vrací podíl kapitálu, který se
    má vsadit.

    Kontrola na známém případu: férová mince s výplatou 1:1 a 60% úspěšností
    dá f* = 0.6/1 − 0.4/1 = 0.2, tedy klasických 20 %.

    Při záporném edge vrací 0 — Kelly říká „nesázej", ne „sázej naopak";
    long-only strategii stejně obrátit nejde.
    """
    if avg_win <= 0 or avg_loss <= 0:
        return 0.0
    if not 0 <= win_rate <= 1:
        raise ValueError("win_rate musí být v <0, 1>")

    fraction = win_rate / avg_loss - (1.0 - win_rate) / avg_win
    return max(0.0, fraction)


def kelly_from_returns(returns: pd.Series) -> float:
    """Kellyho páka odhadnutá přímo z řady výnosů: f* = μ / σ².

    Výsledek nezávisí na frekvenci dat — anualizací se μ i σ² násobí stejným
    číslem a podíl se nezmění. Denní a měsíční data proto dají totéž.
    """
    values = returns.dropna()
    if len(values) < 20:
        return 0.0
    variance = float(values.var(ddof=1))
    if variance <= 0:
        return 0.0
    return float(values.mean()) / variance


@dataclass
class KellyConfig:
    """Nastavení zlomkového Kelly."""

    fraction: float = 0.25
    """Jaký podíl plného Kelly použít. 0.5 = poloviční, 0.25 = čtvrtinové.

    Poloviční Kelly zachová kolem 75 % růstu při zhruba polovičním drawdownu.
    Čtvrtinové je ještě konzervativnější a v praxi rozumný výchozí bod, protože
    odhad edge je vždy nejistý a chyba na horní straně je drahá.
    """

    min_trades: int = 30
    """Pod tolik uzavřených obchodů se Kelly nepoužije — odhad by byl šum."""

    max_multiplier: float = 2.0
    """Strop násobku základní velikosti pozice."""

    min_multiplier: float = 0.25
    """Podlaha násobku."""

    max_risk_per_trade: float = 0.05
    """Absolutní strop na doporučené riziko, ať Kelly spočítá cokoli.

    Kelly na malém vzorku umí vyjít i na 20 % kapitálu na obchod. To je
    matematicky správně a prakticky sebevražedné, protože ten odhad edge
    je skoro jistě nadsazený.
    """

    def __post_init__(self) -> None:
        if not 0 < self.fraction <= 1:
            raise ValueError("fraction musí být v (0, 1]")
        if self.max_multiplier < self.min_multiplier:
            raise ValueError("max_multiplier musí být >= min_multiplier")


class KellySizer:
    """Adaptivní velikost pozice podle dosud realizovaných obchodů.

    Nezvyšuje riziko na základě nadějí, ale podle toho, co strategie
    skutečně předvedla. Dokud nemá dost obchodů, drží se základní velikosti —
    což bude většinu první sezóny.
    """

    def __init__(self, config: KellyConfig | None = None) -> None:
        self.config = config or KellyConfig()
        self._r_multiples: list[float] = []

    def record(self, net_pnl: float, risked_amount: float) -> None:
        """Zaznamená uzavřený obchod jako násobek riskované částky (R).

        Vyražený stop-loss dá zhruba −1 R, vítěz například +2,5 R. Kelly pak
        pracuje přímo s rozdělením, které strategie generuje.
        """
        if risked_amount > 0 and math.isfinite(net_pnl):
            self._r_multiples.append(net_pnl / risked_amount)

    @property
    def trade_count(self) -> int:
        return len(self._r_multiples)

    def full_kelly(self) -> float | None:
        """Plná Kellyho frakce z dosavadních obchodů, nebo None při málo datech."""
        cfg = self.config
        if len(self._r_multiples) < cfg.min_trades:
            return None

        values = np.asarray(self._r_multiples, dtype=float)
        wins = values[values > 0]
        losses = values[values <= 0]
        if wins.size == 0 or losses.size == 0:
            return None

        return kelly_discrete(
            win_rate=wins.size / values.size,
            avg_win=float(wins.mean()),
            avg_loss=float(-losses.mean()),
        )

    def suggested_risk_per_trade(self) -> float | None:
        """Doporučené riziko na obchod jako podíl kapitálu, nebo None."""
        full = self.full_kelly()
        if full is None:
            return None
        return min(full * self.config.fraction, self.config.max_risk_per_trade)

    def multiplier(self, base_risk_per_trade: float) -> float:
        """Kolikrát zvětšit či zmenšit základní velikost pozice.

        Vrací 1.0, dokud není dost obchodů — tedy „nesahej na nastavení,
        ještě nic nevíš".
        """
        cfg = self.config
        suggested = self.suggested_risk_per_trade()
        if suggested is None or base_risk_per_trade <= 0:
            return 1.0
        if suggested <= 0:
            return cfg.min_multiplier
        ratio = suggested / base_risk_per_trade
        return min(max(ratio, cfg.min_multiplier), cfg.max_multiplier)


def kelly_growth_rate(fraction: float, mean_return: float, variance: float) -> float:
    """Očekávaná logaritmická míra růstu při dané frakci.

    g(f) = f·μ − f²·σ²/2. Maximum je v f* = μ/σ², a co je důležitější, funkce
    je kolem maxima velmi plochá: poloviční Kelly ztrácí jen čtvrtinu růstu.
    Naopak dvojnásobek Kelly dává růst **nulový**. Asymetrie chyby je důvod,
    proč se chybuje směrem dolů.
    """
    return fraction * mean_return - 0.5 * fraction**2 * variance
