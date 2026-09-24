"""Převod ISIN na ticker.

Chyby tady nespadnou — vyrobí pozici oceněnou ve špatné měně nebo cenou
jiného papíru. Obojí vypadá věrohodně, a proto to testy hlídají.
"""

import pytest

from trading import isin
from trading.ledger import TxType
from trading.vypis import nacti_vypis


@pytest.mark.parametrize("text,ok", [
    ("IE00B5BMR087", True),    # iShares Core S&P 500
    ("US88160R1014", True),    # Tesla
    ("CZ0005112300", True),    # ČEZ
    ("IE00B5BMR088", False),   # překlep v kontrolní číslici
    ("CSPX.AS", False),
    ("", False),
])
def test_je_isin_kontroluje_i_kontrolni_cislici(text, ok):
    """Překlep v ISIN by se hledal a v horším případě našel jiný papír."""
    assert isin.je_isin(text) is ok


def _kotace(tabulka):
    return lambda t: tabulka.get(t)


def test_vybere_burzu_ve_mene_nakupu(tmp_path):
    """Koupeno v eurech → eurová kotace, i když vyhledávání vrátí jinou.

    Dolarová cena u eurového nákupu by posunula hodnotu pozice o kurz."""
    t = isin.preloz(
        "IE00B5BMR087", "EUR", soubor=tmp_path / "t.csv",
        kandidati=lambda i: ["CSPX.L", "CSPX.AS"],
        mena_kotace=_kotace({"CSPX.L": "USD", "CSPX.AS": "EUR"}),
        nazev=lambda d: ("CSPX.L", "iShares Core S&P 500 UCITS ETF"),
    )
    assert t == "CSPX.AS"


def test_pence_nejsou_libry(tmp_path):
    """Londýnská kotace v pencích má cenu stokrát větší. GBp ≠ GBP."""
    t = isin.preloz(
        "IE00B4ND3602", "GBP", soubor=tmp_path / "t.csv",
        kandidati=lambda i: ["SGLN.L"], mena_kotace=_kotace({"SGLN.L": "GBp"}),
        nazev=lambda d: ("SGLN.L", "iShares Physical Gold"),
    )
    assert t is None


def test_odvozeny_ticker_s_jinym_nazvem_se_odmitne(tmp_path):
    """Tentýž kořen na jiné burze může být úplně jiný papír (CSSPX ≠ CSPX).
    Sedící měna nestačí, musí sedět i název."""
    nazvy = {"IE00B5BMR087": ("CSSPX.MI", "iShares Core S&P 500 UCITS ETF"),
             "CSSPX.SW": ("CSSPX.SW", "Credit Suisse Small Cap Index Fund")}
    t = isin.preloz(
        "IE00B5BMR087", "USD", soubor=tmp_path / "t.csv",
        kandidati=lambda i: ["CSSPX.MI", "CSSPX.SW"],
        mena_kotace=_kotace({"CSSPX.MI": "EUR", "CSSPX.SW": "USD"}),
        nazev=lambda d: nazvy.get(d, (None, "")),
    )
    assert t is None


def test_nalezeny_prevod_se_ulozi_a_rucni_ma_prednost(tmp_path):
    """Stálost: stejný výpis podruhé dá stejné tickery, i kdyby Yahoo
    mezitím vracel něco jiného — jinak by se duplicity nepoznaly."""
    soubor = tmp_path / "t.csv"
    soubor.write_text("isin,mena,ticker\nIE00B5BMR087,EUR,SXR8.DE\n", encoding="utf-8")
    t = isin.preloz("IE00B5BMR087", "EUR", soubor=soubor,
                    kandidati=lambda i: pytest.fail("ruční záznam nesmí jít na síť"))
    assert t == "SXR8.DE"


def test_import_prevede_isin_a_necha_ho_v_poznamce(tmp_path):
    vypis = tmp_path / "v.csv"
    vypis.write_text(
        "Datum;Operace;ISIN;Množství;Cena za kus;Objem;Měna\n"
        "15.03.2024;Nákup;IE00B5BMR087;2;520,00;-1040,00;EUR\n"
        "16.03.2024;Nákup;IE00B4L5Y983;1;90,00;-90,00;EUR\n", encoding="utf-8")
    tabulka = {("IE00B5BMR087", "EUR"): "CSPX.AS"}
    v = nacti_vypis(vypis, prekladac=lambda i, m: tabulka.get((i, m)))

    assert v.pohyby[0].symbol == "CSPX.AS"
    assert "IE00B5BMR087" in v.pohyby[0].note, "ISIN musí zůstat dohledatelný"
    assert v.prevedene == {"IE00B5BMR087": "CSPX.AS"}
    assert v.pohyby[1].symbol == "IE00B4L5Y983", "nepřevedený zůstane, nezmizí"
    assert v.neprevedene == ["IE00B4L5Y983"]
    assert v.pohyby[0].type is TxType.BUY
