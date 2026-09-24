"""Co se na titulu děje, co to znamená a jak podobné situace dopadaly.

Tohle **není předpověď** a nic nedoporučuje. Má tři vrstvy:

1. *Popis* — fakta o posledním baru: trend, propad od maxima, volatilita,
   objem. Počítá se výhradně dozadu (bod 1 z CLAUDE.md).
2. *Historické srovnání* — kolikrát v minulosti nastal podobný stav a co
   následovalo. Vždy s počtem případů, rozptylem a hlavně **proti běžnému
   chování titulu**. Když titul roste v 60 % libovolných čtvrtletí, pak
   „po propadu rostl v 62 % případů" neznamená nic. Bez toho srovnání by
   modul vyráběl vzorce z náhody.
3. *Časová osa* — co se kolem stavu dělo v kalendáři (FOMC, CPI, NFP…).
   Ukazuje souslednost, ne příčinu: zpětně se k jakémukoli pohybu najde
   událost, která ho „vysvětluje".

Srovnávané případy se překrývají (propad trvá týdny a každý den by byl
„případ"). Proto se berou jen případy vzdálené aspoň ``horizont`` barů od
sebe — jinak by jedna epizoda počítala za dvacet a četnost by lhala.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

MIN_PRIPADU = 8
"""Pod tímhle počtem se četnost neukazuje jako číslo — jen jako „málo dat"."""


def priznaky(df: pd.DataFrame) -> pd.DataFrame:
    """Popisné veličiny pro každý bar, počítané jen z minulosti."""
    c = df["close"]
    r = c.pct_change()
    out = pd.DataFrame(index=df.index)
    out["close"] = c
    out["propad"] = c / c.rolling(252, min_periods=60).max() - 1
    out["nad_sma200"] = c / c.rolling(200, min_periods=150).mean() - 1
    out["zmena_20d"] = c / c.shift(20) - 1
    vol20 = r.rolling(20).std() * np.sqrt(252)
    vol_bezna = r.rolling(252, min_periods=120).std() * np.sqrt(252)
    out["vol"] = vol20
    out["vol_pomer"] = vol20 / vol_bezna
    pad = (r < 0).astype(int)
    out["dni_poklesu"] = pad.groupby((pad == 0).cumsum()).cumsum()
    if "volume" in df and df["volume"].fillna(0).sum() > 0:
        out["objem_pomer"] = df["volume"] / df["volume"].rolling(50, min_periods=20).mean()
    else:
        out["objem_pomer"] = np.nan
    return out


def _kategorie(radek: pd.Series) -> dict[str, str]:
    """Stav přeložený do slov. Hranice jsou hrubé schválně — jemné dělení
    by nechalo v každé přihrádce pár případů a četnost by byla šum."""
    k = {}
    p = radek["propad"]
    k["propad"] = (
        "u maxima"
        if p > -0.05
        else "mírný propad"
        if p > -0.10
        else "korekce"
        if p > -0.20
        else "hluboký propad"
    )
    t = radek["nad_sma200"]
    k["trend"] = "nahoru" if t > 0.02 else "dolů" if t < -0.02 else "bez trendu"
    v = radek["vol_pomer"]
    k["neklid"] = "klidný" if v < 0.8 else "běžný" if v < 1.3 else "neklidný"
    return k


@dataclass
class Popis:
    symbol: str
    den: date
    cena: float
    cisla: dict
    kategorie: dict[str, str]
    vety: list[str] = field(default_factory=list)


def popis(symbol: str, df: pd.DataFrame) -> Popis:
    """Co se děje teď, čísly i slovy."""
    f = priznaky(df).dropna(subset=["propad", "nad_sma200", "vol_pomer"])
    if f.empty:
        raise ValueError(f"{symbol}: málo historie na popis stavu (třeba aspoň rok)")
    x = f.iloc[-1]
    kat = _kategorie(x)
    vety = [
        f"Cena je {abs(x['propad']) * 100:.1f} % pod maximem za poslední rok ({kat['propad']}).",
        f"Vůči 200dennímu průměru je {x['nad_sma200'] * 100:+.1f} % "
        f"— dlouhodobý trend {kat['trend']}.",
        f"Za 20 obchodních dní {x['zmena_20d'] * 100:+.1f} %.",
        f"Kolísání {x['vol'] * 100:.0f} % ročně, "
        f"{x['vol_pomer']:.1f}× obvyklé úrovně ({kat['neklid']}).",
    ]
    if x["dni_poklesu"] >= 4:
        vety.append(f"Klesá {int(x['dni_poklesu'])} dní po sobě.")
    if pd.notna(x["objem_pomer"]) and x["objem_pomer"] >= 2:
        vety.append(f"Objem obchodů je {x['objem_pomer']:.1f}× průměru — neobvyklý zájem.")
    cisla = {k: (None if pd.isna(v) else float(v)) for k, v in x.items()}
    return Popis(symbol, f.index[-1].date(), float(x["close"]), cisla, kat, vety)


@dataclass
class Srovnani:
    horizont: int
    pripadu: int
    podil_rustu: float | None
    median: float | None
    nejhorsi: float | None
    nejlepsi: float | None
    bezne_podil_rustu: float
    bezne_median: float
    data: list[str]
    """Data nalezených případů — ať jde každý zkontrolovat."""

    @property
    def dost_dat(self) -> bool:
        return self.pripadu >= MIN_PRIPADU

    def veta(self) -> str:
        if not self.dost_dat:
            return (
                f"Podobný stav nastal jen {self.pripadu}× — na četnost je to málo, "
                "číslo by bylo náhoda."
            )
        rozdil = (self.podil_rustu - self.bezne_podil_rustu) * 100
        hodnoceni = (
            "prakticky stejně jako kdykoli jindy"
            if abs(rozdil) < 8
            else "častěji než obvykle"
            if rozdil > 0
            else "méně často než obvykle"
        )
        return (
            f"Podobný stav nastal {self.pripadu}×. Po {self.horizont} obchodních dnech "
            f"byla cena výš v {self.podil_rustu * 100:.0f} % případů "
            f"(obvykle v {self.bezne_podil_rustu * 100:.0f} %) — {hodnoceni}. "
            f"Medián {self.median * 100:+.1f} % (obvykle {self.bezne_median * 100:+.1f} %), "
            f"rozpětí od {self.nejhorsi * 100:+.1f} % do {self.nejlepsi * 100:+.1f} %."
        )


def srovnani(df: pd.DataFrame, horizont: int = 63) -> Srovnani:
    """Jak dopadly minulé bary se stejnými kategoriemi jako ten poslední.

    Budoucí výnos se u historických případů smí použít — ty už doběhly.
    Poslední bar sám do srovnání nepatří a případy mladší než ``horizont``
    taky ne, protože jejich výsledek ještě neznáme.
    """
    f = priznaky(df).dropna(subset=["propad", "nad_sma200", "vol_pomer"])
    kat = f.apply(_kategorie, axis=1, result_type="expand")
    cil = kat.iloc[-1]
    budouci = f["close"].shift(-horizont) / f["close"] - 1
    platne = budouci.notna()

    shoda = (kat == cil).all(axis=1) & platne
    vybrane: list[pd.Timestamp] = []
    posledni_i = -(10**9)
    pozice = {ts: i for i, ts in enumerate(f.index)}
    for ts in f.index[shoda]:
        i = pozice[ts]
        if i - posledni_i >= horizont:
            vybrane.append(ts)
            posledni_i = i

    bezne = budouci[platne]
    vysl = budouci.loc[vybrane]
    if len(vysl):
        return Srovnani(
            horizont,
            len(vysl),
            float((vysl > 0).mean()),
            float(vysl.median()),
            float(vysl.min()),
            float(vysl.max()),
            float((bezne > 0).mean()),
            float(bezne.median()),
            [t.date().isoformat() for t in vybrane],
        )
    return Srovnani(
        horizont, 0, None, None, None, None, float((bezne > 0).mean()), float(bezne.median()), []
    )


def casova_osa(df: pd.DataFrame, udalosti, symbol: str, dni: int = 30) -> list[dict]:
    """Události z kalendáře za posledních ``dni`` a co cena udělala den po nich.

    Souslednost, ne příčina.
    """
    if not len(df):
        return []
    konec = df.index[-1].date()
    c = df["close"]
    out = []
    for u in udalosti:
        if not u.affects(symbol) or (konec - u.day).days > dni or u.day > konec:
            continue
        po = c[c.index.date >= u.day]
        pred = c[c.index.date < u.day]
        reakce = float(po.iloc[0] / pred.iloc[-1] - 1) if len(po) and len(pred) else None
        out.append({"den": u.day.isoformat(), "udalost": u.name, "reakce": reakce})
    return sorted(out, key=lambda x: x["den"])


def rozbor(symbol: str, df: pd.DataFrame, udalosti=(), horizont: int = 63) -> dict:
    """Všechny tři vrstvy dohromady, v podobě vhodné pro JSON."""
    p = popis(symbol, df)
    s = srovnani(df, horizont)
    return {
        "symbol": symbol,
        "den": p.den.isoformat(),
        "cena": p.cena,
        "kategorie": p.kategorie,
        "vety": p.vety,
        "cisla": p.cisla,
        "srovnani": {**s.__dict__, "dost_dat": s.dost_dat, "veta": s.veta()},
        "udalosti": casova_osa(df, udalosti, symbol),
    }


def nacti(symbol: str) -> pd.DataFrame:
    """Data pro rozbor: aktuální a s dostatkem historie.

    ``data.fetch`` bez ``end`` vrátí cache, i když je měsíc stará, a u titulu
    staženého kdysi jen na půl roku by historie nestačila. Popis stavu
    „k dnešku", který je ve skutečnosti měsíc starý, je horší než žádný.
    """
    from . import data as data_module

    dnes = date.today().isoformat()
    df = data_module.fetch(symbol, end=dnes)
    if len(df) < 300:
        df = data_module.fetch(symbol, end=dnes, use_cache=False)
    return df
