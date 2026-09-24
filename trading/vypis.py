"""Import cizího výpisu do účetní knihy.

Broker zatím vybraný není, takže parser konkrétního výpisu by byl práce
naslepo. Tohle je obecnější a přežije to i výměnu brokera: vezme **jakékoli**
CSV, zkusí uhodnout, který jeho sloupec odpovídá kterému našemu, a co
neuhodne, to si nechá říct (``--mapovani``).

Tři vlastnosti, na kterých to stojí:

*Nejdřív ukázat, teprve pak uložit.* Import je jediné místo, kde do knihy
teče cizí soubor. Přepsat evidenci a až potom zjistit, že se sloupce
namapovaly křivě, je nehoda, ze které se špatně vrací — proto je náhled
výchozí chování a uložení se musí říct.

*Nahrát totéž dvakrát nesmí nic zdvojit.* Běžná nehoda: člověk si není
jistý, jestli import proběhl, a spustí ho znovu. Duplicity pozná
``Ledger.add`` podle otisku pohybu, tady se jen počítá, kolik se jich
přeskočilo.

*Co nejde přečíst, se přizná.* Řádek s neznámým typem pohybu se přeskočí
a vypíše — tiše ho zahodit znamená evidenci, které chybí kus a nikdo neví
který.

Směr peněz se **odvozuje z typu pohybu**, ne ze znaménka ve výpisu.
Brokeři to píšou různě (jedni mají nákup záporně, druzí kladně a směr
v samostatném sloupci) a hádat podle znaménka znamená, že se jednou za čas
nákup zapíše jako prodej. Výjimka je směna, u které znaménko nese jedinou
informaci, kterým směrem převod šel — ta se bere, jak přišla.
"""

from __future__ import annotations

import csv
import logging
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path

from .ledger import Ledger, LedgerError, Transaction, TxType

logger = logging.getLogger(__name__)

#: Sloupce, které umí přečíst ``Ledger.from_csv``. ``day`` a ``type`` jsou
#: povinné — bez data a druhu pohybu se z řádku nedá udělat zápis.
POVINNE = ("day", "type")

#: Zápisy, kterými výpisy myslí "nic". Čtou se jako nula; cokoli jiného
#: bez číslic je hlášená chyba, ne tichá nula.
PRAZDNE_ZAPISY = frozenset({"", "-", "--", "n/a", "na", "n.a.", "null", "none", "nan", "x"})

#: Názvy, pod kterými se naše sloupce vyskytují v cizích výpisech.
#: Porovnává se bez diakritiky a bez ohledu na velikost písmen; nejdřív
#: přesná shoda, teprve pak podřetězec — jinak by "price" sebralo sloupec
#: "Price currency" dřív, než by se k němu dostala "currency".
ALIASY: dict[str, tuple[str, ...]] = {
    "day": ("day", "date", "datum", "den", "trade date", "datum obchodu", "time", "cas"),
    "type": ("type", "typ", "action", "akce", "transakce", "operace", "druh", "pohyb"),
    "symbol": ("symbol", "ticker", "instrument", "isin", "nazev", "name", "titul"),
    "quantity": ("quantity", "qty", "shares", "pocet", "mnozstvi", "no. of shares", "kusu"),
    "price": ("price", "cena", "price / share", "price per share", "cena za kus", "kurz"),
    "amount": ("amount", "total", "castka", "celkem", "objem", "value", "hodnota", "suma"),
    "fee": ("fee", "poplatek", "commission", "provize", "charge", "fees", "poplatky"),
    "currency": ("currency", "mena", "ccy", "currency (total)", "měna"),
    "note": ("note", "poznamka", "notes", "description", "popis", "comment"),
}

#: Přesné názvy druhů pohybu. Naše vlastní hodnoty jsou tu taky, aby
#: reimport našeho vlastního exportu prošel beze změny.
TYPY_PRESNE: dict[str, TxType] = {
    **{t.value: t for t in TxType},
    "buy": TxType.BUY,
    "sell": TxType.SELL,
    "dividend": TxType.DIVIDEND,
    "deposit": TxType.DEPOSIT,
    "withdrawal": TxType.WITHDRAWAL,
    "fee": TxType.FEE,
    "tax": TxType.TAX,
    "exchange": TxType.EXCHANGE,
    "koupe": TxType.BUY,
    "vyber": TxType.WITHDRAWAL,
}

#: Podřetězce pro volnější tvary ("Market buy", "Withholding tax").
#: **Na pořadí záleží**: daň z dividendy je daň, ne dividenda, a kdyby se
#: hledala dividenda dřív, spadl by celý srážkový řádek do výnosů.
TYPY_PODRETEZCE: tuple[tuple[str, TxType], ...] = (
    ("withholding", TxType.TAX),
    ("srazkov", TxType.TAX),
    ("tax", TxType.TAX),
    ("dan z", TxType.TAX),
    ("dividend", TxType.DIVIDEND),
    ("exchange", TxType.EXCHANGE),
    ("conversion", TxType.EXCHANGE),
    ("konverze", TxType.EXCHANGE),
    ("smen", TxType.EXCHANGE),
    ("deposit", TxType.DEPOSIT),
    ("vklad", TxType.DEPOSIT),
    ("cash in", TxType.DEPOSIT),
    ("funds in", TxType.DEPOSIT),
    ("withdraw", TxType.WITHDRAWAL),
    ("vyber", TxType.WITHDRAWAL),
    ("cash out", TxType.WITHDRAWAL),
    ("commission", TxType.FEE),
    ("poplat", TxType.FEE),
    ("fee", TxType.FEE),
    ("charge", TxType.FEE),
    ("buy", TxType.BUY),
    ("nakup", TxType.BUY),
    ("koup", TxType.BUY),
    ("sell", TxType.SELL),
    ("prodej", TxType.SELL),
)

#: Jakým směrem teče peníz u kterého druhu pohybu. Znaménko ve výpisu se
#: ignoruje, protože se na něj nedá spolehnout; ``None`` znamená "nech, jak
#: přišlo" a má ho jen směna, u které je znaménko jediná informace o směru.
SMER: dict[TxType, int | None] = {
    TxType.BUY: -1,
    TxType.SELL: 1,
    TxType.DEPOSIT: 1,
    TxType.WITHDRAWAL: -1,
    TxType.DIVIDEND: 1,
    TxType.TAX: -1,
    TxType.FEE: -1,
    TxType.EXCHANGE: None,
}


def castka(druh: TxType, amount: float, quantity: float = 0.0, price: float = 0.0) -> float:
    """Peněžní tok se správným znaménkem podle druhu pohybu.

    Sdílí ji import i ruční zápis z příkazové řádky. Kdyby každý počítal
    znaménko po svém, dopadne to tak, že se ručně zapsaný nákup chová jinak
    než tentýž nákup z výpisu — a rozdíl se pozná až v součtu.
    """
    smer = SMER[druh]
    if smer is None:
        if not amount:
            raise LedgerError("směna bez částky — ze které strany převodu je tenhle řádek?")
        return amount
    if amount:
        return smer * abs(amount)
    if quantity and price:
        return smer * quantity * price
    raise LedgerError(f"{druh.value} bez částky")


@dataclass
class Vysledek:
    """Co z výpisu vzniklo — a co se do knihy nedostalo."""

    pohyby: list[Transaction] = field(default_factory=list)
    mapovani: dict[str, str] = field(default_factory=dict)
    """Náš sloupec → sloupec ve výpisu. Patří do náhledu: špatně uhodnuté
    mapování je ta chyba, kterou musí uživatel vidět dřív, než uloží."""

    preskocene: list[tuple[int, str]] = field(default_factory=list)
    """Řádky, ze kterých nešel udělat zápis: číslo řádku a důvod."""

    prevedene: dict[str, str] = field(default_factory=dict)
    """ISIN → ticker, jak se převedly. Patří do náhledu jako mapování sloupců."""

    neprevedene: list[str] = field(default_factory=list)
    """ISIN, ke kterým se ticker se správnou měnou nenašel."""


def _bez_diakritiky(text: str) -> str:
    rozlozeno = unicodedata.normalize("NFKD", text)
    return "".join(z for z in rozlozeno if not unicodedata.combining(z))


def _klic(text: str) -> str:
    """Název sloupce na porovnatelný tvar: bez diakritiky, malými, bez balastu."""
    text = _bez_diakritiky(str(text or "")).lower().replace("_", " ")
    return " ".join(text.split()).strip(" :")


def hadej_mapovani(hlavicka: list[str]) -> dict[str, str]:
    """Uhodne, který sloupec výpisu odpovídá kterému našemu.

    Dvě kola: nejdřív přesná shoda názvu, pak podřetězec. Bez toho pořadí
    by sloupec "Currency (Price / share)" sebral ``price`` dřív, než by se
    ke svému jménu dostala ``currency``, a ceny by se načetly z názvu měny.
    Jeden sloupec výpisu se použije nejvýš jednou.
    """
    klice = {sloupec: _klic(sloupec) for sloupec in hlavicka if str(sloupec or "").strip()}
    mapovani: dict[str, str] = {}
    obsazene: set[str] = set()

    def vezmi(nas: str, shoda) -> bool:
        # Prochází se po **aliasech**, ne po sloupcích výpisu: pořadí aliasů
        # je pořadí preference. Výpis, který má vedle sebe ISIN i ticker,
        # tak dá přednost tickeru — pod ním se dají stáhnout ceny, pod ISINem ne.
        for alias in ALIASY[nas]:
            for sloupec, klic in klice.items():
                if sloupec not in obsazene and shoda(alias, klic):
                    mapovani[nas] = sloupec
                    obsazene.add(sloupec)
                    return True
        return False

    # Tři kola podle síly shody, každé přes všechny naše sloupce. Kdyby se
    # rozhodovalo v jednom kole, sebral by "Charge amount" sloupec `amount`
    # dřív, než by na něj došla řada "Total (CZK)" — a poplatek by se zapsal
    # jako celá částka obchodu. Hlavičky nesou podstatné slovo na začátku,
    # takže shoda na začátku má přednost před shodou kdekoli.
    for shoda in (
        lambda alias, klic: alias == klic,
        lambda alias, klic: klic.startswith(alias),
        lambda alias, klic: alias in klic,
    ):
        for nas in ALIASY:
            if nas not in mapovani:
                vezmi(nas, shoda)

    return mapovani


def parse_mapovani(dvojice: list[str] | None) -> dict[str, str]:
    """Přeloží ``--mapovani day=Datum --mapovani type=Akce`` na slovník."""
    out: dict[str, str] = {}
    for dvojice_text in dvojice or []:
        for kus in dvojice_text.split(","):
            if not kus.strip():
                continue
            if "=" not in kus:
                raise LedgerError(f"mapování musí být ve tvaru nas_sloupec=Sloupec ({kus!r})")
            nas, _, cizi = kus.partition("=")
            nas = nas.strip().lower()
            if nas not in ALIASY:
                raise LedgerError(f"{nas!r} není náš sloupec; známe {', '.join(ALIASY)}")
            out[nas] = cizi.strip()
    return out


def cislo(text) -> float:
    """Číslo z buňky výpisu — bez měnových značek a s oběma desetinnými znaky.

    Je-li v čísle tečka i čárka, rozhoduje **ta poslední**: "1,234.56" je
    anglicky, "1.234,56" německy. Je-li tam jen čárka, bere se jako
    desetinná — tak to má český Excel i zbytek téhle knihy. Znamená to, že
    "1,234" je 1,234 a ne tisíc dvě stě; oddělovač tisíců bez desetinné
    části je v exportu vzácný, kdežto české desetinné čárky jsou pravidlo.
    """
    if text is None:
        return 0.0
    surove = _bez_diakritiky(str(text)).strip()
    # Zápisy, kterými výpis myslí "nic", musí projít dřív než cokoli
    # jiného: "-" i "n.a." obsahují znaky, které se jinak čtou jako část
    # čísla, a spadly by až na nečitelném zbytku.
    if surove.lower() in PRAZDNE_ZAPISY:
        return 0.0
    # Měnové značky, nedělitelné mezery, apostrofy (švýcarský zápis tisíců).
    zbytek = "".join(z for z in surove if z.isdigit() or z in ",.-+")
    if not any(z.isdigit() for z in zbytek):
        # Text nulou není. Kdyby se sem dostal namapovaný textový sloupec,
        # tiše by z něj vznikly samé nuly a import by vypadal, že proběhl
        # v pořádku — přesně ten druh chyby, kvůli které je náhled
        # výchozím chováním.
        raise LedgerError(f"{text!r} není číslo")
    if "," in zbytek and "." in zbytek:
        desetinny = max(zbytek.rfind(","), zbytek.rfind("."))
        cela = "".join(z for z in zbytek[:desetinny] if z.isdigit() or z in "-+")
        return float(f"{cela}.{zbytek[desetinny + 1:]}")
    try:
        return float(zbytek.replace(",", "."))
    except ValueError as exc:
        raise LedgerError(f"{text!r} není číslo") from exc


def datum(text, format_: str | None = None) -> date:
    """Datum z buňky výpisu.

    Umí ISO (``2024-01-15``, i s časem) a český zápis (``15.1.2024``).
    Lomítka **odmítá**: ``01/02/2024`` je 1. února pro Evropana a 2. ledna
    pro Američana a uhodnout to z jednoho řádku nejde. Tichý překlep by
    posunul celý časový test a kurz — proto radši hláška a ``--datum-format``.
    """
    surove = str(text or "").strip()
    if not surove:
        raise LedgerError("prázdné datum")
    if format_:
        return datetime.strptime(surove, format_).date()

    hlavni = surove.replace("T", " ").split(" ")[0]
    try:
        return date.fromisoformat(hlavni)
    except ValueError:
        pass
    for vzor in ("%d.%m.%Y", "%d.%m.%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(hlavni, vzor).date()
        except ValueError:
            continue
    if "/" in hlavni:
        raise LedgerError(
            f"{surove!r}: datum s lomítky je dvojznačné (01/02/2024 je 1. února i 2. ledna). "
            "Zadejte --datum-format, např. '%d/%m/%Y'"
        )
    raise LedgerError(f"{surove!r}: datu nerozumím, čekám YYYY-MM-DD nebo DD.MM.RRRR")


def typ(text) -> TxType:
    """Druh pohybu z buňky výpisu."""
    klic = _klic(text)
    if not klic:
        raise LedgerError("prázdný druh pohybu")
    if klic in TYPY_PRESNE:
        return TYPY_PRESNE[klic]
    for kus, druh in TYPY_PODRETEZCE:
        if kus in klic:
            return druh
    raise LedgerError(f"{text!r}: neznámý druh pohybu")


def nacti_vypis(
    path: str | Path,
    mapovani: dict[str, str] | None = None,
    *,
    vychozi_mena: str = "USD",
    datum_format: str | None = None,
    prekladac=None,
) -> Vysledek:
    """Přečte cizí CSV a udělá z něj pohyby. Do knihy nic nezapisuje.

    ``mapovani`` doplňuje (a přebíjí) to uhodnuté — stačí tedy zadat ručně
    jen sloupce, které se netrefily.
    """
    path = Path(path)
    if not path.exists():
        raise LedgerError(f"výpis {path} neexistuje")

    text = path.read_text(encoding="utf-8-sig")
    radky = [r for r in text.splitlines() if not r.lstrip().startswith("#")]
    if not radky:
        raise LedgerError(f"výpis {path} je prázdný")
    oddelovac = Ledger._oddelovac("\n".join(radky))

    ctecka = csv.DictReader(radky, delimiter=oddelovac)
    hlavicka = list(ctecka.fieldnames or [])
    plne = hadej_mapovani(hlavicka)
    plne.update(mapovani or {})

    chybi = [s for s in POVINNE if s not in plne]
    if chybi:
        raise LedgerError(
            f"ve výpisu nepoznávám sloupec {', '.join(chybi)} — hlavička je "
            f"{', '.join(hlavicka) or '(prázdná)'}. Doplňte --mapovani day=NázevSloupce"
        )
    nezname = [nas for nas, cizi in plne.items() if cizi not in hlavicka]
    if nezname:
        raise LedgerError(
            f"sloupec {plne[nezname[0]]!r} ({nezname[0]}) ve výpisu není; hlavička je "
            f"{', '.join(hlavicka)}"
        )

    vysledek = Vysledek(mapovani=plne)
    for cislo_radku, radek in enumerate(ctecka, start=2):
        if not any((v or "").strip() for v in radek.values() if isinstance(v, str)):
            continue
        try:
            vysledek.pohyby.append(_radek_na_pohyb(radek, plne, vychozi_mena, datum_format))
        except (LedgerError, ValueError, KeyError) as exc:
            # Nečitelný řádek nesmí zahodit celý import: typicky je to
            # souhrnný řádek na konci výpisu nebo pohyb, který nevedeme
            # (převod mezi vlastními účty). Vypsat ho ale musíme — jinak
            # bude v knize chybět a nikdo nepozná co.
            vysledek.preskocene.append((cislo_radku, str(exc)))
    prevest_isin(vysledek, prekladac)
    return vysledek


def prevest_isin(vysledek: Vysledek, prekladac=None) -> None:
    """Nahradí ISIN tickerem, podle kterého jdou stáhnout ceny.

    Původní ISIN zůstane v poznámce, ať jde pohyb dohledat ve výpisu banky.
    Co se převést nepodaří, zůstane jako ISIN a přizná se — pozice se pak
    ocení nákupní cenou, což přehled taky řekne.
    """
    from . import isin as isin_module

    prekladac = prekladac or isin_module.preloz
    for i, tx in enumerate(vysledek.pohyby):
        if not tx.symbol or not isin_module.je_isin(tx.symbol):
            continue
        ticker = prekladac(tx.symbol, tx.currency)
        if not ticker:
            if tx.symbol not in vysledek.neprevedene:
                vysledek.neprevedene.append(tx.symbol)
            continue
        vysledek.prevedene[tx.symbol] = ticker
        poznamka = f"ISIN {tx.symbol}" + (f"; {tx.note}" if tx.note else "")
        vysledek.pohyby[i] = replace(tx, symbol=ticker, note=poznamka)


def _radek_na_pohyb(
    radek: dict, mapovani: dict[str, str], vychozi_mena: str, datum_format: str | None
) -> Transaction:
    hodnota = lambda nas: radek.get(mapovani[nas]) if nas in mapovani else None  # noqa: E731

    druh = typ(hodnota("type"))
    quantity = abs(cislo(hodnota("quantity")))
    price = abs(cislo(hodnota("price")))
    fee = abs(cislo(hodnota("fee")))
    amount = cislo(hodnota("amount"))

    # Znaménko z výpisu se zahazuje a odvozuje z druhu pohybu. Výpis, který
    # má nákup kladně, by jinak vyrobil zápis tvrdící, že nákup přinesl
    # peníze. Jediná výjimka je směna — viz ``castka``.
    amount = castka(druh, amount, quantity, price)

    mena = (str(hodnota("currency") or "").strip() or vychozi_mena).upper()
    return Transaction(
        day=datum(hodnota("day"), datum_format),
        type=druh,
        amount=amount,
        symbol=(str(hodnota("symbol") or "").strip().upper() or None),
        quantity=quantity,
        price=price,
        fee=fee,
        currency=mena,
        note=str(hodnota("note") or "").strip(),
    )
