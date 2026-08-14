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

Peníze se počítají v ``float``, stejně jako ve zbytku projektu. Na evidenci
řádu statisíců to stačí; zaokrouhluje se až při výpisu.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

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


@dataclass
class Ledger:
    """Účetní kniha skutečného portfolia."""

    transactions: list[Transaction] = field(default_factory=list)

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

        ledger = cls()
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
        """Uloží knihu. Přepisuje celý soubor — je zdrojem pravdy."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(self.SLOUPCE)
            for tx in self.transactions:
                writer.writerow(
                    [
                        tx.day.isoformat(),
                        tx.type.value,
                        tx.symbol or "",
                        tx.quantity or "",
                        tx.price or "",
                        f"{tx.amount:.2f}",
                        tx.fee or "",
                        tx.currency,
                        tx.note,
                    ]
                )

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
                    Lot(tx.day, tx.symbol, tx.quantity, cena_za_kus)
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
        }

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


def merge(ledger: Ledger, txs: list[Transaction]) -> tuple[Ledger, int, int]:
    """Přimíchá pohyby do knihy bez zdvojení.

    Vrací novou knihu, počet přidaných a počet přeskočených. Původní kniha
    zůstává nedotčená, aby import šlo nejdřív ukázat a teprve pak uložit —
    přepsat evidenci a až potom zjistit, že výpis byl špatný, je nehoda,
    ze které se špatně vrací.
    """
    nova = Ledger(transactions=[replace(t) for t in ledger.transactions])
    pridano = nova.extend(txs)
    return nova, pridano, len(txs) - pridano
