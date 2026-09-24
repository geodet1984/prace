"""Oficiální kurzovní lístek Burzy cenných papírů Praha.

Burza zveřejňuje každý obchodní den zdarma soubor ``plRRMMDD.zip`` na
``ftp.pse.cz/Results.ak`` (popis sloupců: ``Popis_KL.pdf`` tamtéž). Z něj
tenhle modul bere **oficiální závěrečný kurz** českých titulů — přímo od
zdroje, ne přes Yahoo — a údaje, které Yahoo u Prahy často nemá: počet
zobchodovaných kusů a objem obchodů.

Historii pro srovnání s minulostí dál dodává Yahoo (jeho pražská data
pocházejí z téhož zdroje a s kurzovním lístkem se shodují na korunu);
stahovat kvůli ní tisíce denních souborů by nic nepřidalo.

**Pozor na dny bez obchodu.** Když se titul ten den neobchodoval, lístek
uvede poslední známý kurz, klidně týden starý. Datum posledního obchodu
je v samostatném sloupci a modul ho nese s sebou, aby přehled mohl říct
„dnes se neobchodovalo" místo vydávat starou cenu za dnešní.

Stažené soubory se ukládají do cache a nemění se (lístek za uzavřený den
je konečný), takže se každý den stahuje nanejvýš jednou.
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .data import DEFAULT_CACHE_DIR

logger = logging.getLogger(__name__)

ZDROJ = "http://ftp.pse.cz/Results.ak"


@dataclass(frozen=True)
class Kurz:
    """Jeden řádek souboru AK (burzovní obchody) podle Popis_KL.pdf."""

    isin: str
    nazev: str
    bic: str
    den: date
    zaverecny: float
    zmena_pct: float
    predchozi: float
    rocni_min: float
    rocni_max: float
    kusu: int
    objem: float
    posledni_obchod: date | None

    @property
    def obchodovano_dnes(self) -> bool:
        return self.posledni_obchod == self.den

    @property
    def je_akcie(self) -> bool:
        """BIC akcií a podílových listů začíná „BA", dluhopisů „BD"."""
        return self.bic.startswith("BA")


def _datum(text: str) -> date | None:
    text = (text or "").strip()
    return datetime.strptime(text, "%Y/%m/%d").date() if text else None


def _cislo(text: str) -> float:
    text = (text or "").strip()
    return float(text) if text else 0.0


def precti_ak(obsah: str) -> dict[str, Kurz]:
    """Rozparsuje soubor AK. Klíčem je ISIN."""
    out = {}
    for r in csv.reader(io.StringIO(obsah)):
        if len(r) < 12 or not r[0].strip():
            continue
        try:
            k = Kurz(
                isin=r[0].strip(), nazev=r[1].strip(), bic=r[2].strip(), den=_datum(r[3]),
                zaverecny=_cislo(r[4]), zmena_pct=_cislo(r[5]), predchozi=_cislo(r[6]),
                rocni_min=_cislo(r[7]), rocni_max=_cislo(r[8]), kusu=int(_cislo(r[9])),
                objem=_cislo(r[10]), posledni_obchod=_datum(r[11]),
            )
        except ValueError:
            logger.warning("kurzovní lístek: nečitelný řádek %s", r[:2])
            continue
        out[k.isin] = k
    return out


def _stahni_zip(den: date, cache: Path | None) -> bytes | None:
    import requests

    jmeno = f"pl{den:%y%m%d}.zip"
    soubor = cache / jmeno if cache else None
    if soubor and soubor.exists():
        return soubor.read_bytes()
    url = (f"{ZDROJ}/{jmeno}" if den.year >= 2025 else f"{ZDROJ}/{den.year}/{jmeno}")
    try:
        odpoved = requests.get(url, timeout=30)
    except requests.RequestException as exc:
        logger.warning("kurzovní lístek %s nestažen: %s", jmeno, exc)
        return None
    if odpoved.status_code != 200:
        return None  # víkend, svátek, nebo ještě nezveřejněno
    if soubor:
        soubor.parent.mkdir(parents=True, exist_ok=True)
        soubor.write_bytes(odpoved.content)
    return odpoved.content


def kurzovni_listek(k_datu: date | None = None, cache_dir: Path | None = DEFAULT_CACHE_DIR,
                    zpet_dni: int = 10) -> dict[str, Kurz]:
    """Poslední zveřejněný kurzovní lístek k danému dni (nejvýš ``zpet_dni`` zpět).

    Lístek vychází večer; ráno je tedy nejnovější ten včerejší.
    """
    k_datu = k_datu or date.today()
    cache = Path(cache_dir) / "pse" if cache_dir else None
    for posun in range(zpet_dni + 1):
        den = k_datu - timedelta(days=posun)
        if den.weekday() >= 5:
            continue
        data = _stahni_zip(den, cache)
        if not data:
            continue
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            ak = next((n for n in z.namelist() if n.upper().startswith("AK")), None)
            if ak:
                return precti_ak(z.read(ak).decode("cp1250"))
    return {}


_PREVODY: dict[str, str | None] = {}
"""ISIN → ticker v paměti procesu, včetně neúspěchů (ty se neukládají na disk)."""


def _kc(x: float, des: int = 2) -> str:
    """Česky: mezera jako oddělovač tisíců, čárka desetinná."""
    return f"{x:,.{des}f}".replace(",", " ").replace(".", ",")


def pro_ticker(ticker: str, listek: dict[str, Kurz], prevod=None) -> Kurz | None:
    """Kurz pro ticker z Yahoo (``CEZ.PR``).

    Lístek nese ISIN, Yahoo ticker. Nejdřív se zkusí uložená převodní
    tabulka (okamžité); jinak se převádějí ISIN akcií z lístku, dokud se
    ticker nenajde — výsledky se ukládají, takže se to děje jen poprvé.
    ``prevod`` jde podstrčit, testy tak nesahají na síť.
    """
    ticker = ticker.upper()
    if not ticker.endswith(".PR"):
        return None
    if prevod is None:
        from . import isin as isin_module

        ulozene = {t.upper(): i for (i, m), t in isin_module.nacti_tabulku().items()
                   if m == "CZK"}
        if ulozene.get(ticker) in listek:
            return listek[ulozene[ticker]]

        def prevod(isin_: str) -> str | None:
            if isin_ not in _PREVODY:
                _PREVODY[isin_] = isin_module.preloz(isin_, "CZK")
            return _PREVODY[isin_]

    for kurz in listek.values():
        if kurz.je_akcie and (prevod(kurz.isin) or "").upper() == ticker:
            return kurz
    return None


def veta(kurz: Kurz) -> str:
    """Jedna věta pro přehled."""
    den = f"{kurz.den.day}. {kurz.den.month}."
    if not kurz.obchodovano_dnes:
        posledni = (f"{kurz.posledni_obchod.day}. {kurz.posledni_obchod.month}. "
                    f"{kurz.posledni_obchod.year}" if kurz.posledni_obchod else "neznámo kdy")
        return (f"Oficiální kurz BCPP k {den}: {_kc(kurz.zaverecny)} Kč — ten den se "
                f"neobchodovalo, kurz je z posledního obchodu {posledni}.")
    zmena = ("+" if kurz.zmena_pct > 0 else "") + _kc(kurz.zmena_pct)
    return (f"Oficiální kurz BCPP k {den}: {_kc(kurz.zaverecny)} Kč ({zmena} %), "
            f"zobchodováno {_kc(kurz.kusu, 0)} ks za {_kc(kurz.objem / 1e6, 1)} mil. Kč.")


_LISTEK: tuple[date, dict[str, Kurz]] | None = None


def dnesni_listek() -> dict[str, Kurz]:
    """Lístek drží proces v paměti do konce dne — přehled se ptá často."""
    global _LISTEK
    if _LISTEK is None or _LISTEK[0] != date.today():
        _LISTEK = (date.today(), kurzovni_listek())
    return _LISTEK[1]


def oficialni_kurz(ticker: str) -> Kurz | None:
    """Kurz z lístku pro ``*.PR``, jinak None. Selhání sítě = None, ne pád."""
    if not ticker.upper().endswith(".PR"):
        return None
    try:
        return pro_ticker(ticker, dnesni_listek())
    except Exception as exc:  # noqa: BLE001 - bez lístku se použije Yahoo
        logger.warning("%s: kurz z BCPP nezjištěn (%s)", ticker, exc)
        return None


def oprav_radu(ticker: str, df, let: float = 5.0, listek_k=None):
    """Nahradí u pražského titulu podezřelé dny z Yahoo oficiálním kurzem.

    Yahoo u některých pražských titulů celé týdny opakuje poslední cenu
    s nulovým objemem, přestože se podle burzy obchodovalo — u Colt CZ
    v létě 2026 dva měsíce „1 020 Kč, 0 ks" místo skutečných 906–996 Kč.
    Graf i srovnání s historií by pak stály na cenách, které neexistovaly.

    Podezřelý je každý den s nulovým objemem. Pro něj se stáhne kurzovní
    lístek; obchodovalo-li se ten den, vezme se oficiální závěrečný kurz
    a objem. Neobchodovalo-li se opravdu, řádek zůstane — opakovaná cena
    je pak správný popis. Stahuje se jen podezřelé, jednou staženo
    zůstává v cache. ``listek_k`` jde podstrčit v testech.
    """
    import pandas as pd

    if not ticker.upper().endswith(".PR") or df is None or df.empty or "volume" not in df:
        return df
    listek_k = listek_k or (lambda den: kurzovni_listek(den, zpet_dni=0))
    dnes = listek_k(date.today()) or dnesni_listek()
    kurz = pro_ticker(ticker, dnes) if dnes else None
    if kurz is None:
        return df
    isin = kurz.isin

    od = df.index[-1] - pd.Timedelta(days=int(365.25 * let))
    podezrele = df.index[(df.index >= od) & (df["volume"].fillna(0) == 0)]
    if len(podezrele) == 0:
        return df
    df = df.copy()
    opraveno = 0
    for ts in podezrele:
        k = listek_k(ts.date()).get(isin)
        if k and k.obchodovano_dnes and k.zaverecny > 0:
            df.loc[ts, "close"] = k.zaverecny
            df.loc[ts, "volume"] = k.kusu
            opraveno += 1
    if opraveno:
        logger.info("%s: %d dní z Yahoo nahrazeno oficiálním kurzem BCPP", ticker, opraveno)
    return df
