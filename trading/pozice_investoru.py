"""Co dělají konkrétní skupiny investorů — ze zdrojů, které to opravdu evidují.

„Kolik investorů kupovalo a kolik prodávalo" změřit nejde: každý obchod
má kupujícího i prodávajícího. Existují ale úřední záznamy o tom, co
dělají **konkrétní skupiny**, a z nich tenhle modul čte dva:

*COT (CFTC).* Každý týden úřad zveřejní, jak jsou na futures nastavení
velcí spekulanti (fondy) proti firmám, které se jen zajišťují. Týká se
indexů a komodit, tedy i evropských ETF na S&P 500 nebo zlato.

*Insideři (SEC Form 4).* Ředitelé a manažeři amerických firem musí do
dvou dnů ohlásit nákup nebo prodej vlastních akcií. Počítají se jen
obchody na trhu (kód P a S) — opce, odměny a převody o názoru na cenu
nic neříkají.

**Pozor na datum zveřejnění.** COT popisuje stav k úterý, ale vychází
v pátek; Form 4 se počítá podle data podání, ne data obchodu. Použít
datum stavu místo data zveřejnění by byl pohled do budoucnosti (bod 1
z CLAUDE.md) — tři dny, o kterých by backtest věděl a vy ne.

Stejně jako ``stav_trhu`` nic nepředpovídá a vždy srovnává s tím, co
titul dělá běžně.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from .data import DEFAULT_CACHE_DIR

logger = logging.getLogger(__name__)

COT_API = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
COT_ZPOZDENI = timedelta(days=3)
"""Úterní stav vychází v pátek. O tolik se posouvá, kdy je číslo známé."""

#: Titul → trh futures v COT, jehož pozice ho popisují. ETF na S&P 500 se
#: řídí E-mini S&P 500; ETC na kovy příslušnou komoditou.
COT_TRHY: dict[str, tuple[str, str]] = {
    "SP500": ("13874A", "S&P 500"),
    "ZLATO": ("088691", "zlato"),
    "STRIBRO": ("084691", "stříbro"),
}
TITUL_NA_TRH = {
    "SPY": "SP500", "CSPX.AS": "SP500", "CSPX.L": "SP500", "SXR8.DE": "SP500",
    "VUSA.AS": "SP500", "VUSA.L": "SP500", "VUAA.L": "SP500", "SPY5.DE": "SP500",
    "IGLN.L": "ZLATO", "SGLN.L": "ZLATO", "4GLD.DE": "ZLATO", "GLD": "ZLATO",
    "PHAG.L": "STRIBRO", "SLV": "STRIBRO",
}
OKNO_TYDNU = 156
"""Tři roky. Pozice se hodnotí proti vlastní nedávné historii, protože
absolutní velikost trhu futures za 30 let několikrát vyrostla."""


# --- COT ------------------------------------------------------------------


def stahni_cot(kod: str, cache_dir: Path | None = DEFAULT_CACHE_DIR) -> pd.DataFrame:
    """Týdenní pozice pro jeden trh. Index je **datum zveřejnění**, ne stavu."""
    import requests

    cesta = Path(cache_dir) / f"cot_{kod}.csv" if cache_dir else None
    if cesta and cesta.exists():
        df = pd.read_csv(cesta, index_col=0, parse_dates=True)
        if len(df) and (pd.Timestamp.today() - df.index[-1]).days < 8:
            return df

    radky = []
    offset = 0
    while True:
        odpoved = requests.get(COT_API, timeout=60, params={
            "$select": "report_date_as_yyyy_mm_dd,open_interest_all,"
                       "noncomm_positions_long_all,noncomm_positions_short_all,"
                       "comm_positions_long_all,comm_positions_short_all",
            "$where": f"cftc_contract_market_code='{kod}'",
            "$order": "report_date_as_yyyy_mm_dd", "$limit": 5000, "$offset": offset,
        })
        odpoved.raise_for_status()
        davka = odpoved.json()
        radky += davka
        if len(davka) < 5000:
            break
        offset += 5000
    if not radky:
        raise ValueError(f"COT pro trh {kod} je prázdné")
    df = pd.DataFrame(radky)
    df.index = pd.to_datetime(df.pop("report_date_as_yyyy_mm_dd")) + COT_ZPOZDENI
    df = df.astype(float).rename(columns={
        "open_interest_all": "oi",
        "noncomm_positions_long_all": "spek_long", "noncomm_positions_short_all": "spek_short",
        "comm_positions_long_all": "zaj_long", "comm_positions_short_all": "zaj_short",
    })
    df.index.name = "zverejneno"
    if cesta:
        cesta.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(cesta)
    return df


def cot_priznaky(df: pd.DataFrame) -> pd.DataFrame:
    """Čistá pozice spekulantů a její pořadí v posledních třech letech."""
    out = pd.DataFrame(index=df.index)
    out["cista"] = (df["spek_long"] - df["spek_short"]) / df["oi"]
    # Percentil jen z minulosti a aktuálního týdne — nikdy z budoucích.
    out["percentil"] = out["cista"].rolling(OKNO_TYDNU, min_periods=52).apply(
        lambda x: (x[:-1] < x[-1]).mean() if len(x) > 1 else float("nan"), raw=True
    )
    return out


def _cot_kategorie(p: float) -> str:
    return ("extrémně sázejí na růst" if p >= 0.9 else "extrémně sázejí na pokles" if p <= 0.1
            else "běžně")


@dataclass
class Vysledek:
    """Věty pro člověka a srovnání s historií."""

    zdroj: str
    vety: list[str]
    srovnani: str | None = None
    datum: str | None = None


def cot_rozbor(symbol: str, ceny: pd.DataFrame, cot: pd.DataFrame,
               horizont: int = 63) -> Vysledek:
    """Postavení velkých spekulantů a co po podobném postavení následovalo."""
    from .stav_trhu import Srovnani

    trh = COT_TRHY[TITUL_NA_TRH[symbol]][1]
    f = cot_priznaky(cot).dropna()
    if f.empty:
        return Vysledek("COT", [f"Na trhu {trh} je málo historie pozic."])
    ted = f.iloc[-1]
    kat = _cot_kategorie(ted["percentil"])
    smer = "na růst" if ted["cista"] > 0 else "na pokles"
    vety = [
        f"Velcí spekulanti na trhu {trh} drží čistě {abs(ted['cista']) * 100:.1f} % "
        f"otevřených kontraktů {smer} (zveřejněno {f.index[-1].date()}).",
        f"Proti posledním třem letům je to víc než v {ted['percentil'] * 100:.0f} % týdnů "
        f"— spekulanti {kat}.",
    ]

    # Historie: týdny se stejnou kategorií, co pak dělala cena titulu.
    c = ceny["close"]
    budouci = c.shift(-horizont) / c - 1
    bezne = budouci.dropna()
    kategorie = f["percentil"].map(_cot_kategorie)
    kandidati = f.index[(kategorie == kat)]
    vybrane, posledni = [], None
    for den in kandidati:
        # První obchodní den, kdy už číslo bylo veřejné.
        pozice = c.index.searchsorted(den)
        if pozice >= len(c) or pd.isna(budouci.iloc[pozice]):
            continue
        if posledni is not None and pozice - posledni < horizont:
            continue
        vybrane.append(pozice)
        posledni = pozice
    vysl = budouci.iloc[vybrane]
    s = Srovnani(
        horizont, len(vysl),
        float((vysl > 0).mean()) if len(vysl) else None,
        float(vysl.median()) if len(vysl) else None,
        float(vysl.min()) if len(vysl) else None,
        float(vysl.max()) if len(vysl) else None,
        float((bezne > 0).mean()), float(bezne.median()),
        [c.index[i].date().isoformat() for i in vybrane],
    )
    veta = s.veta().replace("Podobný stav nastal", "Podobné postavení nastalo")
    return Vysledek("COT", vety, veta, f.index[-1].date().isoformat())


# --- insideři (SEC Form 4) -----------------------------------------------


SEC_KONTAKT_PROMENNA = "TRADER_SEC_KONTAKT"
"""SEC vydá dokumenty jen s kontaktem v User-Agent (jméno a e-mail). Bez
něj se insideři přeskočí — adresa se nikam neposílá, dokud ji nezadáte."""


def sec_kontakt() -> str | None:
    kontakt = os.environ.get(SEC_KONTAKT_PROMENNA)
    if kontakt:
        return kontakt
    soubor = Path("data/sec_kontakt.txt")
    if soubor.exists():
        return soubor.read_text(encoding="utf-8").strip() or None
    return None


def je_americka_akcie(symbol: str) -> bool:
    """Form 4 existuje jen u amerických firem; evropské tickery mají příponu."""
    return "." not in symbol and symbol not in TITUL_NA_TRH


_KODY = re.compile(
    r"<nonDerivativeTransaction>.*?<transactionCode>(\w)</transactionCode>.*?"
    r"<transactionShares>\s*<value>([\d.]+)</value>.*?"
    r"(?:<transactionPricePerShare>\s*<value>([\d.]+)</value>)?",
    re.S,
)


def obchody_z_form4(xml: str) -> list[tuple[str, float, float]]:
    """(kód, kusů, cena) za každý přímý obchod v dokumentu. Jen P a S."""
    out = []
    for kod, kusu, cena in _KODY.findall(xml):
        if kod in ("P", "S"):
            out.append((kod, float(kusu), float(cena or 0)))
    return out


def stahni_insidery(symbol: str, kontakt: str, cache_dir: Path | None = DEFAULT_CACHE_DIR,
                    max_podani: int = 400) -> pd.DataFrame:
    """Nákupy a prodeje insiderů na trhu. Index je **datum podání**.

    Každý Form 4 je samostatný soubor; stažené se ukládají natrvalo (podání
    se nemění), takže se po prvním běhu stahují jen nová.
    """
    import time

    import requests

    h = {"User-Agent": kontakt}
    cache = Path(cache_dir) / "form4" / symbol if cache_dir else None
    if cache:
        cache.mkdir(parents=True, exist_ok=True)

    mapa = requests.get("https://www.sec.gov/files/company_tickers.json", headers=h, timeout=30)
    mapa.raise_for_status()
    cik = next((v["cik_str"] for v in mapa.json().values() if v["ticker"] == symbol), None)
    if cik is None:
        raise ValueError(f"{symbol}: SEC tuhle firmu nezná")

    sub = requests.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", headers=h,
                       timeout=30)
    sub.raise_for_status()
    rec = sub.json()["filings"]["recent"]
    radky = []
    for i, forma in enumerate(rec["form"]):
        if forma != "4" or len(radky) >= max_podani * 3:
            continue
        acc = rec["accessionNumber"][i].replace("-", "")
        soubor = rec["primaryDocument"][i].split("/")[-1]
        cil = cache / f"{acc}.xml" if cache else None
        if cil and cil.exists():
            xml = cil.read_text(encoding="utf-8")
        else:
            odpoved = requests.get(
                f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{soubor}",
                headers=h, timeout=30)
            if odpoved.status_code != 200:
                logger.warning("%s: Form 4 %s nestažen (%s)", symbol, acc, odpoved.status_code)
                continue
            xml = odpoved.text
            if cil:
                cil.write_text(xml, encoding="utf-8")
            time.sleep(0.12)  # SEC povoluje 10 dotazů za vteřinu
        for kod, kusu, cena in obchody_z_form4(xml):
            radky.append({"podano": rec["filingDate"][i], "kod": kod, "kusu": kusu,
                          "hodnota": kusu * cena})
    df = pd.DataFrame(radky, columns=["podano", "kod", "kusu", "hodnota"])
    df["podano"] = pd.to_datetime(df["podano"])
    return df.set_index("podano").sort_index()


def insideri_rozbor(symbol: str, ceny: pd.DataFrame, obchody: pd.DataFrame,
                    horizont: int = 63, k_datu: date | None = None) -> Vysledek:
    """Co insideři dělali posledních 90 dní a co následovalo po jejich nákupech."""
    from .stav_trhu import Srovnani

    k_datu = pd.Timestamp(k_datu or date.today())
    posledni = obchody[obchody.index > k_datu - pd.Timedelta(days=90)]
    nakupy = posledni[posledni["kod"] == "P"]
    prodeje = posledni[posledni["kod"] == "S"]
    vety = [
        f"Za 90 dní insideři {symbol} na trhu koupili za {nakupy['hodnota'].sum():,.0f} USD "
        f"({len(nakupy)} obchodů) a prodali za {prodeje['hodnota'].sum():,.0f} USD "
        f"({len(prodeje)} obchodů).".replace(",", " "),
        "Prodeje o názoru na cenu mnoho neříkají — insideři prodávají i kvůli daním nebo "
        "plánovaným prodejům. Nákup vlastních akcií za vlastní peníze je vzácnější.",
    ]
    if obchody.empty:
        return Vysledek("insideři", vety)

    # Historie: dny, kdy byl zveřejněn nákup insidera, a co pak dělala cena.
    c = ceny["close"]
    budouci = c.shift(-horizont) / c - 1
    bezne = budouci.dropna()
    vybrane, predchozi = [], None
    for den in obchody.index[obchody["kod"] == "P"].unique():
        # Den po podání — podání mohlo přijít až po zavření burzy.
        pozice = c.index.searchsorted(den + pd.Timedelta(days=1))
        if pozice >= len(c) or pd.isna(budouci.iloc[pozice]):
            continue
        if predchozi is not None and pozice - predchozi < horizont:
            continue
        vybrane.append(pozice)
        predchozi = pozice
    vysl = budouci.iloc[vybrane]
    s = Srovnani(
        horizont, len(vysl),
        float((vysl > 0).mean()) if len(vysl) else None,
        float(vysl.median()) if len(vysl) else None,
        float(vysl.min()) if len(vysl) else None,
        float(vysl.max()) if len(vysl) else None,
        float((bezne > 0).mean()), float(bezne.median()),
        [c.index[i].date().isoformat() for i in vybrane],
    )
    return Vysledek("insideři", vety,
                    s.veta().replace("Podobný stav nastal", "Nákup insidera byl zveřejněn"))


def rozbor(symbol: str, ceny: pd.DataFrame) -> list[dict]:
    """Všechny dostupné zdroje pro titul. Selhání jednoho nesmí shodit ostatní."""
    out = []
    if symbol in TITUL_NA_TRH:
        try:
            kod = COT_TRHY[TITUL_NA_TRH[symbol]][0]
            out.append(cot_rozbor(symbol, ceny, stahni_cot(kod)).__dict__)
        except Exception as exc:  # noqa: BLE001
            out.append({"zdroj": "COT", "vety": [f"Pozice se nepodařilo zjistit: {exc}"]})
    if je_americka_akcie(symbol):
        kontakt = sec_kontakt()
        if not kontakt:
            out.append({"zdroj": "insideři", "vety": [
                "Obchody insiderů nejsou nastavené: SEC vydá data jen s kontaktem "
                "(jméno a e-mail). Uložte ho do data/sec_kontakt.txt."]})
        else:
            try:
                out.append(insideri_rozbor(symbol, ceny, stahni_insidery(symbol, kontakt)).__dict__)
            except Exception as exc:  # noqa: BLE001
                out.append({"zdroj": "insideři",
                            "vety": [f"Obchody insiderů se nepodařilo zjistit: {exc}"]})
    return out
