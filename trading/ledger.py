"""Evidence skutečného portfolia — co jste opravdu koupili a za kolik.

Tenhle modul stojí **vedle** ``portfolio.py``, ne místo něj. Ten je
účetnictvím backtestu: zná hotovost, pozice a obchody, které vymyslela
strategie. Tohle je evidence skutečných peněz a zná věci, které backtest
nikdy nepotká — dividendy, srážkovou daň, vklady, výběry, poplatek za
převod, prodej po částech.

**Nic nedoporučuje a nic neobchoduje.** Je to účetní kniha: zapisuje, co
jste udělali, a počítá, jak to dopadlo. Co koupit, rozhodujete vy.

Dvě věci, na kterých tady stojí správnost:

*Párování FIFO.* Prodáte-li polovinu pozice nakupované na třikrát, je
podstatné, *které* kusy odešly — kvůli zisku i kvůli časovému testu.
Prodává se od nejstaršího nákupu, protože tak to dělá i finanční úřad
a protože průměrná cena by tříletý test rozmazala na nesmysl.

*Poplatky jsou součástí ceny.* Nákupní poplatek zvyšuje pořizovací cenu,
prodejní snižuje výnos. Vést je zvlášť a hlásit "zisk" bez nich je přesně
ten druh čísla, kvůli kterému člověk obchoduje víc, než se mu vyplácí.

*Hotovost se vede po měnách.* Kdo inkasuje dividendu v dolarech a nechá ji
ležet, drží dál měnovou sázku — a evidence, která zná jen pozice, o ní neví.
Zůstatky proto vznikají přehráním knihy (``cash``) a v korunovém ocenění se
přepočítávají **dnešním** kurzem proti kurzu dne, kdy peníze přitekly.

Peníze se počítají v ``float``, stejně jako ve zbytku projektu. Na evidenci
řádu statisíců to stačí; zaokrouhluje se až při výpisu.

Ocenění existuje ve dvou podobách. ``valuation`` počítá v měně zápisů
a o kurzech nic neví — je to nejjednodušší pohled a pro účet vedený
v jediné měně stačí. ``valuation_czk`` převede všechno do korun ke
**dni, kdy se to stalo**, a hlavně rozdělí zhodnocení na výkon aktiva
a pohyb kurzu. Bez toho rozdělení je korunová evidence zavádějící: kdo
vydělal na titulu deset procent a přišel o deset na kurzu, vidí nulu
a nemá jak poznat, že to byly dvě velké věci proti sobě.
"""

from __future__ import annotations

import csv
import logging
import os
import statistics
import tempfile
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


class ZdrojKurzu(Protocol):
    """Cokoli, co umí říct kurz měny k datu.

    Záměrně minimální rozhraní: kniha nemá vědět, jestli kurz přišel
    z ČNB, z cache nebo z tabulky v testu. Chybějící kurz se hlásí
    výjimkou z rodiny ``LookupError`` — ``fx.KurzChybi`` z ní dědí.
    """

    def kurz(self, mena: str, den: date) -> float: ...

# Časový test podle §4 zákona o daních z příjmů: cenné papíry držené déle
# než tři roky jsou osvobozené. Konstanta je tady proto, aby se dala změnit
# na jednom místě, až se zákon zase pohne.
CASOVY_TEST = timedelta(days=3 * 365 + 1)


class LedgerError(RuntimeError):
    """Zápis, který nedává smysl — a tiše ho spolknout by falšovalo účet."""


class TxType(str, Enum):
    """Druh pohybu.

    Vklad a výběr nejsou obchody: nemění hodnotu portfolia, jen kolik
    vlastních peněz do něj natekla. Bez nich se nedá spočítat, jestli
    jste vydělali, nebo jen víc vložili.
    """

    DEPOSIT = "vklad"
    WITHDRAWAL = "vyber"
    BUY = "nakup"
    SELL = "prodej"
    DIVIDEND = "dividenda"
    TAX = "dan"
    FEE = "poplatek"
    EXCHANGE = "smena"
    """Převod mezi měnami. **Jeden řádek je jedna strana převodu**, takže
    směna se zapisuje jako dvojice: ``-24 500 CZK`` a ``+1 000 USD``.

    Vypadá to jako práce navíc, ale jinak by řádek nesl dvě měny a celá
    kniha by musela mít druhý měnový sloupec. Navíc je díky tomu vidět
    kurzový spread brokera: sečtou-li se obě strany kurzem ČNB toho dne,
    nevyjde nula, ale přesně to, co si broker vzal."""


#: Pohyby, které se vážou ke konkrétnímu titulu.
_S_TITULEM = {TxType.BUY, TxType.SELL, TxType.DIVIDEND}


@dataclass(frozen=True)
class Transaction:
    """Jeden řádek účetní knihy.

    ``quantity`` a ``price`` dávají smysl jen u nákupu a prodeje; u dividend,
    daní a poplatků nese celou informaci ``amount``. Držíme obojí schválně:
    dopočítávat částku z ceny a počtu kusů znamená, že se výpis od brokera
    o pár haléřů rozejde s naší evidencí a nikdo nepozná proč.
    """

    day: date
    type: TxType
    amount: float
    """Peněžní tok v měně účtu. Kladný = přiteklo, záporný = odteklo."""

    symbol: str | None = None
    quantity: float = 0.0
    price: float = 0.0
    fee: float = 0.0
    currency: str = "USD"
    note: str = ""

    def __post_init__(self) -> None:
        if self.type in _S_TITULEM and not self.symbol:
            raise LedgerError(f"{self.type.value} bez symbolu ({self.day})")
        if self.type in (TxType.BUY, TxType.SELL):
            if self.quantity <= 0:
                raise LedgerError(f"{self.type.value} {self.symbol}: počet kusů musí být kladný")
            if self.price < 0:
                raise LedgerError(f"{self.type.value} {self.symbol}: záporná cena")
        if self.fee < 0:
            raise LedgerError(f"záporný poplatek ({self.day})")
        if self.type is TxType.EXCHANGE and not self.amount:
            # Nulová strana směny je vždycky překlep — a tichá nula by
            # z dvojice udělala jednostranný pohyb, tedy vyrobené peníze.
            raise LedgerError(f"směna s nulovou částkou ({self.day})")

    @property
    def key(self) -> tuple:
        """Otisk pro rozpoznání duplicit při opakovaném importu.

        Nahrát stejný výpis dvakrát je běžná nehoda a zdvojené obchody
        by tiše nafoukly portfolio. Datum, typ, titul a částka stačí —
        dva skutečně různé obchody se stejnými hodnotami týž den jsou
        vzácné a jejich sloučení je menší škoda než zdvojení všeho.
        """
        return (self.day, self.type, self.symbol, self.quantity, self.price, self.amount)


@dataclass
class Lot:
    """Zbývající část jednoho nákupu.

    Časový test se počítá od data pořízení *téhle* dávky, ne od prvního
    nákupu titulu. Kdo přikupoval, má každou dávku osvobozenou jindy.
    """

    day: date
    symbol: str
    quantity: float
    cost_per_unit: float
    """Pořizovací cena za kus **včetně poměrné části nákupního poplatku**."""

    currency: str = "USD"
    """Měna nákupu. Dávka si ji nese s sebou, protože kurz se bere k datu
    pořízení *téhle* dávky — a kdo přikupoval, má každou pořízenou jiným
    kurzem."""

    @property
    def cost(self) -> float:
        return self.quantity * self.cost_per_unit

    def osvobozeno_od(self) -> date:
        """Datum, od kterého prodej téhle dávky projde časovým testem."""
        return self.day + CASOVY_TEST


@dataclass(frozen=True)
class Realized:
    """Uzavřená část pozice — spárovaný nákup s prodejem."""

    symbol: str
    quantity: float
    bought: date
    sold: date
    cost: float
    proceeds: float
    """Výnos z prodeje po odečtení poměrné části prodejního poplatku."""

    currency: str = "USD"

    @property
    def pnl(self) -> float:
        return self.proceeds - self.cost

    @property
    def held_days(self) -> int:
        return (self.sold - self.bought).days

    @property
    def prosel_casovym_testem(self) -> bool:
        """Držení delší než tři roky. **Není to daňové poradenství** —
        je to výpočet z data nákupu a prodeje, který si nechte ověřit.
        Osvobození má i další podmínky, které tenhle modul nezná.
        """
        return self.sold - self.bought >= CASOVY_TEST


#: Kolikanásobek obvyklé (mediánové) velikosti pohybu už je podezřelý.
#: Dvacetinásobek projde jednorázovému velkému vkladu mezi drobnými nákupy,
#: ale neprojde překlepu o řád — a právě ten hledáme.
_SKOK = 20.0


@dataclass(frozen=True)
class Nalez:
    """Jedna věc, která v knize nesedí.

    ``zavaznost`` je ``"chyba"`` (kniha si odporuje sama se sebou) nebo
    ``"podezreni"`` (může být v pořádku, ale stojí za ověření). Rozlišení
    není kosmetika: kontrola, která hlásí i správné zápisy jako chybu, se
    přestane číst — a s ní i ty skutečné.
    """

    zavaznost: str
    den: date | None
    zprava: str

    def __str__(self) -> str:
        znacka = "CHYBA " if self.zavaznost == "chyba" else "pozor "
        return f"{znacka} {self.den or '':<12} {self.zprava}"


@dataclass
class Ledger:
    """Účetní kniha skutečného portfolia."""

    transactions: list[Transaction] = field(default_factory=list)

    komentare: list[str] = field(default_factory=list)
    """Řádky s ``#`` z hlavičky souboru. Držíme je proto, že vzor knihy je
    z poloviny nápověda — a příkaz, který připíše jeden pohyb a přitom
    smaže dokumentaci k formátu, si uživatel podruhé nespustí."""

    oddelovac: str = ","
    """Oddělovač, kterým soubor přišel. Přepsat českému Excelu středníky
    na čárky znamená, že se mu kniha při dalším otevření rozsype."""

    # --- zápis ----------------------------------------------------------

    def add(self, tx: Transaction) -> bool:
        """Zapíše pohyb. Vrací ``False``, byl-li to duplikát.

        Kniha se drží setříděná podle data — FIFO párování jinak dá
        nesmysl, kdyby se starší obchod dopsal až dodatečně.
        """
        if any(t.key == tx.key for t in self.transactions):
            logger.info("duplicitní pohyb %s %s, přeskakuji", tx.day, tx.type.value)
            return False
        self.transactions.append(tx)
        self.transactions.sort(key=lambda t: (t.day, t.type.value))
        return True

    def extend(self, txs: list[Transaction]) -> int:
        """Přidá dávku pohybů. Vrací počet skutečně zapsaných."""
        return sum(self.add(tx) for tx in txs)

    # --- načtení a uložení ----------------------------------------------

    SLOUPCE = ("day", "type", "symbol", "quantity", "price", "amount", "fee", "currency", "note")

    @staticmethod
    def _oddelovac(ukazka: str) -> str:
        """Uhodne oddělovač sloupců.

        Český Excel a výpisy českých brokerů exportují středníkem, protože
        čárka je desetinná. Načíst takový soubor jako oddělený čárkou dá
        rozsekané řádky a hlášku o zápisu, který ve skutečnosti sedí.
        """
        hlavicka = ukazka.splitlines()[0] if ukazka else ""
        return ";" if hlavicka.count(";") > hlavicka.count(",") else ","

    @classmethod
    def from_csv(cls, path: str | Path) -> Ledger:
        """Načte knihu z CSV. Řádky začínající ``#`` se ignorují."""
        path = Path(path)
        if not path.exists():
            raise LedgerError(f"kniha {path} neexistuje")

        text = path.read_text(encoding="utf-8")
        oddelovac = cls._oddelovac("\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        ))

        ledger = cls(komentare=_hlavickove_komentare(text), oddelovac=oddelovac)
        with path.open(encoding="utf-8", newline="") as handle:
            rows = (line for line in handle if not line.lstrip().startswith("#"))
            for line_no, row in enumerate(csv.DictReader(rows, delimiter=oddelovac), start=2):
                if not row.get("day"):
                    continue
                try:
                    ledger.add(cls._row_to_tx(row))
                except (LedgerError, ValueError, KeyError) as exc:
                    raise LedgerError(f"{path}:{line_no}: {exc}") from exc
        logger.info("kniha %s: %d pohybů", path, len(ledger.transactions))
        return ledger

    @staticmethod
    def _row_to_tx(row: dict) -> Transaction:
        cislo = lambda k: float(str(row.get(k) or 0).replace(",", ".").replace(" ", ""))  # noqa: E731
        return Transaction(
            day=datetime.strptime(row["day"].strip()[:10], "%Y-%m-%d").date(),
            type=TxType(row["type"].strip().lower()),
            amount=cislo("amount"),
            symbol=(row.get("symbol") or "").strip().upper() or None,
            quantity=cislo("quantity"),
            price=cislo("price"),
            fee=cislo("fee"),
            currency=(row.get("currency") or "USD").strip().upper(),
            note=(row.get("note") or "").strip(),
        )

    def to_csv(self, path: str | Path) -> None:
        """Uloží knihu. Přepisuje celý soubor — je zdrojem pravdy.

        Zapisuje se **do dočasného souboru vedle a teprve pak přejmenuje**.
        Kdyby se psalo rovnou, stačí plný disk, Ctrl-C nebo výjimka uprostřed
        a z evidence zbude půlka — přesně ten soubor, ze kterého se pak
        nedá zjistit, co v něm chybělo. ``os.replace`` je na jednom svazku
        atomický, takže na cílovém místě je vždycky buď stará, nebo celá
        nová kniha.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Dočasný soubor musí být ve stejném adresáři: přes hranici svazku
        # (/tmp vs. domovský adresář) přejmenování atomické není.
        handle_fd, docasny = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
        )
        try:
            with open(handle_fd, "w", encoding="utf-8", newline="") as handle:
                for radek in self.komentare:
                    handle.write(radek.rstrip("\n") + "\n")
                writer = csv.writer(handle, delimiter=self.oddelovac)
                writer.writerow(self.SLOUPCE)
                for tx in self.transactions:
                    writer.writerow(
                        [
                            tx.day.isoformat(),
                            tx.type.value,
                            tx.symbol or "",
                            _do_csv(tx.quantity),
                            _do_csv(tx.price),
                            f"{tx.amount:.2f}",
                            _do_csv(tx.fee),
                            tx.currency,
                            tx.note,
                        ]
                    )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(docasny, path)
        except BaseException:
            # I při Ctrl-C: nedopsaný soubor po sobě uklidit, ať se příště
            # nepoznává, který ze dvou je ten pravý.
            Path(docasny).unlink(missing_ok=True)
            raise

    # --- výpočty --------------------------------------------------------
    #
    # Všechno níž je čistá funkce nad seznamem pohybů. Žádné ceny z internetu,
    # žádný stav — díky tomu jde celá evidence testovat bez sítě.

    def _prehraj(self) -> tuple[dict[str, list[Lot]], list[Realized]]:
        """Přehraje knihu a spáruje prodeje s nákupy metodou FIFO."""
        lots: dict[str, list[Lot]] = {}
        realized: list[Realized] = []

        for tx in self.transactions:
            if tx.type is TxType.BUY:
                # Poplatek je součástí pořizovací ceny, ne samostatná ztráta.
                cena_za_kus = (tx.quantity * tx.price + tx.fee) / tx.quantity
                lots.setdefault(tx.symbol, []).append(
                    Lot(tx.day, tx.symbol, tx.quantity, cena_za_kus, tx.currency)
                )

            elif tx.type is TxType.SELL:
                fronta = lots.get(tx.symbol, [])
                drzeno = sum(lot.quantity for lot in fronta)
                if tx.quantity > drzeno + 1e-9:
                    raise LedgerError(
                        f"{tx.day} prodej {tx.quantity} ks {tx.symbol}, ale drženo jen {drzeno}"
                    )
                # Prodejní poplatek snižuje výnos, rozpočítaný na kusy.
                vynos_za_kus = (tx.quantity * tx.price - tx.fee) / tx.quantity
                zbyva = tx.quantity
                while zbyva > 1e-9:
                    lot = fronta[0]
                    kusu = min(lot.quantity, zbyva)
                    realized.append(
                        Realized(
                            symbol=tx.symbol,
                            quantity=kusu,
                            bought=lot.day,
                            sold=tx.day,
                            cost=kusu * lot.cost_per_unit,
                            proceeds=kusu * vynos_za_kus,
                            # Měna dávky, ne prodeje. Prodej cizí měny za
                            # jinou měnu je samostatný obchod, který tahle
                            # kniha neumí — a předstírat opak by rozpad
                            # zhodnocení tiše rozhodil.
                            currency=lot.currency,
                        )
                    )
                    lot.quantity -= kusu
                    zbyva -= kusu
                    if lot.quantity <= 1e-9:
                        fronta.pop(0)

        return lots, realized

    def positions(self) -> list[Lot]:
        """Otevřené dávky, nejstarší napřed."""
        lots, _ = self._prehraj()
        return sorted((lot for fronta in lots.values() for lot in fronta), key=lambda x: x.day)

    def holdings(self) -> dict[str, float]:
        """Kolik kusů kterého titulu držíte."""
        out: dict[str, float] = {}
        for lot in self.positions():
            out[lot.symbol] = out.get(lot.symbol, 0.0) + lot.quantity
        return out

    def realized(self) -> list[Realized]:
        """Uzavřené obchody spárované FIFO."""
        return self._prehraj()[1]

    def cash_flow(self) -> dict[str, float]:
        """Rozpad peněžních toků, které nejsou pohybem ceny.

        ``vlozeno`` je klíčové číslo celé evidence: proti němu se poměřuje
        hodnota portfolia. Bez něj vypadá každý přírůstek jako výdělek,
        i když jste jen přisypali.
        """
        soucet = lambda typ: sum(t.amount for t in self.transactions if t.type is typ)  # noqa: E731
        vklady, vybery = soucet(TxType.DEPOSIT), soucet(TxType.WITHDRAWAL)
        return {
            "vklady": vklady,
            "vybery": vybery,
            "vlozeno": vklady + vybery,
            "dividendy": soucet(TxType.DIVIDEND),
            "dane": soucet(TxType.TAX),
            # Poplatky zapsané samostatně (vedení účtu, převod). Poplatky
            # z nákupů a prodejů jsou už v pořizovací ceně a výnosu.
            "poplatky_mimo_obchody": soucet(TxType.FEE),
            "poplatky_v_obchodech": -sum(
                t.fee for t in self.transactions if t.type in (TxType.BUY, TxType.SELL)
            ),
        }

    @staticmethod
    def _tok(tx: Transaction) -> float:
        """Kolik peněz pohyb odčerpal nebo přinesl — v měně toho pohybu.

        U nákupu a prodeje se **nepoužije sloupec ``amount``**, ale dopočte
        se z počtu kusů, ceny a poplatku. Je to schválně: přesně takhle
        vzniká i pořizovací cena dávky a výnos z prodeje, takže zůstatek
        hotovosti a pořizovací cena drží pohromadě. Kdyby se hotovost brala
        z ``amount``, stačí, aby broker ve výpisu uváděl částku bez poplatku
        (a někteří ano), a zůstatek by se rozcházel o poplatky — pomalu
        a nepozorovaně.
        """
        if tx.type is TxType.BUY:
            return -(tx.quantity * tx.price + tx.fee)
        if tx.type is TxType.SELL:
            return tx.quantity * tx.price - tx.fee
        return tx.amount

    def cash(self) -> dict[str, float]:
        """Zůstatek hotovosti po měnách — doslova to, co je v knize.

        Nic nedopočítává a nic nenarovnává: chybí-li v knize zápis směny,
        vyjde dolarový zůstatek záporný. Je to užitečné, ne vadné — přesně
        tak se pozná neúplná kniha (viz ``kontrola``). Korunové ocenění si
        pak chybějící směnu dopočte samo, protože bez ní by nešlo spočítat
        vůbec nic.
        """
        out: dict[str, float] = {}
        for tx in self.transactions:
            mena = (tx.currency or "").upper()
            out[mena] = out.get(mena, 0.0) + self._tok(tx)
        # Nuly z vyrovnaných měn jen zaplevelují výpis; drobný zbytek
        # z plovoucí čárky není zůstatek, ale zaokrouhlovací šum.
        return {m: z for m, z in out.items() if abs(z) > 1e-9}

    def valuation(self, prices: dict[str, float]) -> dict:
        """Ocenění portfolia k daným cenám.

        Ceny se předávají zvenčí, ne stahují uvnitř — evidence se tak dá
        spočítat i bez sítě a v testu bez jediného mocku. Chybí-li cena
        titulu, ocení se pořizovací cenou a přizná se to v ``bez_ceny``.
        """
        lots = self.positions()
        toky = self.cash_flow()
        realized = self.realized()

        hodnota = 0.0
        porizovaci = 0.0
        bez_ceny: list[str] = []
        for lot in lots:
            cena = prices.get(lot.symbol)
            if cena is None:
                bez_ceny.append(lot.symbol)
                cena = lot.cost_per_unit
            hodnota += lot.quantity * cena
            porizovaci += lot.cost

        realizovany = sum(r.pnl for r in realized)
        nerealizovany = hodnota - porizovaci

        return {
            "hodnota": hodnota,
            "porizovaci_cena": porizovaci,
            "nerealizovany_zisk": nerealizovany,
            "realizovany_zisk": realizovany,
            "dividendy": toky["dividendy"],
            "dane": toky["dane"],
            # Poplatky ze samostatných řádků (vedení účtu, převod). Poplatky
            # z nákupů a prodejů se tady záměrně neobjeví — jsou už uvnitř
            # pořizovací ceny a výnosu, takže přičíst je ještě sem by je
            # odečetlo dvakrát a rozpad by nedával součet.
            "poplatky": toky["poplatky_mimo_obchody"],
            "poplatky_v_obchodech": toky["poplatky_v_obchodech"],
            "vlozeno": toky["vlozeno"],
            # Celkový výsledek proti vloženým penězům. Tohle je jediné číslo,
            # které opravdu platí — všechno ostatní je jeho rozpad.
            "celkem": nerealizovany
            + realizovany
            + toky["dividendy"]
            + toky["dane"]
            + toky["poplatky_mimo_obchody"],
            # ^ součet přesně těch položek, které výpis ukazuje nad čarou
            "pozic": len({lot.symbol for lot in lots}),
            "bez_ceny": sorted(set(bez_ceny)),
            # Hotovost jen informativně: v jedné měně žádný kurzový rozdíl
            # nevzniká, takže na "celkem" nemá co měnit. Přepočet drženého
            # dolaru na koruny umí až ``valuation_czk``.
            "hotovost": self.cash(),
        }

    # --- ocenění v korunách ---------------------------------------------

    def valuation_czk(
        self,
        prices: dict[str, float],
        kurzy: ZdrojKurzu,
        k_datu: date | None = None,
        domaci: str = "CZK",
    ) -> dict:
        """Ocenění v domácí měně s rozpadem na výkon aktiva a pohyb kurzu.

        ``kurzy`` je cokoli s metodou ``kurz(mena, den) -> float``; kurz je
        "kolik korun stojí jeden kus cizí měny". Zdroj se předává zvenčí ze
        stejného důvodu jako ceny — evidence tak jde spočítat bez sítě.

        **Kurz se bere k datu pohybu, ne dnešní na všechno.** Přepočíst
        nákup z roku 2020 dnešním kurzem je táž chyba jako pohled do
        budoucnosti v backtestu: použije informaci, kterou jste tehdy
        neměli, a systematicky posune výsledek.

        Rozpad zhodnocení stojí na jedné rovnosti, kterou hlídá test::

            výkon aktiva + pohyb kurzu == nerealizovaný + realizovaný zisk

        Zisk v korunách je ``q·c₁·r₁ − q·c₀·r₀`` a ten se dá rozdělit
        dvěma stejně platnými způsoby — smíšený člen ``q·(c₁−c₀)·(r₁−r₀)``
        musí někam. Dáváme ho ke kurzu, takže:

        * **výkon aktiva** ``q·(c₁−c₀)·r₀`` je "kolik byste vydělali,
          kdyby kurz stál" — přesně ten myšlený pokus, kvůli kterému se
          na rozpad člověk dívá;
        * **pohyb kurzu** ``q·c₁·(r₁−r₀)`` je změna kurzu na *dnešní*
          hodnotě pozice, tedy na částce, která je kurzu vystavená teď.

        Ta volba není jediná možná a je dobré o ní vědět; podstatné je, že
        se obě části sečtou přesně na celek a ani koruna se nepočítá dvakrát.

        **Hotovost je součástí výsledku.** Nevyměněná dolarová hotovost je pořád
        měnová sázka, takže se přeceňuje dnešním kurzem proti kurzu dne, kdy
        přitekla; rozdíl je v ``kurzovy_rozdil_hotovosti``. Bez toho by
        dividenda inkasovaná v dolarech a ponechaná v dolarech ztuhla na
        kurzu dne výplaty a další pohyb kurzu by na ní nebyl vidět.

        Celý výpočet stojí na jedné identitě, kterou hlídá test::

            celkem == hodnota pozic + hotovost − vloženo

        a ``celkem`` je zároveň součet položek, které výpis ukazuje nad
        čarou. Kdyby se rozešly, je někde peníz započtený dvakrát.
        """
        k_datu = k_datu or date.today()
        lots, realized = self._prehraj()
        vsechny_lots = sorted(
            (lot for fronta in lots.values() for lot in fronta), key=lambda x: (x.symbol, x.day)
        )

        bez_kurzu: set[str] = set()

        def prepocet(mena: str, den: date) -> float | None:
            """Kurz, nebo ``None`` s poznámkou. Dosadit 1.0 by byla lež."""
            mena = (mena or domaci).upper()
            if mena == domaci.upper():
                return 1.0
            try:
                return float(kurzy.kurz(mena, den))
            except LookupError:
                bez_kurzu.add(mena)
                return None

        hodnota = porizovaci = 0.0
        ner_aktivum = ner_kurz = 0.0
        bez_ceny: list[str] = []
        pozice: list[dict] = []

        for lot in vsechny_lots:
            kurz_nakup = prepocet(lot.currency, lot.day)
            kurz_dnes = prepocet(lot.currency, k_datu)
            if kurz_nakup is None or kurz_dnes is None:
                # Bez kurzu se dávka do korunových součtů nezapočítá vůbec.
                # Jinak by se rovnost rozpadu držela jen zdánlivě.
                continue

            cena = prices.get(lot.symbol)
            if cena is None:
                bez_ceny.append(lot.symbol)
                cena = lot.cost_per_unit

            cost = lot.quantity * lot.cost_per_unit * kurz_nakup
            value = lot.quantity * cena * kurz_dnes
            aktivum = lot.quantity * (cena - lot.cost_per_unit) * kurz_nakup
            kurzove = lot.quantity * cena * (kurz_dnes - kurz_nakup)

            hodnota += value
            porizovaci += cost
            ner_aktivum += aktivum
            ner_kurz += kurzove
            pozice.append(
                {
                    "symbol": lot.symbol,
                    "quantity": lot.quantity,
                    "mena": lot.currency,
                    "nakoupeno": lot.day.isoformat(),
                    "cena_za_kus": lot.cost_per_unit,
                    "cena": cena,
                    "kurz_nakup": kurz_nakup,
                    "kurz_dnes": kurz_dnes,
                    "porizovaci_cena": cost,
                    "hodnota": value,
                    "zisk": value - cost,
                    "zisk_pct": (value - cost) / cost * 100.0 if cost else 0.0,
                    "vykon_aktiva": aktivum,
                    "kurzovy_rozdil": kurzove,
                    "bez_ceny": lot.symbol in bez_ceny,
                }
            )

        real_aktivum = real_kurz = realizovany = 0.0
        for obchod in realized:
            kurz_nakup = prepocet(obchod.currency, obchod.bought)
            kurz_prodej = prepocet(obchod.currency, obchod.sold)
            if kurz_nakup is None or kurz_prodej is None:
                continue
            realizovany += obchod.proceeds * kurz_prodej - obchod.cost * kurz_nakup
            real_aktivum += (obchod.proceeds - obchod.cost) * kurz_nakup
            real_kurz += obchod.proceeds * (kurz_prodej - kurz_nakup)

        toky = self._toky_czk(prepocet)
        hotovost = self._hotovost_czk(prepocet, k_datu, domaci)
        nerealizovany = hodnota - porizovaci

        return {
            "mena": domaci.upper(),
            "k_datu": k_datu.isoformat(),
            "hodnota": hodnota,
            "porizovaci_cena": porizovaci,
            "nerealizovany_zisk": nerealizovany,
            "realizovany_zisk": realizovany,
            "dividendy": toky["dividendy"],
            "dane": toky["dane"],
            "poplatky": toky["poplatky_mimo_obchody"],
            "poplatky_v_obchodech": toky["poplatky_v_obchodech"],
            "vlozeno": toky["vlozeno"],
            # Kurzový rozdíl na nevyměněné hotovosti a spread ze zapsaných
            # směn. Obojí jsou skutečné koruny, takže patří do "celkem" —
            # a s nimi platí identita
            #     celkem == hodnota pozic + hotovost − vloženo.
            "hotovost": hotovost["hotovost"],
            "hotovost_meny": hotovost["hotovost_meny"],
            "kurzovy_rozdil_hotovosti": hotovost["kurzovy_rozdil_hotovosti"],
            "smeny": hotovost["smeny"],
            "dopoctene_smeny": hotovost["dopoctene_smeny"],
            "celkem": (
                nerealizovany
                + realizovany
                + toky["dividendy"]
                + toky["dane"]
                + toky["poplatky_mimo_obchody"]
                + hotovost["smeny"]
                + hotovost["kurzovy_rozdil_hotovosti"]
            ),
            # Druhý rozpad **téhož** zhodnocení z pozic — podle příčiny, ne
            # podle druhu. Tyhle dvě položky se sčítají na (nerealizovaný +
            # realizovaný), ne na "celkem": dividendy, daně ani poplatky mimo
            # obchody nemají s pohybem ceny titulu co dělat. Přičíst je
            # k předchozímu rozpadu by započetlo tytéž peníze dvakrát.
            "vykon_aktiva": ner_aktivum + real_aktivum,
            "kurzovy_rozdil": ner_kurz + real_kurz,
            "vykon_aktiva_nerealizovany": ner_aktivum,
            "kurzovy_rozdil_nerealizovany": ner_kurz,
            "pozic": len({p["symbol"] for p in pozice}),
            "pozice": pozice,
            "meny": self._expozice(pozice),
            "kurzy": self._kurzy_k_datu(prepocet, k_datu),
            "bez_ceny": sorted(set(bez_ceny)),
            # Měny, pro které kurz nebyl. Jejich pohyby v součtech **nejsou** —
            # radši chybějící řádek než tichý přepočet kurzem 1:1.
            "bez_kurzu": sorted(bez_kurzu),
        }

    def _hotovost_czk(self, prepocet, k_datu: date, domaci: str) -> dict:
        """Hotovost po měnách, přeceněná dnešním kurzem.

        Vrací dvě čísla, na kterých stojí oprava staré díry:

        * ``hotovost`` — kolik korun by dnes bylo, kdyby se všechny zůstatky
          vyměnily za koruny;
        * ``kurzovy_rozdil_hotovosti`` — rozdíl proti tomu, kolik korun ty
          peníze představovaly **v den, kdy přitekly**. Přesně tenhle kus
          výsledku dřív chyběl: dolarová dividenda ponechaná v dolarech
          ztuhla na kurzu dne výplaty.

        **Chybějící směna se dopočítá.** Kdo si vede knihu poctivě, zapíše
        i převod korun na dolary (``TxType.EXCHANGE``). Kdo ne, má dolarový
        zůstatek v mínusu — dolary utratil, ale nikdy je "nekoupil". Takový
        schodek se tady dorovná převodem z domácí měny kurzem ČNB toho dne.
        Je to volba mezi dvěma nedokonalostmi a tahle je ta menší:

        * dopočet je z definice **korunově neutrální** (odečte přesně tolik
          korun, kolik chybějící cizí měna toho dne stála), takže výsledek
          neúplné knihy vyjde stejně jako dřív a nikomu se čísla nezmění;
        * bez dopočtu by záporný zůstatek dolarů znamenal, že kniha tvrdí,
          že dolary dlužíte — a při poklesu dolaru by z toho spočítala
          **zisk** z krátké pozice, kterou nikdo nikdy nedržel.

        Kolikrát se dopočítávalo, se hlásí v ``dopoctene_smeny``; ``kontrola``
        na to upozorní, protože je to známka neúplné knihy, ne stav věcí.
        """
        domaci = domaci.upper()
        meny = {(t.currency or domaci).upper() for t in self.transactions} | {domaci}
        # Měna bez dnešního kurzu do hotovostních součtů nevstupuje vůbec —
        # stejně jako u pozic. Půlka přepočtu je horší než žádný.
        kurzy_dnes = {}
        for mena in sorted(meny):
            kurz = prepocet(mena, k_datu)
            if kurz is not None:
                kurzy_dnes[mena] = kurz

        zustatky: dict[str, float] = {}
        historicky = 0.0
        smeny = 0.0
        dopoctene: list[dict] = []

        # Uvnitř jednoho dne kniha pořadí pohybů nezná. Připsat dřív než
        # odepsat je bezpečnější volba: jinak by nákup zaplacený z peněz,
        # které týž den přišly, vypadal jako přečerpaný účet a dopočítal by
        # se převod, který se nikdy nestal. Na součty to nemá vliv (dopočet
        # je korunově neutrální), jen na počet hlášení.
        for tx in sorted(self.transactions, key=lambda t: (t.day, self._tok(t) <= 0)):
            mena = (tx.currency or domaci).upper()
            kurz = prepocet(mena, tx.day) if mena in kurzy_dnes else None
            if kurz is None:
                continue

            tok = self._tok(tx)
            historicky += tok * kurz
            if tx.type is TxType.EXCHANGE:
                # V měně zápisu se obě strany směny sečtou na nesmysl,
                # v korunách přesně na to, co si broker vzal za převod.
                smeny += tx.amount * kurz
            zustatky[mena] = zustatky.get(mena, 0.0) + tok

            if mena != domaci and zustatky[mena] < -1e-9:
                chybi = -zustatky[mena]
                zustatky[mena] = 0.0
                zustatky[domaci] = zustatky.get(domaci, 0.0) - chybi * kurz
                dopoctene.append(
                    {"den": tx.day.isoformat(), "mena": mena, "kolik": chybi, "kurz": kurz}
                )

        dnes = sum(zustatek * kurzy_dnes[mena] for mena, zustatek in zustatky.items())
        return {
            "hotovost": dnes,
            "kurzovy_rozdil_hotovosti": dnes - historicky,
            "smeny": smeny,
            "hotovost_meny": sorted(
                (
                    {
                        "mena": mena,
                        "zustatek": zustatek,
                        "kurz_dnes": kurzy_dnes[mena],
                        "hodnota": zustatek * kurzy_dnes[mena],
                    }
                    for mena, zustatek in zustatky.items()
                    if abs(zustatek) > 1e-9
                ),
                key=lambda z: -abs(z["hodnota"]),
            ),
            "dopoctene_smeny": dopoctene,
        }

    def _toky_czk(self, prepocet) -> dict[str, float]:
        """Peněžní toky přepočtené kurzem **dne, kdy nastaly**.

        Vklad 1 000 USD z roku 2020 je jiná částka v korunách než tentýž
        vklad dnes. Sečíst je dnešním kurzem by z "vloženo" udělalo číslo,
        které se mění, i když jste nic nevložili.
        """
        out = {"vklady": 0.0, "vybery": 0.0, "dividendy": 0.0, "dane": 0.0}
        klice = {
            TxType.DEPOSIT: "vklady",
            TxType.WITHDRAWAL: "vybery",
            TxType.DIVIDEND: "dividendy",
            TxType.TAX: "dane",
        }
        poplatky = 0.0
        v_obchodech = 0.0
        for tx in self.transactions:
            kurz = prepocet(tx.currency, tx.day)
            if kurz is None:
                continue
            if tx.type in klice:
                out[klice[tx.type]] += tx.amount * kurz
            elif tx.type is TxType.FEE:
                poplatky += tx.amount * kurz
            if tx.type in (TxType.BUY, TxType.SELL):
                v_obchodech -= tx.fee * kurz
        out["vlozeno"] = out["vklady"] + out["vybery"]
        out["poplatky_mimo_obchody"] = poplatky
        out["poplatky_v_obchodech"] = v_obchodech
        return out

    @staticmethod
    def _expozice(pozice: list[dict]) -> list[dict]:
        """Kolik portfolia leží v které měně.

        Rozpad zhodnocení říká, co kurz udělal; tohle říká, co udělat může.
        """
        celkem = sum(p["hodnota"] for p in pozice)
        podle_meny: dict[str, dict] = {}
        for p in pozice:
            zaznam = podle_meny.setdefault(
                p["mena"], {"mena": p["mena"], "hodnota": 0.0, "kurz": p["kurz_dnes"]}
            )
            zaznam["hodnota"] += p["hodnota"]
        for zaznam in podle_meny.values():
            zaznam["podil_pct"] = zaznam["hodnota"] / celkem * 100.0 if celkem else 0.0
        return sorted(podle_meny.values(), key=lambda z: -z["hodnota"])

    def _kurzy_k_datu(self, prepocet, k_datu: date) -> dict[str, float]:
        """Kurzy použité k dnešnímu ocenění — ať je vidět, čím se počítalo."""
        out = {}
        for mena in sorted({t.currency for t in self.transactions}):
            kurz = prepocet(mena, k_datu)
            if kurz is not None:
                out[mena] = kurz
        return out

    # --- kontrola knihy --------------------------------------------------

    def kontrola(self, k_datu: date | None = None, domaci: str = "CZK") -> list[Nalez]:
        """Projde knihu a vrátí, co v ní nesedí.

        Účetní kniha je psaná ručně a chyba v ní se pozná pozdě — typicky
        až u daňového přiznání nebo když se přehled začne rozcházet
        s výpisem od brokera. Tahle funkce hledá právě ty chyby, které jdou
        udělat překlepem a **nespadnou**: prodej titulu, který kniha nezná,
        dividendu u nedržené pozice, přečerpanou hotovost, datum
        v budoucnosti a částku o řád vedle.

        Nálezy jsou dvojího druhu. ``chyba`` znamená, že kniha si sama se
        sebou odporuje a spočítaná čísla nedávají smysl. ``podezreni`` je
        věc, která **může** být v pořádku — dividenda po prodeji celé
        pozice se stává (rozhodný den byl dřív) a jednorázový velký vklad
        taky. Proto se nezamítá, jen ukazuje; kontrola, která křičí i na
        správné zápisy, se za týden vypne.
        """
        k_datu = k_datu or date.today()
        nalezy: list[Nalez] = []
        nalezy += self._kontrola_data(k_datu)
        nalezy += self._kontrola_pozic()
        nalezy += self._kontrola_hotovosti(domaci)
        nalezy += self._kontrola_castek()
        return sorted(nalezy, key=lambda n: (n.den or date.min, n.zprava))

    def _kontrola_data(self, k_datu: date) -> list[Nalez]:
        """Datum v budoucnosti. Skoro vždycky přehozený rok nebo den a měsíc.

        Nechat to projít znamená ocenit pozici kurzem, který ještě nebyl
        vyhlášen, a časový test spustit od data, které nenastalo.
        """
        return [
            Nalez("chyba", tx.day, f"{tx.type.value} {tx.symbol or ''} má datum v budoucnosti")
            for tx in self.transactions
            if tx.day > k_datu
        ]

    def _kontrola_pozic(self) -> list[Nalez]:
        """Prodeje a dividendy proti tomu, co kniha v ten den eviduje."""
        nalezy: list[Nalez] = []
        drzeno: dict[str, float] = {}
        for tx in self.transactions:
            if tx.type is TxType.BUY:
                drzeno[tx.symbol] = drzeno.get(tx.symbol, 0.0) + tx.quantity
            elif tx.type is TxType.SELL:
                mam = drzeno.get(tx.symbol, 0.0)
                if tx.quantity > mam + 1e-9:
                    kolik = (
                        "kniha ho nikdy nekoupila"
                        if mam <= 1e-9
                        else f"kniha eviduje jen {mam:g} ks"
                    )
                    nalezy.append(
                        Nalez(
                            "chyba",
                            tx.day,
                            f"prodej {tx.quantity:g} ks {tx.symbol}, ale {kolik}",
                        )
                    )
                drzeno[tx.symbol] = max(0.0, mam - tx.quantity)
            elif tx.type is TxType.DIVIDEND and drzeno.get(tx.symbol, 0.0) <= 1e-9:
                nalezy.append(
                    Nalez(
                        "podezreni",
                        tx.day,
                        f"dividenda {tx.symbol}, ale ten den podle knihy titul nedržíte "
                        "(může být v pořádku, byl-li rozhodný den před prodejem)",
                    )
                )
        return nalezy

    def _kontrola_hotovosti(self, domaci: str) -> list[Nalez]:
        """Záporný zůstatek hotovosti — a rozlišuje se, v které měně.

        Mínus v domácí měně znamená, že kniha utrácí peníze, které do ní
        nikdo nevložil: chybí vklad. Mínus v cizí měně je něco jiného —
        typicky chybějící zápis směny, protože korun se koupily dolary
        a nikdo to nezapsal. Splácat obojí do jedné hlášky by vedlo
        k hledání chybějícího vkladu tam, kde chybí převod.
        """
        domaci = domaci.upper()
        nalezy: list[Nalez] = []
        zustatky: dict[str, float] = {}
        nahlaseno: set[str] = set()
        for tx in sorted(self.transactions, key=lambda t: (t.day, self._tok(t) <= 0)):
            mena = (tx.currency or domaci).upper()
            zustatky[mena] = zustatky.get(mena, 0.0) + self._tok(tx)
            if zustatky[mena] >= -1e-9 or mena in nahlaseno:
                continue
            nahlaseno.add(mena)
            if mena == domaci:
                nalezy.append(
                    Nalez(
                        "chyba",
                        tx.day,
                        f"zůstatek {mena} klesl na {zustatky[mena]:,.2f} — v knize chybí vklad",
                    )
                )
            else:
                nalezy.append(
                    Nalez(
                        "podezreni",
                        tx.day,
                        f"zůstatek {mena} klesl na {zustatky[mena]:,.2f} — chybí nejspíš zápis "
                        f"směny (typ smena). Přehled si převod dopočítá kurzem ČNB toho dne, "
                        "ale kurz, který jste dostali od brokera, byl jiný",
                    )
                )
        return nalezy

    def _kontrola_castek(self) -> list[Nalez]:
        """Částky, které nesedí — překlep o řád a rozpor mezi sloupci."""
        nalezy: list[Nalez] = []

        for tx in self.transactions:
            if tx.type not in (TxType.BUY, TxType.SELL) or not tx.amount:
                continue
            ocekavano = tx.quantity * tx.price
            # Tolerance pokrývá obě konvence, které brokeři používají:
            # částku s poplatkem i bez něj. Půl procenta navíc je na
            # zaokrouhlení kurzu a haléře — ne na překlep o řád.
            dovoleno = tx.fee + max(0.02, 0.005 * ocekavano)
            if abs(abs(tx.amount) - ocekavano) > dovoleno:
                nalezy.append(
                    Nalez(
                        "podezreni",
                        tx.day,
                        f"{tx.type.value} {tx.symbol}: {tx.quantity:g} × {tx.price:g} = "
                        f"{ocekavano:,.2f}, ale amount je {tx.amount:,.2f}",
                    )
                )

        # Pohyb o řád vedle. Medián, ne průměr: jeden překlep by průměr
        # zvedl tak, že by se sám schoval pod práh.
        podle_meny: dict[str, list[Transaction]] = {}
        for tx in self.transactions:
            podle_meny.setdefault((tx.currency or "").upper(), []).append(tx)
        for mena, pohyby in podle_meny.items():
            castky = [abs(self._tok(tx)) for tx in pohyby if abs(self._tok(tx)) > 1e-9]
            if len(castky) < 5:
                # Na třech pohybech je "obvyklá velikost" statistika o ničem
                # a každý druhý zápis by byl podezřelý.
                continue
            median = statistics.median(castky)
            if median <= 0:
                continue
            for tx in pohyby:
                castka = abs(self._tok(tx))
                if castka > _SKOK * median:
                    nalezy.append(
                        Nalez(
                            "podezreni",
                            tx.day,
                            f"{tx.type.value} {tx.symbol or ''} {castka:,.2f} {mena} je "
                            f"{castka / median:.0f}× nad obvyklou velikostí pohybu "
                            f"({median:,.2f} {mena}) — nepřebývá nula?",
                        )
                    )
        return nalezy

    def tax_clock(self, k_datu: date | None = None) -> list[dict]:
        """Kdy která dávka projde časovým testem.

        **Není to daňové poradenství.** Je to rozdíl dvou dat spočítaný
        z vašich vlastních zápisů; osvobození má i další podmínky a patří
        k daňovému poradci.
        """
        k_datu = k_datu or date.today()
        out = []
        for lot in self.positions():
            osvobozeno = lot.osvobozeno_od()
            out.append(
                {
                    "symbol": lot.symbol,
                    "quantity": lot.quantity,
                    "nakoupeno": lot.day,
                    "osvobozeno_od": osvobozeno,
                    "dni_zbyva": max(0, (osvobozeno - k_datu).days),
                    "splneno": k_datu >= osvobozeno,
                }
            )
        return sorted(out, key=lambda r: r["osvobozeno_od"])


def _do_csv(hodnota: float) -> str:
    """Číslo do buňky bez zbytečného ocasu.

    ``str(2.0)`` je "2.0" a při každém přepsání knihy by se ručně napsaná
    dvojka změnila na dvojku s desetinnou nulou. Ve verzovaném souboru to
    dělá hluk v diffu, ve kterém se ztratí skutečná změna. Deset platných
    číslic je nad rámec všeho, co se v knize vyskytne, takže se nic
    nezaokrouhlí.
    """
    return f"{hodnota:.10g}" if hodnota else ""


def _hlavickove_komentare(text: str) -> list[str]:
    """Komentářové řádky z hlavičky souboru, aby přežily přepsání knihy.

    Vzor knihy je z poloviny nápověda k formátu. Příkaz, který připíše jeden
    pohyb a přitom tu nápovědu smaže, uživatele naučí, že se knihy raději
    nemá dotýkat.
    """
    hlavicka: list[str] = []
    for radek in text.splitlines():
        # Prázdný řádek uvnitř hlavičky ano, před ní ne — jinak by se
        # "hlavičkou" stal každý soubor začínající prázdným řádkem.
        if radek.lstrip().startswith("#") or (not radek.strip() and hlavicka):
            hlavicka.append(radek)
        else:
            break
    return hlavicka


def merge(ledger: Ledger, txs: list[Transaction]) -> tuple[Ledger, int, int]:
    """Přimíchá pohyby do knihy bez zdvojení.

    Vrací novou knihu, počet přidaných a počet přeskočených. Původní kniha
    zůstává nedotčená, aby import šlo nejdřív ukázat a teprve pak uložit —
    přepsat evidenci a až potom zjistit, že výpis byl špatný, je nehoda,
    ze které se špatně vrací.
    """
    nova = Ledger(
        transactions=[replace(t) for t in ledger.transactions],
        komentare=list(ledger.komentare),
        oddelovac=ledger.oddelovac,
    )
    pridano = nova.extend(txs)
    return nova, pridano, len(txs) - pridano
