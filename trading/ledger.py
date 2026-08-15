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

        Co tenhle výpočet **neumí**: dividendu inkasovanou v dolarech
        a v dolarech ponechanou. Kniha nevede zůstatek hotovosti po měnách,
        takže takový pohyb přepočte kurzem dne, kdy přišel, a další pohyb
        kurzu na nevyměněné hotovosti nevidí. U reinvestovaných dividend
        to nevadí (nákup má vlastní datum a kurz), u hromadící se hotovosti
        ano.
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
            "celkem": (
                nerealizovany
                + realizovany
                + toky["dividendy"]
                + toky["dane"]
                + toky["poplatky_mimo_obchody"]
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
