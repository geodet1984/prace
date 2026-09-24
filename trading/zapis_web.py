"""Zápis do účetní knihy z formuláře v přehledu.

Přehled byl od začátku jen ke čtení. Tenhle modul je **jediná** výjimka
a je úzká schválně: zapisovat jde jen do knihy skutečného portfolia, jen
dvěma způsoby (jeden pohyb, nebo import výpisu) a vždy ve dvou krocích —
nejdřív náhled, uložení až po potvrzení. Papírový účet se přes přehled
dál změnit nedá; ten posouvá výhradně ``trading paper``.

Formulář používá **stejnou cestu** jako terminál: ``vypis.castka`` pro
znaménko, ``Ledger.add`` pro duplicity, ``Ledger.to_csv`` pro atomický
zápis. Kdyby měl web vlastní převod na pohyb, dříve nebo později by se
ručně zadaný nákup z prohlížeče choval jinak než tentýž z terminálu.

Ochrana serveru proti cizím webům (kontrola hlavičky a adresy) je
v ``dashboard.py``; tady je jen práce s knihou, aby šla testovat bez sítě.
"""

from __future__ import annotations

import tempfile
import threading
from datetime import date
from pathlib import Path

from . import vypis as vypis_module
from .ledger import Ledger, LedgerError, Transaction, TxType

#: Dva zápisy ve stejnou chvíli (dvojklik, dva panely) by si jinak mohly
#: přepsat knihu navzájem — druhý by uložil verzi bez prvního pohybu.
_ZAMEK = threading.Lock()


def _kniha(cesta: Path) -> Ledger:
    return Ledger.from_csv(cesta) if cesta.exists() else Ledger()


def _popis(tx: Transaction) -> dict:
    return {
        "den": tx.day.isoformat(),
        "typ": tx.type.value,
        "symbol": tx.symbol or "",
        "pocet": tx.quantity,
        "cena": tx.price,
        "castka": tx.amount,
        "poplatek": tx.fee,
        "mena": tx.currency,
        "poznamka": tx.note,
    }


def pohyb_z_formulare(data: dict) -> Transaction:
    """Převede pole formuláře na pohyb. Stejná pravidla jako ``trading zapis``."""
    try:
        druh = TxType((data.get("typ") or "").strip().lower())
    except ValueError as exc:
        raise LedgerError(f"neznámý druh pohybu {data.get('typ')!r}") from exc

    pocet = vypis_module.cislo(data.get("pocet"))
    cena = vypis_module.cislo(data.get("cena"))
    den = vypis_module.datum(data["den"]) if data.get("den") else date.today()
    if den > date.today():
        raise LedgerError(f"datum {den} je v budoucnosti")

    return Transaction(
        day=den,
        type=druh,
        amount=vypis_module.castka(druh, vypis_module.cislo(data.get("castka")), pocet, cena),
        symbol=(data.get("symbol") or "").strip().upper() or None,
        quantity=pocet,
        price=cena,
        fee=vypis_module.cislo(data.get("poplatek")),
        currency=(data.get("mena") or "CZK").strip().upper(),
        note=(data.get("poznamka") or "").strip(),
    )


def zapis(cesta: Path, data: dict, *, potvrdit: bool) -> dict:
    """Jeden pohyb. Bez ``potvrdit`` jen řekne, co by se stalo."""
    tx = pohyb_z_formulare(data)
    with _ZAMEK:
        kniha = _kniha(cesta)
        duplicita = any(t.key == tx.key for t in kniha.transactions)
        if duplicita or not potvrdit:
            return {"ulozeno": False, "duplicita": duplicita, "pohyb": _popis(tx)}
        kniha.add(tx)
        # Prodej víc kusů, než držíte, se pozná až při přehrání knihy.
        # Uložit ho a pak nechat přehled padat je horší než odmítnout hned.
        kniha.realized()
        kniha.to_csv(cesta)
    return {"ulozeno": True, "duplicita": False, "pohyb": _popis(tx),
            "pohybu_celkem": len(kniha.transactions)}


def import_vypisu(cesta: Path, obsah: str, *, potvrdit: bool, mena: str = "CZK",
                  mapovani: dict[str, str] | None = None) -> dict:
    """Import výpisu. Bez ``potvrdit`` jen náhled, jako ``trading import``."""
    with tempfile.TemporaryDirectory() as tmp:
        soubor = Path(tmp) / "vypis.csv"
        soubor.write_text(obsah, encoding="utf-8")
        vysledek = vypis_module.nacti_vypis(soubor, mapovani, vychozi_mena=mena)

    with _ZAMEK:
        kniha = _kniha(cesta)
        stare = {t.key for t in kniha.transactions}
        nove = []
        for tx in vysledek.pohyby:
            if tx.key not in stare:
                nove.append(tx)
                stare.add(tx.key)
        odpoved = {
            "mapovani": vysledek.mapovani,
            "nove": [_popis(t) for t in nove],
            "uz_v_knize": len(vysledek.pohyby) - len(nove),
            "preskocene": [{"radek": r, "duvod": d} for r, d in vysledek.preskocene],
            "ulozeno": False,
        }
        if potvrdit and nove:
            kniha.extend(nove)
            kniha.realized()
            kniha.to_csv(cesta)
            odpoved["ulozeno"] = True
    return odpoved
