"""Data pro grafy v přehledu.

Kreslí se až ve stránce (SVG, bez knihovny z internetu); tady se jen
připravují řady bodů. Obě funkce jsou čisté — ceny i kurzy dostávají
zvenčí, takže jdou testovat bez sítě.

*Cena titulu* — denní závěrečné ceny, zředěné na rozumný počet bodů.
Stránka se obnovuje a posílat tisíce bodů na telefon by nic nepřidalo.

*Hodnota portfolia v čase* — pro každý týden se kniha přehraje jen do toho
dne a ocení **tehdejšími** cenami a **tehdejšími** kurzy ČNB. Ocenit starý
týden dnešní cenou nebo kurzem by byla stejná chyba jako pohled do
budoucnosti v backtestu: křivka by vypadala hladce a lhala by. Vedle
hodnoty jde i čára „vloženo", aby bylo vidět, kolik z růstu je zisk a
kolik jen přisypané peníze.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .ledger import Ledger

MAX_BODU = 400


def cenova_rada(df: pd.DataFrame, let: float = 5.0) -> dict:
    """Závěrečné ceny za posledních ``let`` let, nejvýš ``MAX_BODU`` bodů.

    Ředí se rovnoměrně po řádcích, ale poslední bar se ponechá vždy —
    graf, který končí den před dneškem, by vypadal jako chyba dat.
    """
    c = df["close"].dropna()
    if c.empty:
        return {"dny": [], "ceny": []}
    c = c[c.index >= c.index[-1] - pd.Timedelta(days=int(365.25 * let))]
    krok = max(1, -(-len(c) // MAX_BODU))  # nahoru, jinak by bodů bylo víc než MAX_BODU
    vyber = c.iloc[::krok]
    if vyber.index[-1] != c.index[-1]:
        vyber = pd.concat([vyber, c.iloc[-1:]])
    return {"dny": [d.date().isoformat() for d in vyber.index],
            "ceny": [round(float(x), 4) for x in vyber.values]}


def _cena_k(df: pd.DataFrame | None, den: date) -> float | None:
    """Poslední známá závěrečná cena k danému dni (nikdy z budoucnosti)."""
    if df is None or df.empty:
        return None
    c = df["close"].dropna()
    c = c[c.index <= pd.Timestamp(den)]
    return float(c.iloc[-1]) if len(c) else None


def portfolio_v_case(kniha: Ledger, historie: dict[str, pd.DataFrame], kurzy,
                     k_datu: date | None = None, krok_dni: int = 7) -> list[dict]:
    """Týdenní body hodnoty portfolia a vložených peněz v korunách."""
    if not kniha.transactions:
        return []
    k_datu = k_datu or date.today()
    zacatek = kniha.transactions[0].day
    dny, den = [], zacatek
    while den < k_datu:
        dny.append(den)
        den += timedelta(days=krok_dni)
    dny.append(k_datu)

    body = []
    for den in dny:
        cast = Ledger(transactions=[t for t in kniha.transactions if t.day <= den])
        ceny = {s: c for s in cast.holdings() if (c := _cena_k(historie.get(s), den)) is not None}
        v = cast.valuation_czk(ceny, kurzy, k_datu=den)
        # Majetek = pozice + hotovost na účtu. Bez hotovosti by každý dosud
        # nevyinvestovaný vklad vypadal proti čáře „vloženo" jako ztráta.
        majetek = v["hodnota"] + (v.get("hotovost") or 0.0)
        body.append({"den": den.isoformat(), "hodnota": round(majetek, 2),
                     "vlozeno": round(v["vlozeno"], 2),
                     "odhad": bool(v["bez_ceny"] or v["bez_kurzu"])})
    return body
