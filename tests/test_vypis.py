"""Import cizího výpisu do účetní knihy.

Import je jediné místo, kudy do evidence teče soubor, který jsme nepsali.
Chyby tady mají společnou vlastnost: nespadnou. Špatně uhodnutý sloupec,
prohozený desetinný oddělovač nebo dvojznačné datum vyrobí zápis, který
vypadá věrohodně a pozná se až v součtu — nebo vůbec. Testy proto míří
právě na ně.
"""

from datetime import date

import pytest

from trading.ledger import Ledger, LedgerError, TxType
from trading.vypis import (
    castka,
    cislo,
    datum,
    hadej_mapovani,
    nacti_vypis,
    parse_mapovani,
    typ,
)


def vypis(tmp_path, obsah, jmeno="vypis.csv"):
    cesta = tmp_path / jmeno
    cesta.write_text(obsah, encoding="utf-8")
    return cesta


# --- hádání sloupců -----------------------------------------------------


def test_presna_shoda_ma_prednost_pred_podretezcem():
    """"Price currency" nesmí sebrat sloupec dřív než "Currency".

    Podřetězcové hledání bez přednosti přesné shody namapuje měnu na
    sloupec s cenou. Součty pak vyjdou v měně, kterou nikdo nezadal.
    """
    m = hadej_mapovani(["Date", "Type", "Price currency", "Currency", "Price"])
    assert m["currency"] == "Currency"
    assert m["price"] == "Price"


def test_hadani_ignoruje_diakritiku_a_velikost_pismen():
    """České výpisy píšou "Měna" a "Částka"; ASCII porovnání je nenajde."""
    m = hadej_mapovani(["Datum", "Typ operace", "Počet", "Cena za kus", "Částka", "Měna"])
    assert m["day"] == "Datum"
    assert m["type"] == "Typ operace"
    assert m["quantity"] == "Počet"
    assert m["amount"] == "Částka"
    assert m["currency"] == "Měna"


def test_chybejici_povinny_sloupec_se_hlasi_s_hlavickou(tmp_path):
    """Hláška musí ukázat, co ve výpisu skutečně je — jinak uživatel hádá,
    jak se ten sloupec má jmenovat."""
    cesta = vypis(tmp_path, "Kdy;Co;Kolik\n2024-01-02;nakup;5\n")
    with pytest.raises(LedgerError, match="Kdy"):
        nacti_vypis(cesta)


def test_rucni_mapovani_prebije_uhodnute(tmp_path):
    cesta = vypis(
        tmp_path,
        "Datum;Typ;Ticker;Počet;Cena;Hodnota;Měna\n"
        "2024-01-02;nakup;X;2;100;-200;USD\n",
    )
    v = nacti_vypis(cesta, parse_mapovani(["symbol=Měna"]))
    assert v.pohyby[0].symbol == "USD", "ručně zadané mapování musí vyhrát"


# --- čísla --------------------------------------------------------------


@pytest.mark.parametrize(
    "text,ocekavano",
    [
        ("1,234.56", 1234.56),   # anglicky: čárka jsou tisíce
        ("1.234,56", 1234.56),   # německy a česky: tečka jsou tisíce
        ("1,5", 1.5),            # jen čárka = desetinná
        ("1 234,50", 1234.50),   # mezera jako oddělovač tisíců
        ("-520,50", -520.50),
        ("$1,000.00", 1000.0),   # měnová značka
        ("", 0.0),
        (None, 0.0),
    ],
)
def test_cislo_rozezna_desetinny_oddelovac(text, ocekavano):
    """Rozhoduje poslední oddělovač. Prohodit je znamená chybu stokrát
    nebo tisíckrát vedle — a číslo přitom pořád vypadá jako číslo."""
    assert cislo(text) == pytest.approx(ocekavano)


def test_necislo_se_hlasi():
    """Text nesmí tiše propadnout na nulu.

    Kdyby se do číselného sloupce dostal namapovaný textový, vznikly by
    z něj samé nuly a import by vypadal, že proběhl v pořádku. Součet by
    seděl, jen by neodpovídal skutečnosti.
    """
    with pytest.raises(LedgerError, match="není číslo"):
        cislo("nevím")


@pytest.mark.parametrize("text", ["-", "N/A", "n.a.", "null", ""])
def test_zapisy_ktere_znamenaji_nic_jsou_nula(text):
    """Prázdný poplatek se ve výpisech píše různě a chybou není."""
    assert cislo(text) == 0.0


# --- data ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text,ocekavano",
    [
        ("2024-01-15", date(2024, 1, 15)),
        ("2024-01-15T09:30:00", date(2024, 1, 15)),
        ("15.1.2024", date(2024, 1, 15)),
        ("15.01.2024", date(2024, 1, 15)),
    ],
)
def test_datum_umi_iso_i_cesky_zapis(text, ocekavano):
    assert datum(text) == ocekavano


def test_datum_s_lomitky_se_odmita():
    """01/02/2024 je 1. února pro Evropana a 2. ledna pro Američana.

    Uhodnout to z jednoho řádku nejde a tichý překlep posune časový test
    i kurz. Radši hláška než měsíc vedle.
    """
    with pytest.raises(LedgerError, match="dvojznačné"):
        datum("01/02/2024")


def test_datum_format_umlci_dvojznacnost():
    assert datum("01/02/2024", "%d/%m/%Y") == date(2024, 2, 1)


# --- směr peněz ---------------------------------------------------------


def test_smer_se_bere_z_druhu_pohybu_ne_ze_znamenka():
    """Brokeři píšou nákup jednou kladně, jednou záporně.

    Hádat směr podle znaménka znamená, že se jednou za čas nákup zapíše
    jako prodej — a v součtu to vypadá jako zisk.
    """
    assert castka(TxType.BUY, 500.0) == -500.0
    assert castka(TxType.BUY, -500.0) == -500.0
    assert castka(TxType.SELL, -500.0) == 500.0
    assert castka(TxType.SELL, 500.0) == 500.0


def test_smena_si_znamenko_nechava():
    """U směny je znaménko jediná informace o tom, kterým směrem převod
    šel. Přepsat ho podle druhu by z obou stran udělalo tutéž stranu."""
    assert castka(TxType.EXCHANGE, -45000.0) == -45000.0
    assert castka(TxType.EXCHANGE, 1970.0) == 1970.0


def test_smena_bez_castky_je_chyba():
    with pytest.raises(LedgerError, match="ze které strany"):
        castka(TxType.EXCHANGE, 0.0)


def test_castka_se_dopocita_z_poctu_a_ceny():
    assert castka(TxType.BUY, 0.0, quantity=2, price=250.0) == -500.0


@pytest.mark.parametrize(
    "text,ocekavano",
    [("nakup", TxType.BUY), ("BUY", TxType.BUY), ("Prodej", TxType.SELL),
     ("dividenda", TxType.DIVIDEND), ("Vklad", TxType.DEPOSIT)],
)
def test_typ_pohybu_z_ruznych_zapisu(text, ocekavano):
    assert typ(text) == ocekavano


def test_neznamy_typ_se_hlasi():
    with pytest.raises(LedgerError, match="neznámý druh"):
        typ("zaklínadlo")


# --- celý import --------------------------------------------------------


CIZI = """Datum;Typ operace;Ticker;Počet;Cena za kus;Částka;Poplatek;Měna
2025-03-04;nakup;CSPX.AS;1;520,50;-520,50;1,00;EUR
2025-03-05;dividenda;SPY;;;8,20;;USD
"""


def test_import_precte_cizi_vypis_se_strednikem_a_carkou(tmp_path):
    v = nacti_vypis(vypis(tmp_path, CIZI))
    assert len(v.pohyby) == 2
    nakup = v.pohyby[0]
    assert nakup.day == date(2025, 3, 4)
    assert nakup.type is TxType.BUY
    assert nakup.symbol == "CSPX.AS"
    assert nakup.price == pytest.approx(520.50)
    assert nakup.amount == pytest.approx(-520.50)
    assert nakup.fee == pytest.approx(1.0)
    assert nakup.currency == "EUR"


def test_nacteni_do_knihy_nezapisuje(tmp_path):
    """Náhled je výchozí chování. Přepsat evidenci a až pak zjistit, že se
    sloupce namapovaly křivě, je nehoda, ze které se špatně vrací."""
    kniha = Ledger()
    nacti_vypis(vypis(tmp_path, CIZI))
    assert kniha.transactions == []


def test_tyz_vypis_dvakrat_nic_nezdvoji(tmp_path):
    """Běžná nehoda: člověk si není jistý, jestli import proběhl."""
    cesta = vypis(tmp_path, CIZI)
    kniha = Ledger()

    prvni = kniha.extend(nacti_vypis(cesta).pohyby)
    druhy = kniha.extend(nacti_vypis(cesta).pohyby)

    assert (prvni, druhy) == (2, 0)
    assert len(kniha.transactions) == 2


def test_necitelny_radek_se_preskoci_a_prizna(tmp_path):
    """Zahodit řádek potichu znamená evidenci, které chybí kus a nikdo
    neví který. Souhrnné řádky na konci výpisu jsou běžné."""
    obsah = CIZI + "2025-03-06;zaklínadlo;X;1;10;-10;;USD\nCelkem;;;;;-512,30;;\n"
    v = nacti_vypis(vypis(tmp_path, obsah))

    assert len(v.pohyby) == 2
    assert len(v.preskocene) == 2
    cisla_radku = [r for r, _ in v.preskocene]
    assert cisla_radku == [4, 5], "musí být poznat, které řádky to byly"


def test_prazdne_radky_se_nepocitaji_jako_preskocene(tmp_path):
    v = nacti_vypis(vypis(tmp_path, CIZI + "\n;;;;;;;\n"))
    assert v.preskocene == []


def test_chybejici_mena_dostane_vychozi(tmp_path):
    obsah = "Datum;Typ;Ticker;Počet;Cena;Částka\n2025-03-04;nakup;X;1;100;-100\n"
    v = nacti_vypis(vypis(tmp_path, obsah), vychozi_mena="CZK")
    assert v.pohyby[0].currency == "CZK"


def test_bom_na_zacatku_souboru_nevadi(tmp_path):
    """Excel ukládá CSV s BOM a ten by se jinak nalepil na první sloupec,
    takže by se "Datum" jmenoval "﻿Datum" a mapování by ho nenašlo."""
    cesta = tmp_path / "vypis.csv"
    cesta.write_text("﻿" + CIZI, encoding="utf-8")
    v = nacti_vypis(cesta)
    assert len(v.pohyby) == 2


def test_mapovani_je_ve_vysledku_pro_nahled(tmp_path):
    """Špatně uhodnuté mapování je ta chyba, kterou musí uživatel vidět
    dřív, než uloží — takže musí být z čeho ji vypsat."""
    v = nacti_vypis(vypis(tmp_path, CIZI))
    assert v.mapovani["day"] == "Datum"
    assert v.mapovani["amount"] == "Částka"
