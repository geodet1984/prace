"""Evidence skutečného portfolia.

Testy míří na chyby, které se v účetnictví dělají snadno a poznají se
pozdě: špatně spárovaný prodej, poplatek započítaný dvakrát nebo vůbec,
zdvojený import výpisu a záměna "vydělal jsem" za "přisypal jsem".
"""

from datetime import date, timedelta

import pytest

from trading.ledger import (
    CASOVY_TEST,
    Ledger,
    LedgerError,
    Transaction,
    TxType,
    merge,
)


def tx(day, typ, **kw):
    """Zkratka: datum jako 'YYYY-MM-DD', částku dopočítáme, není-li zadaná."""
    d = date.fromisoformat(day)
    if "amount" not in kw:
        znamenko = -1 if typ is TxType.BUY else 1
        kw["amount"] = znamenko * (kw.get("quantity", 0) * kw.get("price", 0)) - kw.get("fee", 0)
    return Transaction(day=d, type=typ, **kw)


@pytest.fixture
def kniha():
    """Dva nákupy téhož titulu za různé ceny a v různých letech."""
    led = Ledger()
    led.add(tx("2020-01-15", TxType.DEPOSIT, amount=10_000))
    led.add(tx("2020-02-01", TxType.BUY, symbol="SPY", quantity=10, price=100, fee=5))
    led.add(tx("2022-06-01", TxType.BUY, symbol="SPY", quantity=10, price=200, fee=5))
    return led


# --- párování FIFO ------------------------------------------------------


def test_prodej_bere_nejstarsi_kusy(kniha):
    """FIFO, ne průměrná cena.

    Průměr by u dvou nákupů za 100 a 200 vyrobil zisk i tam, kde žádný
    není, a hlavně by rozmazal datum pořízení, na kterém stojí časový test.
    """
    kniha.add(tx("2023-01-10", TxType.SELL, symbol="SPY", quantity=10, price=150))
    (obchod,) = kniha.realized()

    assert obchod.bought == date(2020, 2, 1), "měly odejít kusy z prvního nákupu"
    assert obchod.quantity == 10
    # Pořizovací cena 10×100 + poplatek 5 = 1005, výnos 1500.
    assert obchod.pnl == pytest.approx(495.0)


def test_prodej_pres_hranici_davky_se_rozpadne_na_dva_zaznamy(kniha):
    """Prodej 15 kusů z dávek po 10 musí vzniknout jako dva spárované obchody.

    Sloučit je do jednoho by znamenalo jedno datum nákupu pro obojí —
    a tím pádem špatně spočítaný časový test pro polovinu kusů.
    """
    kniha.add(tx("2023-01-10", TxType.SELL, symbol="SPY", quantity=15, price=150))
    obchody = kniha.realized()

    assert len(obchody) == 2
    assert [o.quantity for o in obchody] == [10, 5]
    assert [o.bought for o in obchody] == [date(2020, 2, 1), date(2022, 6, 1)]


def test_castecny_prodej_nechava_zbytek_otevreny(kniha):
    kniha.add(tx("2023-01-10", TxType.SELL, symbol="SPY", quantity=15, price=150))
    assert kniha.holdings() == {"SPY": 5}
    assert kniha.positions()[0].day == date(2022, 6, 1), "zbýt má mladší dávka"


def test_prodej_vic_nez_drzite_je_chyba(kniha):
    """Účetní kniha nesmí umět zápornou pozici — long-only evidence.

    Tiše to spolknout by vyrobilo fiktivní zisk z kusů, které nikdy
    neexistovaly.
    """
    kniha.add(tx("2023-01-10", TxType.SELL, symbol="SPY", quantity=25, price=150))
    with pytest.raises(LedgerError, match="drženo jen"):
        kniha.realized()


# --- poplatky -----------------------------------------------------------


def test_nakupni_poplatek_zvysuje_porizovaci_cenu():
    led = Ledger()
    led.add(tx("2024-01-02", TxType.BUY, symbol="X", quantity=10, price=100, fee=20))
    (lot,) = led.positions()
    assert lot.cost == pytest.approx(1020.0)


def test_prodejni_poplatek_snizuje_vynos():
    led = Ledger()
    led.add(tx("2024-01-02", TxType.BUY, symbol="X", quantity=10, price=100))
    led.add(tx("2024-06-02", TxType.SELL, symbol="X", quantity=10, price=100, fee=20))
    (obchod,) = led.realized()
    assert obchod.pnl == pytest.approx(-20.0), "beze změny ceny je ztrátou přesně poplatek"


def test_poplatek_z_obchodu_se_nepocita_dvakrat():
    """Poplatek je v pořizovací ceně; kdyby se přičetl ještě zvlášť,
    celkový výsledek by ho odečetl dvakrát."""
    led = Ledger()
    led.add(tx("2024-01-02", TxType.BUY, symbol="X", quantity=10, price=100, fee=50))
    led.add(tx("2024-06-02", TxType.SELL, symbol="X", quantity=10, price=100, fee=50))
    v = led.valuation({})
    assert v["celkem"] == pytest.approx(-100.0)


# --- vklady versus výdělek ---------------------------------------------


def test_vklad_neni_zisk():
    """Nejdražší chyba v evidenci: považovat přírůstek na účtu za výdělek.

    Vložíte-li 10 000 a nic nekoupíte, portfolio má hodnotu 0 a výsledek
    je nula — ne deset tisíc.
    """
    led = Ledger()
    led.add(tx("2024-01-01", TxType.DEPOSIT, amount=10_000))
    v = led.valuation({})
    assert v["vlozeno"] == 10_000
    assert v["celkem"] == 0.0


def test_vyber_snizuje_vlozene_ne_vysledek():
    led = Ledger()
    led.add(tx("2024-01-01", TxType.DEPOSIT, amount=10_000))
    led.add(tx("2024-03-01", TxType.WITHDRAWAL, amount=-4_000))
    v = led.valuation({})
    assert v["vlozeno"] == 6_000
    assert v["celkem"] == 0.0


def test_dividenda_je_vynos_a_nemeni_porizovaci_cenu():
    led = Ledger()
    led.add(tx("2024-01-02", TxType.BUY, symbol="X", quantity=10, price=100))
    led.add(tx("2024-04-02", TxType.DIVIDEND, symbol="X", amount=35))
    led.add(tx("2024-04-02", TxType.TAX, amount=-5))
    v = led.valuation({"X": 100})
    assert v["porizovaci_cena"] == pytest.approx(1000.0)
    assert v["celkem"] == pytest.approx(30.0)


# --- ocenění ------------------------------------------------------------


def test_ocenění_scita_nerealizovany_a_realizovany_zisk():
    led = Ledger()
    led.add(tx("2024-01-02", TxType.BUY, symbol="A", quantity=10, price=100))
    led.add(tx("2024-01-02", TxType.BUY, symbol="B", quantity=10, price=100))
    led.add(tx("2024-06-02", TxType.SELL, symbol="B", quantity=10, price=120))
    v = led.valuation({"A": 150})

    assert v["nerealizovany_zisk"] == pytest.approx(500.0)
    assert v["realizovany_zisk"] == pytest.approx(200.0)
    assert v["celkem"] == pytest.approx(700.0)


def test_chybejici_cena_se_prizna_a_neshodi_vypocet():
    """Bez ceny se ocení pořizovací — ale musí být poznat, že jde o odhad.

    Tvářit se, že titul beze zdroje ceny stagnuje, je lež; spadnout kvůli
    jednomu titulu by ale znemožnilo přehled celého portfolia.
    """
    led = Ledger()
    led.add(tx("2024-01-02", TxType.BUY, symbol="NEZNAMY", quantity=10, price=100))
    v = led.valuation({})
    assert v["bez_ceny"] == ["NEZNAMY"]
    assert v["hodnota"] == pytest.approx(1000.0)


# --- časový test --------------------------------------------------------


def test_casovy_test_se_pocita_od_data_davky():
    """Přikoupení nesmí posunout osvobození u starších kusů."""
    led = Ledger()
    led.add(tx("2020-02-01", TxType.BUY, symbol="X", quantity=10, price=100))
    led.add(tx("2024-02-01", TxType.BUY, symbol="X", quantity=10, price=100))
    stary, novy = led.tax_clock(k_datu=date(2024, 6, 1))

    assert stary["splneno"] is True
    assert novy["splneno"] is False
    assert novy["dni_zbyva"] > 900


def test_prodej_tesne_pred_trema_lety_neprojde():
    """Den před koncem testu je pořád zdanitelný — a je to den, kdy se
    lidé nejčastěji spálí."""
    nakup = date(2021, 1, 10)
    led = Ledger()
    led.add(tx(nakup.isoformat(), TxType.BUY, symbol="X", quantity=1, price=100))

    tesne_pred = nakup + CASOVY_TEST - timedelta(days=1)
    led.add(tx(tesne_pred.isoformat(), TxType.SELL, symbol="X", quantity=1, price=200))
    (obchod,) = led.realized()
    assert obchod.prosel_casovym_testem is False


# --- import a duplicity -------------------------------------------------


def test_stejny_vypis_nahrany_dvakrat_nezdvoji_portfolio(kniha):
    """Nahrát výpis dvakrát je běžná nehoda; zdvojené kusy by nafoukly
    portfolio o titul, který nikdo nekoupil."""
    puvodni = kniha.holdings()
    pridano = kniha.extend(list(kniha.transactions))

    assert pridano == 0
    assert kniha.holdings() == puvodni


def test_merge_nemeni_puvodni_knihu(kniha):
    """Import se má dát nejdřív ukázat a teprve pak uložit."""
    pocet = len(kniha.transactions)
    novy = tx("2024-01-05", TxType.BUY, symbol="QQQ", quantity=1, price=400)

    nova, pridano, preskoceno = merge(kniha, [novy, novy])

    assert (pridano, preskoceno) == (1, 1)
    assert len(kniha.transactions) == pocet, "původní kniha zůstává nedotčená"
    assert len(nova.transactions) == pocet + 1


def test_kniha_zustava_setridena_i_po_dopsani_starsiho_obchodu():
    """Dopsat zpětně starší nákup se stává; FIFO by z nesetříděné knihy
    spárovalo prodej se špatnou dávkou."""
    led = Ledger()
    led.add(tx("2024-01-01", TxType.BUY, symbol="X", quantity=5, price=200))
    led.add(tx("2020-01-01", TxType.BUY, symbol="X", quantity=5, price=100))
    led.add(tx("2024-06-01", TxType.SELL, symbol="X", quantity=5, price=300))

    (obchod,) = led.realized()
    assert obchod.bought == date(2020, 1, 1)


# --- kolo přes disk -----------------------------------------------------


def test_ulozeni_a_nacteni_zachova_vysledek(kniha, tmp_path):
    """Evidence, která se po uložení a načtení spočítá jinak, je k ničemu."""
    kniha.add(tx("2023-01-10", TxType.SELL, symbol="SPY", quantity=12, price=150, fee=3))
    pred = kniha.valuation({"SPY": 180})

    cesta = tmp_path / "kniha.csv"
    kniha.to_csv(cesta)
    po = Ledger.from_csv(cesta).valuation({"SPY": 180})

    assert po == pred


def test_csv_oddeleny_strednikem_s_desetinnou_carkou(tmp_path):
    """Český Excel exportuje středníkem, protože čárka je desetinná.

    Načíst takový soubor jako oddělený čárkou znamená rozsekané řádky
    a hlášku o chybě u zápisu, který je ve skutečnosti v pořádku.
    """
    cesta = tmp_path / "kniha.csv"
    cesta.write_text(
        "day;type;symbol;quantity;price;amount;fee;currency;note\n"
        "2024-01-02;nakup;X;1,5;100;-150;0;CZK;\n",
        encoding="utf-8",
    )
    (lot,) = Ledger.from_csv(cesta).positions()
    assert lot.quantity == pytest.approx(1.5)


def test_neznamy_typ_pohybu_hlasi_radek(tmp_path):
    cesta = tmp_path / "kniha.csv"
    cesta.write_text(
        "day;type;symbol;quantity;price;amount;fee;currency;note\n"
        "2024-01-02;zaklinadlo;X;1;100;-100;0;USD;\n",
        encoding="utf-8",
    )
    with pytest.raises(LedgerError, match=":2:"):
        Ledger.from_csv(cesta)


def test_nakup_bez_symbolu_je_chyba():
    with pytest.raises(LedgerError, match="bez symbolu"):
        Transaction(day=date(2024, 1, 1), type=TxType.BUY, amount=-100, quantity=1, price=100)
