"""Převod ISIN na ticker, podle kterého se stahují ceny.

Výpisy z bank uvádějí ISIN (jednoznačný identifikátor cenného papíru),
ceny se ale stahují podle tickeru. Bez převodu by se pozice ocenila
nákupní cenou a hodnota portfolia by stála.

**Burza musí sedět s měnou nákupu.** Jeden fond se obchoduje na několika
burzách v různých měnách — CSPX v eurech v Amsterdamu, v dolarech
v Londýně. Kdo koupil v eurech a dostane dolarovou kotaci, má hodnotu
pozice vedle o kurz a nepozná to. Stejně zákeřné jsou londýnské kotace
v pencích (GBp): cena stokrát větší, než by měla být. Proto se u každého
kandidáta ověří měna, ve které se obchoduje, a bere se jen ten, který
sedí přesně.

Nalezené dvojice se ukládají do ``data/isin_tickery.csv``. Soubor je
čitelný a dá se opravit ručně — ruční záznam má vždy přednost. A převod
je tím stálý: stejný výpis nahraný podruhé dá stejné tickery, takže se
duplicity poznají i po změně nabídky na Yahoo.
"""

from __future__ import annotations

import csv
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

SOUBOR = Path("data/isin_tickery.csv")

_TVAR = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")

#: Přípony evropských burz na Yahoo. Holý kořen bez přípony se nezkouší:
#: v USA pod ním může být úplně jiný papír (CSSPX ≠ CSPX) se shodou jmen.
PRIPONY = (".AS", ".DE", ".L", ".MI", ".PA", ".SW", ".PR", ".VI")


def je_isin(text: str) -> bool:
    """Tvar i kontrolní číslice (Luhn nad převedenými písmeny).

    Kontrolní číslice odhalí překlep — bez ní by se hledal neexistující
    papír a v horším případě se našel jiný.
    """
    text = (text or "").strip().upper()
    if not _TVAR.match(text):
        return False
    cislice = "".join(str(int(z, 36)) for z in text[:-1])
    soucet = 0
    for i, z in enumerate(reversed(cislice)):
        n = int(z) * (2 if i % 2 == 0 else 1)
        soucet += n - 9 if n > 9 else n
    return (10 - soucet % 10) % 10 == int(text[-1])


def nacti_tabulku(soubor: Path = SOUBOR) -> dict[tuple[str, str], str]:
    """(ISIN, měna) → ticker z uloženého souboru."""
    if not soubor.exists():
        return {}
    out = {}
    with soubor.open(encoding="utf-8", newline="") as f:
        for radek in csv.DictReader(r for r in f if not r.lstrip().startswith("#")):
            if radek.get("isin") and radek.get("ticker"):
                out[(radek["isin"].strip().upper(), (radek.get("mena") or "").strip().upper())] = \
                    radek["ticker"].strip()
    return out


def _uloz(isin: str, mena: str, ticker: str, soubor: Path = SOUBOR) -> None:
    novy = not soubor.exists()
    soubor.parent.mkdir(parents=True, exist_ok=True)
    with soubor.open("a", encoding="utf-8", newline="") as f:
        if novy:
            f.write("# ISIN → ticker pro stahování cen. Ruční opravy mají přednost.\n")
            f.write("isin,mena,ticker\n")
        csv.writer(f).writerow([isin, mena, ticker])


def _mena_kotace(ticker: str) -> str | None:
    """Měna, ve které Yahoo titul kotuje. ``GBp`` (pence) zůstává odlišné od GBP."""
    import yfinance as yf

    # Neexistující kandidáti jsou tu běžní; yfinance by každý vypsal jako chybu.
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    try:
        return yf.Ticker(ticker).fast_info["currency"]
    except Exception:  # noqa: BLE001 - neexistující ticker je prostě kandidát navíc
        return None


def _nazev(dotaz: str) -> tuple[str | None, str]:
    """(symbol, název) prvního výsledku vyhledávání na Yahoo."""
    import yfinance as yf

    try:
        q = yf.Search(dotaz, max_results=3).quotes
    except Exception:  # noqa: BLE001
        return None, ""
    if not q:
        return None, ""
    return q[0].get("symbol"), q[0].get("longname") or q[0].get("shortname") or ""


def _stejny_nazev(a: str, b: str) -> bool:
    """Zhruba stejný název — shoda většiny slov, bez ohledu na pořadí a velikost.

    Yahoo u kotací ISIN neuvádí, takže je to jediná kontrola, že odvozený
    ticker na jiné burze je tentýž fond a ne jiný papír se shodou jmen.
    """
    slova = lambda s: set(re.findall(r"[a-z0-9&]+", s.lower())) - {"ucits", "etf", "plc", "the"}  # noqa: E731
    sa, sb = slova(a), slova(b)
    return bool(sa and sb) and len(sa & sb) / min(len(sa), len(sb)) >= 0.6


def _kandidati(isin: str) -> list[str]:
    """Co Yahoo k ISIN najde, a tentýž kořen na ostatních burzách."""
    import yfinance as yf

    try:
        nalezene = [q["symbol"] for q in yf.Search(isin, max_results=8).quotes if q.get("symbol")]
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: vyhledání na Yahoo selhalo (%s)", isin, exc)
        return []
    out = list(nalezene)
    for symbol in nalezene:
        koren = symbol.split(".")[0]
        # Borsa Italiana kotuje řadu ETF s předponou „CS" (CSSPX = CSPX).
        koreny = {koren, koren[2:]} if koren.startswith("CS") and len(koren) > 4 else {koren}
        for k in koreny:
            out += [k + p for p in PRIPONY]
    return list(dict.fromkeys(out))


def preloz(isin: str, mena: str, *, soubor: Path = SOUBOR,
           mena_kotace=_mena_kotace, kandidati=_kandidati, nazev=_nazev) -> str | None:
    """Ticker pro ISIN kotovaný v ``mena``, nebo ``None``.

    Přímý výsledek vyhledávání podle ISIN se bere, sedí-li měna. Odvozený
    (tentýž kořen na jiné burze) navíc musí mít stejný název jako papír
    nalezený podle ISIN — jinak jde o shodu jmen s jiným papírem.

    ``mena_kotace``, ``kandidati`` a ``nazev`` jdou podstrčit — testy tak
    nesahají na síť.
    """
    isin, mena = isin.strip().upper(), (mena or "").strip().upper()
    tabulka = nacti_tabulku(soubor)
    if (isin, mena) in tabulka:
        return tabulka[(isin, mena)]
    primy, vzor = nazev(isin)
    for ticker in kandidati(isin):
        if mena_kotace(ticker) != mena:
            continue
        if ticker != primy and not _stejny_nazev(nazev(ticker)[1], vzor):
            logger.info("%s: %s má měnu %s, ale jiný název — přeskakuji", isin, ticker, mena)
            continue
        _uloz(isin, mena, ticker, soubor)
        return ticker
    return None
