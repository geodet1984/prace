"""Denní hlídač portfolia — co je nového od minule.

Přehled v prohlížeči ukáže stav, když se na něj podíváte. Tenhle modul
řeší opačný směr: běží sám a ozve se, jen když je co říct.

Rozdíl je zásadní pro použitelnost. Hlídač, který se ozývá každý den,
se po týdnu přestane číst — a s ním i to jedno hlášení, kvůli kterému
celý existoval. Proto si **pamatuje, co už řekl** (`stav.json`) a totéž
podruhé neopakuje. Cenou je stav, který se hromadí v čase, takže platí
bod 5 z CLAUDE.md: cokoli sem přibude, musí přibýt i do ``uloz``
a ``nacti`` a zvednout ``STATE_VERSION``.

Co hlídá a proč zrovna to:

*Časový test.* Prodej den před uplynutím tří let je zdanitelný, den po
něm ne. Je to jediná věc v celé evidenci, kde má **konkrétní datum**
přímý finanční dopad — a zároveň se na ni snadno zapomene, protože se
blíží tři roky.

*Chyby v knize.* Když si evidence odporuje, jsou všechna ostatní čísla
nespolehlivá. Hlásí se jen ``chyba``, ne ``podezreni`` — podezření bývá
v pořádku a hlásit ho denně je nejrychlejší cesta k tomu, aby si člověk
oznámení vypnul.

*Velký pohyb hodnoty.* Ne jako signál k obchodu, ale jako informace, že
se stalo něco, o čem byste měl vědět.

*Zastaralá čísla.* Když se nepodařilo stáhnout cenu nebo kurz, přehled
počítá s náhradou. Tichý odhad vydávaný za skutečnost je horší než
přiznaná nejistota.

Hlídač **nikdy nic nedoporučuje**. Neřekne „prodej", řekne „za 12 dní
uplyne tříletá lhůta u téhle dávky". Co s tím, rozhodujete vy.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .ledger import Ledger

logger = logging.getLogger(__name__)

#: Zvedněte při každé změně toho, co se ukládá — jinak se starý stav načte
#: jako by byl nový a hlídač buď zopakuje, co už řekl, nebo naopak zamlčí
#: něco nového.
STATE_VERSION = 2

#: Kolik dní předem upozornit na konec tříleté lhůty. Dva měsíce jsou
#: kompromis: dost času na rozmyšlenou a prodej, ale ne tak brzy, aby se
#: na to do té doby zapomnělo.
DNI_PRED_LHUTOU = 60

#: O kolik procent se musí změnit hodnota portfolia proti minulému hlášení,
#: aby to stálo za zmínku.
PRAH_POHYBU_PCT = 5.0


@dataclass(frozen=True)
class Zprava:
    """Jedna věc, kterou stojí za to říct.

    ``kod`` je stabilní klíč — podle něj se pozná, že totéž už bylo
    oznámeno. Musí tedy obsahovat všechno, co danou zprávu odlišuje
    (titul, datum), a nic, co se mění samo (počet zbývajících dní).
    """

    kod: str
    text: str
    zavaznost: str = "info"
    """``info``, ``pozor`` nebo ``chyba`` — určuje pořadí a nadpis."""

    def __str__(self) -> str:
        return self.text


@dataclass
class Stav:
    """Co si hlídač pamatuje mezi běhy."""

    version: int = STATE_VERSION
    oznamene: set[str] = None
    posledni_hodnota: float | None = None
    posledni_beh: str | None = None
    kategorie: dict[str, dict[str, str]] = None
    """Poslední známý stav každého titulu (propad, trend, neklid). Hlásí se
    změna, ne stav — „SPY je u maxima" každý den by nikdo nečetl."""

    def __post_init__(self) -> None:
        if self.oznamene is None:
            self.oznamene = set()
        if self.kategorie is None:
            self.kategorie = {}

    @classmethod
    def nacti(cls, path: str | Path) -> Stav:
        """Načte stav. Chybějící nebo cizí verze začne od nuly.

        Číst starý formát „jak to půjde" znamená hlídače, který po změně
        tiše přestane hlásit část věcí. Radši jedno hlášení navíc.
        """
        path = Path(path)
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("stav hlídače %s je poškozený, začínám znovu", path)
            return cls()
        if data.get("version") != STATE_VERSION:
            logger.info("stav hlídače má verzi %s, čekám %s — začínám znovu",
                        data.get("version"), STATE_VERSION)
            return cls()
        return cls(
            version=STATE_VERSION,
            oznamene=set(data.get("oznamene", [])),
            posledni_hodnota=data.get("posledni_hodnota"),
            posledni_beh=data.get("posledni_beh"),
            kategorie=data.get("kategorie", {}),
        )

    def uloz(self, path: str | Path) -> None:
        """Uloží stav. Přes dočasný soubor, ať přerušení nenechá zmetek."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "version": self.version,
                    "oznamene": sorted(self.oznamene),
                    "posledni_hodnota": self.posledni_hodnota,
                    "posledni_beh": self.posledni_beh,
                    "kategorie": self.kategorie,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        tmp.replace(path)


def zpravy(
    kniha: Ledger,
    prehled: dict,
    stav: Stav,
    k_datu: date | None = None,
    trh: list[dict] | None = None,
) -> list[Zprava]:
    """Co je nového. Čistá funkce — nic nestahuje a nic neukládá.

    ``prehled`` je výstup ``valuation`` nebo ``valuation_czk``. Díky tomu
    jde hlídač testovat bez sítě i bez souborů.
    """
    k_datu = k_datu or date.today()
    out: list[Zprava] = []

    # --- chyby v knize ---------------------------------------------------
    for nalez in kniha.kontrola(k_datu=k_datu):
        if nalez.zavaznost != "chyba":
            continue
        out.append(
            Zprava(
                kod=f"chyba:{nalez.den}:{nalez.zprava[:40]}",
                text=f"V knize nesedí: {nalez.zprava}",
                zavaznost="chyba",
            )
        )

    # --- časový test -----------------------------------------------------
    for radek in kniha.tax_clock(k_datu=k_datu):
        if radek["splneno"]:
            out.append(
                Zprava(
                    kod=f"lhuta-splnena:{radek['symbol']}:{radek['nakoupeno']}",
                    text=(
                        f"{radek['symbol']}: dávka z {radek['nakoupeno']} "
                        f"({radek['quantity']:.3f} ks) prošla tříletou lhůtou."
                    ),
                )
            )
        elif radek["dni_zbyva"] <= DNI_PRED_LHUTOU:
            out.append(
                Zprava(
                    kod=f"lhuta-blizi:{radek['symbol']}:{radek['nakoupeno']}",
                    text=(
                        f"{radek['symbol']}: dávce z {radek['nakoupeno']} "
                        f"({radek['quantity']:.3f} ks) zbývá {radek['dni_zbyva']} dní "
                        f"do konce tříleté lhůty ({radek['osvobozeno_od']})."
                    ),
                    zavaznost="pozor",
                )
            )

    # --- pohyb hodnoty ---------------------------------------------------
    hodnota = prehled.get("hodnota")
    mena = prehled.get("mena", "")
    if hodnota and stav.posledni_hodnota:
        zmena = (hodnota / stav.posledni_hodnota - 1) * 100.0
        if abs(zmena) >= PRAH_POHYBU_PCT:
            out.append(
                Zprava(
                    # Do kódu patří datum, ne velikost pohybu: jinak by
                    # každé další procento vyrobilo "novou" zprávu a hlásilo
                    # by se to znovu a znovu během jednoho propadu.
                    kod=f"pohyb:{k_datu.isoformat()}",
                    text=(
                        f"Hodnota portfolia se od minulého hlášení změnila o "
                        f"{zmena:+.1f} % na {hodnota:,.0f} {mena}."
                    ),
                    zavaznost="pozor" if zmena < 0 else "info",
                )
            )

    # --- zastaralá čísla -------------------------------------------------
    if prehled.get("bez_ceny"):
        out.append(
            Zprava(
                kod=f"bez-ceny:{k_datu.isoformat()}:{','.join(prehled['bez_ceny'])}",
                text=(
                    "Bez aktuální ceny, oceněno pořizovací: "
                    + ", ".join(prehled["bez_ceny"])
                    + ". Hodnota portfolia je tím pádem jen odhad."
                ),
            )
        )
    if prehled.get("bez_kurzu"):
        out.append(
            Zprava(
                kod=f"bez-kurzu:{k_datu.isoformat()}:{','.join(prehled['bez_kurzu'])}",
                text=(
                    "Chybí kurz pro "
                    + ", ".join(prehled["bez_kurzu"])
                    + " — tyhle pohyby v korunových součtech nejsou."
                ),
            )
        )

    # --- změna stavu trhu -------------------------------------------------
    # Jen změna proti minulému běhu. První pozorování titulu se nehlásí:
    # nebylo s čím srovnávat a hlásit „nový" stav u všeho by byl šum.
    nazvy = {"propad": "od maxima", "trend": "trend", "neklid": "kolísání"}
    for r in trh or []:
        predtim = stav.kategorie.get(r["symbol"])
        if not predtim:
            continue
        zmeny = [f"{nazvy.get(k, k)}: {predtim.get(k)} → {v}"
                 for k, v in r["kategorie"].items() if predtim.get(k) != v]
        if zmeny:
            out.append(Zprava(
                kod=f"trh:{r['symbol']}:{r['den']}:{'/'.join(r['kategorie'].values())}",
                text=f"{r['symbol']} změnil stav — {'; '.join(zmeny)}. {r['srovnani']['veta']}",
            ))

    return out


def nove(zpravy_: list[Zprava], stav: Stav) -> list[Zprava]:
    """Vyfiltruje to, co už bylo oznámeno."""
    return [z for z in zpravy_ if z.kod not in stav.oznamene]


def zapamatuj(zpravy_: list[Zprava], stav: Stav, hodnota: float | None,
              k_datu: date | None = None, trh: list[dict] | None = None) -> Stav:
    """Poznamená, co bylo oznámeno, a aktuální hodnotu pro příští srovnání."""
    stav.oznamene |= {z.kod for z in zpravy_}
    for r in trh or []:
        stav.kategorie[r["symbol"]] = dict(r["kategorie"])
    if hodnota:
        stav.posledni_hodnota = hodnota
    stav.posledni_beh = (k_datu or date.today()).isoformat()
    return stav


PORADI = {"chyba": 0, "pozor": 1, "info": 2}


def serad(zpravy_: list[Zprava]) -> list[Zprava]:
    """Chyby nahoru. Co je nejdůležitější, se nemá hledat."""
    return sorted(zpravy_, key=lambda z: (PORADI.get(z.zavaznost, 9), z.kod))


def shrnuti(zpravy_: list[Zprava]) -> str:
    """Jedna věta do oznámení. Delší text se stejně nevejde."""
    if not zpravy_:
        return "Nic nového."
    chyb = sum(1 for z in zpravy_ if z.zavaznost == "chyba")
    if chyb:
        return f"{chyb}× chyba v knize a další {len(zpravy_) - chyb} zpráv"
    if len(zpravy_) == 1:
        return zpravy_[0].text
    return f"{len(zpravy_)} nových zpráv k portfoliu"


def datum_ze_stavu(stav: Stav) -> date | None:
    """Kdy hlídač běžel naposledy, nebo ``None``."""
    if not stav.posledni_beh:
        return None
    try:
        return datetime.fromisoformat(stav.posledni_beh).date()
    except ValueError:
        return None
