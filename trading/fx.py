"""Kurzy ČNB — most mezi tím, v čem nakupujete, a tím, v čem žijete.

Kdo kupuje v dolarech a počítá v korunách, drží ve skutečnosti **dvě**
sázky: na titul a na kurz. Pozice, která vyrostla o deset procent, zatímco
koruna o deset procent posílila, nevydělala nic — a evidence, která to
neukáže, je hůř než žádná, protože tvrdí opak.

Tenhle modul je proto jen dodavatel kurzů. Rozpad zhodnocení na výkon
aktiva a pohyb kurzu dělá ``ledger.valuation_czk`` nad tím, co sem přijde.

**Zdroj je výhradně denní kurzovní lístek ČNB.** Yahoo (``USDCZK=X``) by
šlo použít taky a bylo by to o řádek víc, ale míchat dva zdroje znamená,
že tatáž kniha vyjde jednou tak a podruhé jinak podle toho, co zrovna
odpovědělo. Kurz ČNB je navíc ten, se kterým počítá finanční úřad, takže
čísla z knihy jdou aspoň porovnat s daňovým přiznáním. Nedostupná ČNB
tedy neznamená náhradní zdroj, ale sáhnutí do cache.

Lístek se cachuje **v surové podobě**, jak přišel po drátě. Není to
z lenosti: kdyby se ukládal už rozparsovaný, byla by cesta z cache jiný
kus kódu než cesta ze sítě a obě by mohly dát jiný výsledek. Takhle je
z cache i ze sítě totéž pole bajtů a týž parser. Hlídá to test.

Pár vlastností lístku, které se snadno přehlédnou:

* **Kurz nemusí být za jeden kus.** Jen je 100 JPY, rupie 1000 IDR.
  Sloupec "množství" se musí dělit, jinak vyjde portfolio v jenech
  stokrát dražší.
* **ČNB vrací nejbližší starší lístek.** Na sobotu, svátek i na datum
  v budoucnosti odpoví posledním vyhlášeným kurzem a v hlavičce přizná
  datum jeho platnosti. Je to správné chování (v sobotu se nevyhlašuje),
  ale znamená to, že odpověď na *budoucí* datum se ještě změní — a tu
  proto do trvalé cache nepatří ukládat.
* **Kurz je "za jednotku cizí měny v korunách"**, tedy CZK/USD. Násobí se
  jím, nedělí.

**Není to daňové poradenství.** Pro daňové přiznání existuje i jednotný
roční kurz a další pravidla, která tenhle modul nezná.
"""

from __future__ import annotations

import logging
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

#: Denní kurzovní lístek. Parametr ``date`` je ve tvaru DD.MM.YYYY.
CNB_URL = (
    "https://www.cnb.cz/cs/financni-trhy/devizovy-trh/kurzy-devizoveho-trhu"
    "/kurzy-devizoveho-trhu/denni_kurz.txt"
)

#: Měna, ve které uživatel žije. Kurz sama k sobě je z definice 1.
DOMACI_MENA = "CZK"

DEFAULT_CACHE_DIR = Path("data/cache/kurzy")

#: Jak dlouho se smí věřit lístku staženému pro dnešek nebo pozdější datum.
#: ČNB vyhlašuje kolem 14:30, takže dopolední odpověď na dnešek nese ještě
#: včerejší kurz a odpoledne se změní. U starších dat tenhle problém není:
#: vyhlášený kurz se zpětně nemění, a tak platí navždy.
CERSTVOST_S = 6 * 3600


class FxError(RuntimeError):
    """Lístek se nepodařilo stáhnout nebo mu nerozumíme."""


class KurzChybi(LookupError):
    """Pro tuhle měnu a datum kurz nemáme.

    Dědí z ``LookupError`` schválně: účetní kniha ho tak umí odchytit,
    aniž by o tomhle modulu musela vědět. Přepočet, který se nepovedl,
    se pak v evidenci přizná — místo aby se tiše dosadila jednička.
    """


@dataclass(frozen=True)
class Listek:
    """Jeden kurzovní lístek.

    ``platnost`` je datum z hlavičky, ne datum dotazu. Ptáte-li se na
    sobotu, dostanete páteční lístek a musí to být poznat — jinak by
    nešlo odlišit "kurz k datu" od "poslední známý kurz".
    """

    platnost: date
    kurzy: dict[str, float]
    """Kolik korun stojí **jeden** kus měny. Množství je už vyděleno."""

    zdroj: str = "sit"
    """``sit``, ``cache``, nebo ``cache-starsi`` (offline náhrada)."""

    def kurz(self, mena: str) -> float:
        mena = mena.upper()
        if mena == DOMACI_MENA:
            return 1.0
        try:
            return self.kurzy[mena]
        except KeyError as exc:
            raise KurzChybi(f"{mena}: na lístku z {self.platnost} není") from exc


def parse_listek(text: str) -> Listek:
    """Rozparsuje text lístku.

    Formát::

        14.08.2026 #156
        země|měna|množství|kód|kurz
        EMU|euro|1|EUR|24,210
        Japonsko|jen|100|JPY|13,163

    Desetinná čárka a sloupec "množství" jsou dvě místa, kde se dá tiše
    splést o řád; obojí má test.
    """
    radky = [r for r in text.splitlines() if r.strip()]
    if len(radky) < 3:
        raise FxError(f"kurzovní lístek je příliš krátký ({len(radky)} řádků)")

    try:
        platnost = datetime.strptime(radky[0].split()[0], "%d.%m.%Y").date()
    except (ValueError, IndexError) as exc:
        raise FxError(f"nerozumím hlavičce lístku: {radky[0]!r}") from exc

    kurzy: dict[str, float] = {}
    for radek in radky[2:]:
        pole = radek.split("|")
        if len(pole) < 5:
            continue
        _zeme, _mena, mnozstvi, kod, kurz = pole[:5]
        try:
            kusu = float(_cislo(mnozstvi))
            hodnota = float(_cislo(kurz))
        except ValueError:
            logger.warning("nečitelný řádek lístku: %r", radek)
            continue
        if kusu <= 0:
            continue
        # Dělení množstvím: lístek uvádí 100 JPY, my chceme jeden jen.
        kurzy[kod.strip().upper()] = hodnota / kusu

    if not kurzy:
        raise FxError("kurzovní lístek neobsahuje jediný kurz")
    return Listek(platnost=platnost, kurzy=kurzy)


def _cislo(text: str) -> str:
    """Český zápis čísla na strojový. Mezera je oddělovač tisíců, i ta pevná."""
    return text.strip().replace("\xa0", "").replace(" ", "").replace(",", ".")


_KONTEXT: ssl.SSLContext | None = None


def _kontext() -> ssl.SSLContext:
    """Ověřování certifikátů — zapnuté, jen s doplněnými kořeny.

    Python stažený z python.org na macOS nepoužívá systémovou klíčenku
    a bez doinstalovaných kořenových certifikátů skončí každý HTTPS
    požadavek na ``CERTIFICATE_VERIFY_FAILED``. Přidáváme proto ještě
    balík ``certifi``, který si stejně táhne ``yfinance``. Ověřování se
    tím **nevypíná** — vypnout ho kvůli pohodlí u modulu, podle kterého
    se počítají peníze, je přesně ta zkratka, co se nemá dělat.
    """
    global _KONTEXT
    if _KONTEXT is None:
        kontext = ssl.create_default_context()
        try:
            import certifi
        except ImportError:  # pragma: no cover - závisí na prostředí
            logger.debug("certifi není, spoléhám na systémové kořeny")
        else:
            kontext.load_verify_locations(cafile=certifi.where())
        _KONTEXT = kontext
    return _KONTEXT


def stahni_listek(den: date, timeout: float = 10.0) -> str:
    """Stáhne surový text lístku k datu. Vrací, co přišlo — nic neparsuje."""
    url = f"{CNB_URL}?date={den:%d.%m.%Y}"
    try:
        with urllib.request.urlopen(  # noqa: S310 - pevná URL, jen https
            url, timeout=timeout, context=_kontext()
        ) as odpoved:
            return odpoved.read().decode("utf-8")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise FxError(f"kurzovní lístek k {den} se nepodařilo stáhnout: {exc}") from exc


@dataclass
class Kurzovnik:
    """Zdroj kurzů s cache na disku. Bez sítě funguje dál.

    Použití je jediná metoda ``kurz(mena, den)``. Všechno ostatní je
    obsluha cache.

    ``povolit_sit=False`` udělá z kurzovníku čistě offline nástroj —
    testy tak nemají jak omylem sáhnout na internet.
    """

    cache_dir: Path | None = DEFAULT_CACHE_DIR
    povolit_sit: bool = True
    timeout: float = 10.0

    #: Kdy jsme sáhli po starším lístku, protože k datu nebyl (offline).
    #: Přehled to pak umí přiznat místo toho, aby tvářil jistotu.
    nahrady: dict[date, date] = field(default_factory=dict, repr=False)

    _pamet: dict[date, Listek] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.cache_dir is not None:
            self.cache_dir = Path(self.cache_dir)

    # --- veřejné rozhraní ------------------------------------------------

    def kurz(self, mena: str, den: date) -> float:
        """Kolik korun stál jeden kus měny k danému dni.

        Zásadní je, že ``den`` je **datum toho pohybu**, ne dnešek. Ocenit
        nákup z roku 2020 dnešním kurzem je stejný druh chyby jako pohled
        do budoucnosti v backtestu: použije informaci, kterou jste v tu
        chvíli neměli, a odpověď vyjde soustavně vedle.
        """
        mena = mena.upper()
        if mena == DOMACI_MENA:
            return 1.0
        return self.listek(den).kurz(mena)

    def listek(self, den: date) -> Listek:
        """Lístek platný k datu. Sahá po síti jen když musí."""
        if den in self._pamet:
            return self._pamet[den]

        z_disku = self._z_cache(den)
        if z_disku is not None:
            self._pamet[den] = z_disku
            return z_disku

        if self.povolit_sit:
            try:
                text = stahni_listek(den, self.timeout)
                listek = parse_listek(text)
                self._uloz(den, text)
                self._pamet[den] = listek
                return listek
            except FxError as exc:
                logger.warning("kurz k %s ze sítě nelze získat (%s), zkouším cache", den, exc)

        nahradni = self._nejblizsi_starsi(den)
        if nahradni is not None:
            self.nahrady[den] = nahradni.platnost
            logger.warning("kurz k %s nahrazen lístkem z %s", den, nahradni.platnost)
            self._pamet[den] = nahradni
            return nahradni

        raise KurzChybi(
            f"kurz k {den} není v cache a síť nepomohla — přehled v korunách nelze spočítat"
        )

    def predehraj(self, dny: list[date]) -> None:
        """Připraví lístky pro zadaná data dopředu.

        Kvůli přehledu, který se ptá na kurz uvnitř smyčky přes pozice:
        bez tohohle by se prohlížeč díval do prázdna, dokud doběhne
        dvacet HTTP dotazů na ČNB.
        """
        for den in sorted(set(dny)):
            try:
                self.listek(den)
            except KurzChybi as exc:
                logger.warning("%s", exc)

    # --- cache -----------------------------------------------------------

    def _cesta(self, den: date) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / f"{den:%Y-%m-%d}.txt"

    def _z_cache(self, den: date) -> Listek | None:
        cesta = self._cesta(den)
        if cesta is None or not cesta.exists():
            return None
        # Odpověď na dnešek a dál se ještě změní — ČNB vyhlašuje odpoledne
        # a na budoucí datum vrací zatím poslední známý kurz. Uložit takovou
        # odpověď natrvalo by znamenalo evidenci, která si pamatuje kurz,
        # jenž nikdy neplatil.
        if den >= date.today() and time.time() - cesta.stat().st_mtime > CERSTVOST_S:
            return None
        try:
            return _se_zdrojem(parse_listek(cesta.read_text(encoding="utf-8")), "cache")
        except (FxError, OSError) as exc:
            logger.warning("poškozená cache kurzů %s (%s)", cesta, exc)
            return None

    def _uloz(self, den: date, text: str) -> None:
        cesta = self._cesta(den)
        if cesta is None:
            return
        cesta.parent.mkdir(parents=True, exist_ok=True)
        cesta.write_text(text, encoding="utf-8")

    def _nejblizsi_starsi(self, den: date) -> Listek | None:
        """Poslední lístek v cache, který není z budoucnosti vůči ``den``.

        Nouzové řešení pro běh bez sítě. Vrací se jen tehdy, když k datu
        nic není — a volající to pak má přiznat, protože kurz z jiného dne
        je odhad, ne účetní údaj.
        """
        if self.cache_dir is None or not self.cache_dir.exists():
            return None
        kandidati = []
        for cesta in self.cache_dir.glob("*.txt"):
            try:
                kdy = date.fromisoformat(cesta.stem)
            except ValueError:
                continue
            if kdy <= den:
                kandidati.append((kdy, cesta))
        for _kdy, cesta in sorted(kandidati, reverse=True):
            try:
                return _se_zdrojem(parse_listek(cesta.read_text(encoding="utf-8")), "cache-starsi")
            except (FxError, OSError):
                continue
        return None


def _se_zdrojem(listek: Listek, zdroj: str) -> Listek:
    return Listek(platnost=listek.platnost, kurzy=listek.kurzy, zdroj=zdroj)


class _BezSite:
    """Společné pro tabulkové kurzovníky: nemají co předehrávat ani nahrazovat.

    Metody jsou tu proto, aby tabulka byla plnohodnotná náhrada za
    ``Kurzovnik`` všude, kde se s ním pracuje — jinak by se testovaná
    cesta lišila od té produkční právě v tom místě, kde se testuje.
    """

    #: Nikdy nic nenahrazujeme, kurz je zadaný.
    nahrady: dict[date, date] = {}  # noqa: RUF012 - záměrně sdílený prázdný slovník

    def predehraj(self, dny: list[date]) -> None:
        """Nic. Tabulka je v paměti."""


@dataclass
class PevneKurzy(_BezSite):
    """Kurzovník z tabulky — pro testy a pro běh s ručně zadaným kurzem.

    Datum se ignoruje. Je to schválně hloupé: testy rozpadu zhodnocení
    potřebují znát kurz přesně, ne ho stahovat.
    """

    kurzy: dict[str, float]

    def kurz(self, mena: str, den: date) -> float:  # noqa: ARG002 - podpis zdroje kurzů
        mena = mena.upper()
        if mena == DOMACI_MENA:
            return 1.0
        try:
            return self.kurzy[mena]
        except KeyError as exc:
            raise KurzChybi(f"{mena}: kurz není v tabulce") from exc


@dataclass
class KurzyPodleDne(_BezSite):
    """Kurzovník z tabulky ``{(měna, datum): kurz}`` s náhradou nejbližším starším.

    Testům, které rozlišují kurz ke dni nákupu a ke dni ocenění, stačí
    tohle — a nemusí kvůli tomu na síť ani na disk.
    """

    kurzy: dict[tuple[str, date], float]

    def kurz(self, mena: str, den: date) -> float:
        mena = mena.upper()
        if mena == DOMACI_MENA:
            return 1.0
        if (mena, den) in self.kurzy:
            return self.kurzy[(mena, den)]
        starsi = [d for (m, d) in self.kurzy if m == mena and d <= den]
        if not starsi:
            raise KurzChybi(f"{mena}: kurz k {den} ani dřívější není v tabulce")
        return self.kurzy[(mena, max(starsi))]
