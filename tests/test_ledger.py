"""Evidence skutečného portfolia.

Testy míří na chyby, které se v účetnictví dělají snadno a poznají se
pozdě: špatně spárovaný prodej, poplatek započítaný dvakrát nebo vůbec,
zdvojený import výpisu a záměna "vydělal jsem" za "přisypal jsem".
"""

from datetime import date, timedelta

import pytest

from trading import ledger as ledger_module
from trading.fx import KurzyPodleDne, PevneKurzy
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


# --- koruny: kurz a rozpad zhodnocení -----------------------------------
#
# Uživatel nakupuje v dolarech a žije v korunách, takže drží dvě sázky
# najednou. Testy níž hlídají, že se dají od sebe odlišit — a že se
# přitom nezapočítají dvakrát.

#: Dolar za dvacet při nákupu, za osmnáct při ocenění: koruna posílila
#: o deset procent. Kurz je vždy "kolik korun stojí jeden dolar".
KURZY = KurzyPodleDne(
    {
        ("USD", date(2024, 1, 2)): 20.0,
        ("USD", date(2024, 6, 2)): 19.0,
        ("USD", date(2026, 1, 2)): 18.0,
        ("EUR", date(2024, 1, 2)): 25.0,
        ("EUR", date(2026, 1, 2)): 25.0,
    }
)

DNES = date(2026, 1, 2)


def usd_kniha():
    """Jeden dolarový nákup za sto dolarů."""
    led = Ledger()
    led.add(tx("2024-01-02", TxType.BUY, symbol="X", quantity=1, price=100, currency="USD"))
    return led


def test_rust_titulu_pohlceny_posilenim_koruny_je_nula():
    """Deset procent nahoru na titulu a deset procent nahoru na koruně = nic.

    Tohle je celý důvod, proč rozpad existuje. Kdo vidí jen výsledek,
    myslí si, že se nestalo nic; přitom se staly dvě velké věci proti
    sobě a jedna z nich se dá řídit.
    """
    v = usd_kniha().valuation_czk({"X": 110.0}, KURZY, k_datu=DNES)

    assert v["celkem"] == pytest.approx(-20.0), "1980 Kč hodnota proti 2000 Kč pořizovací"
    assert v["vykon_aktiva"] == pytest.approx(200.0), "titul sám vydělal 10 USD po 20 Kč"
    assert v["kurzovy_rozdil"] == pytest.approx(-220.0)


def test_vykon_aktiva_a_kurz_se_nescitaji_dvakrat():
    """Rozpad podle příčiny musí dát přesně zisk z pozic, ne o kus víc.

    Smíšený člen ``q·(c₁−c₀)·(r₁−r₀)`` musí připadnout právě jedné
    z položek. Připočíst ho oběma je snadná chyba, po které rozpad sedí
    na papíře a nesedí v součtu — a přesně tak vzniká přehled, kterému
    se pak nedá věřit ani v jednom čísle.
    """
    led = usd_kniha()
    led.add(tx("2024-01-02", TxType.BUY, symbol="Y", quantity=2, price=50, currency="USD"))
    led.add(tx("2024-06-02", TxType.SELL, symbol="Y", quantity=2, price=70, currency="USD"))
    led.add(tx("2024-01-02", TxType.BUY, symbol="E", quantity=3, price=40, currency="EUR"))

    v = led.valuation_czk({"X": 110.0, "E": 45.0}, KURZY, k_datu=DNES)

    assert v["vykon_aktiva"] + v["kurzovy_rozdil"] == pytest.approx(
        v["nerealizovany_zisk"] + v["realizovany_zisk"]
    )


def test_rozpad_podle_druhu_dava_stejne_celkem_jako_v_puvodni_mene():
    """Přepočet nesmí měnit strukturu výsledku, jen jednotku.

    Kniha vedená v jediné měně s kurzem 1,0 musí v korunách dát tytéž
    položky jako ``valuation``. Kdyby se lišily, byl by rozdíl v jednom
    z obou výpočtů, ne v kurzu.
    """
    led = usd_kniha()
    led.add(tx("2024-04-02", TxType.DIVIDEND, symbol="X", amount=5, currency="USD"))
    led.add(tx("2024-04-02", TxType.TAX, amount=-1, currency="USD"))

    jedna = PevneKurzy({"USD": 1.0})
    v_czk = led.valuation_czk({"X": 110.0}, jedna, k_datu=DNES)
    v_usd = led.valuation({"X": 110.0})

    for klic in ("hodnota", "nerealizovany_zisk", "dividendy", "dane", "celkem", "vlozeno"):
        assert v_czk[klic] == pytest.approx(v_usd[klic]), klic


def test_kurz_se_bere_ke_dni_nakupu_ne_dnesni():
    """Dnešní kurz na starý nákup je táž chyba jako pohled do budoucnosti.

    Použije informaci, kterou jste v den nákupu neměli. Pořizovací cena
    v korunách je dána kurzem *tehdy* — a přesně proto může pozice
    v korunách prodělávat, i když v dolarech vydělává.
    """
    v = usd_kniha().valuation_czk({"X": 100.0}, KURZY, k_datu=DNES)

    assert v["porizovaci_cena"] == pytest.approx(2000.0), "100 USD × 20 Kč z ledna 2024"
    assert v["hodnota"] == pytest.approx(1800.0), "táž cena, ale dnešní kurz 18"
    assert v["vykon_aktiva"] == pytest.approx(0.0), "titul se nehnul"
    assert v["kurzovy_rozdil"] == pytest.approx(-200.0), "celá ztráta je kurzová"


def test_dnesnim_kurzem_na_vsechno_by_kurzovy_rozdil_zmizel():
    """Kontrolní test k předchozímu: kdyby se použil jeden kurz na všechno,
    kurzový rozdíl by vyšel nula — a chyba by nebyla jak odhalit.

    Držíme ho tu proto, že "vypadá to správně" je u přepočtu měn slabý
    argument: špatná verze vypadá stejně dobře, jen tvrdí, že kurz nikdy
    nic neudělal.
    """
    jeden = PevneKurzy({"USD": 18.0})
    v = usd_kniha().valuation_czk({"X": 100.0}, jeden, k_datu=DNES)

    assert v["kurzovy_rozdil"] == pytest.approx(0.0)
    assert v["porizovaci_cena"] == pytest.approx(1800.0), "a pořizovací cena je jiná než pravdivá"


def test_realizovany_obchod_ma_taky_rozpad():
    """Prodej se ocení kurzem dne prodeje, nákup kurzem dne nákupu.

    Spočítat obojí jedním kurzem znamená schovat kurzový výsledek
    uzavřených obchodů — a ten je na rozdíl od nerealizovaného už
    definitivní.
    """
    led = Ledger()
    led.add(tx("2024-01-02", TxType.BUY, symbol="X", quantity=1, price=100, currency="USD"))
    led.add(tx("2024-06-02", TxType.SELL, symbol="X", quantity=1, price=100, currency="USD"))

    v = led.valuation_czk({}, KURZY, k_datu=DNES)

    assert v["realizovany_zisk"] == pytest.approx(-100.0), "100 USD za 19 místo za 20"
    assert v["vykon_aktiva"] == pytest.approx(0.0)
    assert v["kurzovy_rozdil"] == pytest.approx(-100.0)


def test_vklad_se_pocita_kurzem_dne_vkladu():
    """"Vloženo" se nesmí měnit, když jste nic nevložili.

    Přepočíst staré vklady dnešním kurzem znamená, že se referenční
    hodnota celé evidence hýbe s dolarem — a výnos proti ní pak nic
    neříká.
    """
    led = Ledger()
    led.add(tx("2024-01-02", TxType.DEPOSIT, amount=1000, currency="USD"))
    assert led.valuation_czk({}, KURZY, k_datu=DNES)["vlozeno"] == pytest.approx(20_000.0)


def test_chybejici_kurz_se_prizna_a_neshodi_prehled():
    """Měnu bez kurzu radši vynechat než přepočíst jedna ku jedné.

    Kurz 1,0 na dolarovou pozici udělá z dvaceti tisíc tisícovku a nikde
    to není poznat. Vynechaný řádek je vidět, tichý dvacetinásobek ne.
    """
    led = Ledger()
    led.add(tx("2024-01-02", TxType.BUY, symbol="X", quantity=1, price=100, currency="USD"))
    led.add(tx("2024-01-02", TxType.BUY, symbol="Z", quantity=1, price=100, currency="ISK"))

    v = led.valuation_czk({"X": 100.0, "Z": 100.0}, KURZY, k_datu=DNES)

    assert v["bez_kurzu"] == ["ISK"]
    assert v["pozic"] == 1, "islandská pozice do součtů nevstoupila"
    assert v["hodnota"] == pytest.approx(1800.0)
    assert v["vykon_aktiva"] + v["kurzovy_rozdil"] == pytest.approx(
        v["nerealizovany_zisk"] + v["realizovany_zisk"]
    ), "rovnost rozpadu musí platit i nad zúženou množinou"


def test_kazda_davka_ma_vlastni_kurz_nakupu():
    """Přikoupení nesmí přepsat kurz starší dávky.

    Je to táž vlastnost jako u časového testu: dávka si nese své datum,
    a tedy i svůj kurz. Zprůměrovat je znamená rozmazat obojí.
    """
    led = Ledger()
    led.add(tx("2024-01-02", TxType.BUY, symbol="X", quantity=1, price=100, currency="USD"))
    led.add(tx("2024-06-02", TxType.BUY, symbol="X", quantity=1, price=100, currency="USD"))

    v = led.valuation_czk({"X": 100.0}, KURZY, k_datu=DNES)
    kurzy_davek = [p["kurz_nakup"] for p in v["pozice"]]

    assert sorted(kurzy_davek) == [19.0, 20.0]
    assert v["porizovaci_cena"] == pytest.approx(3900.0)


# --- hotovost -----------------------------------------------------------
#
# Kniha dlouho neuměla zůstatky, a tak dolarová dividenda ponechaná
# v dolarech ztuhla na kurzu dne výplaty. Testy níž hlídají, že se ta díra
# nevrátí — a hlavně že se oprava nikde nezapočítá dvakrát.


def test_hotovost_se_vede_po_menach():
    """Zůstatek není jedno číslo. Dolary a koruny se nesčítají.

    Sečíst je (jako by kurz byl 1) je chyba, která se v korunové evidenci
    schová: 100 USD a 100 CZK vypadá jako 200 a nikdo nepozná, že jedna
    stovka je dvacetkrát větší než druhá.
    """
    led = Ledger()
    led.add(tx("2024-01-02", TxType.DEPOSIT, amount=10_000, currency="CZK"))
    led.add(tx("2024-01-03", TxType.BUY, symbol="X", quantity=1, price=100, fee=1, currency="USD"))
    led.add(tx("2024-04-02", TxType.DIVIDEND, symbol="X", amount=5, currency="USD"))

    assert led.cash() == {"CZK": 10_000.0, "USD": pytest.approx(-96.0)}


def test_hotovost_z_nakupu_se_pocita_z_kusu_a_ceny_ne_ze_sloupce_amount():
    """Poplatek musí z účtu odejít, i když ho výpis do částky nezapočítal.

    Někteří brokeři píšou do "amount" hodnotu obchodu bez poplatku. Brát
    hotovost odtud znamená, že se zůstatek rozchází přesně o poplatky —
    pomalu, po korunách a bez jediné hlášky.
    """
    led = Ledger()
    led.add(
        Transaction(
            day=date(2024, 1, 2),
            type=TxType.BUY,
            symbol="X",
            quantity=10,
            price=100,
            fee=20,
            amount=-1000,  # bez poplatku, jak to píše výpis
            currency="USD",
        )
    )
    (lot,) = led.positions()
    assert led.cash()["USD"] == pytest.approx(-lot.cost), "z účtu odešla pořizovací cena včetně "
    "poplatku"


def test_dolarova_dividenda_ponechana_v_dolarech_citi_pohyb_kurzu():
    """Regrese na díru popsanou v docstringu ``valuation_czk``.

    Dividenda inkasovaná při kurzu 20 a ponechaná v dolarech má při kurzu
    18 menší korunovou hodnotu. Dokud kniha nevedla hotovost, ztuhla na
    kurzu dne výplaty a další pohyb kurzu na ní nebyl vidět — přehled pak
    tvrdil, že se nestalo nic.
    """
    led = Ledger()
    led.add(tx("2024-01-02", TxType.DIVIDEND, symbol="X", amount=100, currency="USD"))

    v = led.valuation_czk({}, KURZY, k_datu=DNES)

    # 100 USD přišlo po 20 Kč, dnes je po 18 → dvě stovky pryč.
    assert v["kurzovy_rozdil_hotovosti"] == pytest.approx(-200.0)
    assert v["dividendy"] == pytest.approx(2000.0), "výnos se pořád počítá kurzem dne výplaty"


def test_celkem_je_hodnota_pozic_plus_hotovost_minus_vlozeno():
    """Identita, na které stojí celý korunový přehled.

    Výsledek je majetek minus vložené peníze — nic víc. Rozpad na položky
    (zisk, dividendy, daně, poplatky, kurz na hotovosti, spread ze směn) je
    jen vysvětlení, *odkud* to číslo je, a musí se na ně sečíst přesně.
    Rozejde-li se to, je někde peníz započtený dvakrát nebo vůbec.
    """
    led = Ledger()
    led.add(tx("2024-01-02", TxType.DEPOSIT, amount=50_000, currency="CZK"))
    led.add(tx("2024-01-02", TxType.EXCHANGE, amount=-40_000, currency="CZK"))
    led.add(tx("2024-01-02", TxType.EXCHANGE, amount=1_950, currency="USD"))
    led.add(tx("2024-01-02", TxType.BUY, symbol="X", quantity=5, price=100, fee=2, currency="USD"))
    led.add(tx("2024-06-02", TxType.SELL, symbol="X", quantity=2, price=120, fee=2, currency="USD"))
    led.add(tx("2024-06-02", TxType.DIVIDEND, symbol="X", amount=10, currency="USD"))
    led.add(tx("2024-06-02", TxType.TAX, amount=-1.5, currency="USD"))
    led.add(tx("2024-06-02", TxType.FEE, amount=-150, currency="CZK"))

    v = led.valuation_czk({"X": 130.0}, KURZY, k_datu=DNES)

    assert v["celkem"] == pytest.approx(v["hodnota"] + v["hotovost"] - v["vlozeno"])
    polozky = (
        v["nerealizovany_zisk"]
        + v["realizovany_zisk"]
        + v["dividendy"]
        + v["dane"]
        + v["poplatky"]
        + v["smeny"]
        + v["kurzovy_rozdil_hotovosti"]
    )
    assert polozky == pytest.approx(v["celkem"]), "rozpad se musí sečíst na celek"


def test_chybejici_smena_se_dopocte_a_nezmeni_vysledek():
    """Kniha bez zápisu směny musí vyjít stejně jako s ním.

    Většina lidí převod měny nezapíše — koupili dolary, aby mohli koupit
    ETF, a v hlavě je to jeden úkon. Kdyby se takový schodek nechal jako
    záporný dolarový zůstatek, spočítala by kniha při poklesu dolaru
    **zisk** z krátké pozice, kterou nikdo nedržel. Dopočet kurzem ČNB je
    korunově neutrální, takže čísla zůstanou tam, kde byla.
    """
    bez = Ledger()
    bez.add(tx("2024-01-02", TxType.DEPOSIT, amount=50_000, currency="CZK"))
    bez.add(tx("2024-01-02", TxType.BUY, symbol="X", quantity=5, price=100, currency="USD"))

    se_smenou = Ledger()
    se_smenou.add(tx("2024-01-02", TxType.DEPOSIT, amount=50_000, currency="CZK"))
    # 500 USD kurzem ČNB toho dne (20 Kč) = přesně 10 000 Kč, tedy bez spreadu.
    se_smenou.add(tx("2024-01-02", TxType.EXCHANGE, amount=-10_000, currency="CZK"))
    se_smenou.add(tx("2024-01-02", TxType.EXCHANGE, amount=500, currency="USD"))
    se_smenou.add(tx("2024-01-02", TxType.BUY, symbol="X", quantity=5, price=100, currency="USD"))

    a = bez.valuation_czk({"X": 100.0}, KURZY, k_datu=DNES)
    b = se_smenou.valuation_czk({"X": 100.0}, KURZY, k_datu=DNES)

    assert a["celkem"] == pytest.approx(b["celkem"])
    assert a["hotovost"] == pytest.approx(b["hotovost"])
    assert len(a["dopoctene_smeny"]) == 1, "a musí být poznat, že se dopočítávalo"
    assert b["dopoctene_smeny"] == []


def test_spread_brokera_ze_smeny_je_videt():
    """Za převod korun na dolary si broker vezme svoje. Má to být vidět.

    Obě strany směny přepočtené kurzem ČNB téhož dne se nesečtou na nulu —
    rozdíl je přesně to, co si broker nechal. U českých brokerů to bývá
    víc než poplatky za samotné nákupy, a přitom to nikde není napsané.
    """
    led = Ledger()
    led.add(tx("2024-01-02", TxType.DEPOSIT, amount=20_000, currency="CZK"))
    led.add(tx("2024-01-02", TxType.EXCHANGE, amount=-20_000, currency="CZK"))
    # Kurz ČNB je 20 Kč/USD, tedy férově 1 000 USD. Dostali jsme 990.
    led.add(tx("2024-01-02", TxType.EXCHANGE, amount=990, currency="USD"))

    v = led.valuation_czk({}, KURZY, k_datu=DNES)

    assert v["smeny"] == pytest.approx(-200.0), "10 USD po 20 Kč zůstalo brokerovi"
    assert v["celkem"] == pytest.approx(v["hodnota"] + v["hotovost"] - v["vlozeno"])


def test_nulova_strana_smeny_je_chyba():
    """Jednostranná směna vyrábí peníze z ničeho."""
    with pytest.raises(LedgerError, match="nulovou částkou"):
        Transaction(day=date(2024, 1, 2), type=TxType.EXCHANGE, amount=0, currency="CZK")


# --- zápis na disk ------------------------------------------------------


def test_pad_uprostred_zapisu_nechá_puvodni_knihu_celou(kniha, tmp_path, monkeypatch):
    """Useknutá kniha je horší než žádná: nejde z ní zjistit, co chybí.

    Proto se zapisuje do dočasného souboru a teprve hotový se přejmenuje.
    Test simuluje pád (plný disk, Ctrl-C, výjimka) uprostřed zápisu.
    """
    cesta = tmp_path / "kniha.csv"
    kniha.to_csv(cesta)
    puvodni = cesta.read_bytes()

    volani = {"n": 0}

    def obcas_spadne(hodnota):
        volani["n"] += 1
        if volani["n"] > 3:
            raise OSError("disk je plný")
        return ""

    monkeypatch.setattr(ledger_module, "_do_csv", obcas_spadne)
    kniha.add(tx("2024-01-05", TxType.BUY, symbol="QQQ", quantity=1, price=400))
    with pytest.raises(OSError, match="disk"):
        kniha.to_csv(cesta)

    assert cesta.read_bytes() == puvodni, "na místě knihy musí zůstat ta stará, celá"
    assert list(tmp_path.glob("*.tmp")) == [], "a po nepovedeném zápisu nesmí nic zbýt"


def test_komentare_a_strednik_prezijou_prepis_knihy(tmp_path):
    """Vzor knihy je z poloviny nápověda k formátu, oddělovač je nastavení Excelu.

    Příkaz, který připíše pohyb a přitom smaže dokumentaci nebo přehodí
    středníky na čárky, si uživatel podruhé nespustí — a vrátí se k ručnímu
    editování, kvůli kterému ten příkaz vznikl.
    """
    cesta = tmp_path / "kniha.csv"
    cesta.write_text(
        "# moje kniha\n# druhy radek napovedy\n"
        "day;type;symbol;quantity;price;amount;fee;currency;note\n"
        "2024-01-02;nakup;X;1;100;-100;0;USD;\n",
        encoding="utf-8",
    )
    led = Ledger.from_csv(cesta)
    led.add(tx("2024-02-02", TxType.BUY, symbol="Y", quantity=1, price=50, currency="USD"))
    led.to_csv(cesta)

    text = cesta.read_text(encoding="utf-8")
    assert text.startswith("# moje kniha\n# druhy radek napovedy\n")
    assert "day;type;symbol" in text, "středník musí zůstat středníkem"
    assert len(Ledger.from_csv(cesta).transactions) == 2


# --- kontrola knihy -----------------------------------------------------


def _zpravy(led, k_datu=date(2026, 1, 2)):
    return [(n.zavaznost, n.zprava) for n in led.kontrola(k_datu=k_datu)]


def test_kontrola_mlci_nad_poradnou_knihou():
    """Kontrola, která hlásí i správné zápisy, se za týden vypne."""
    led = Ledger()
    led.add(tx("2024-01-02", TxType.DEPOSIT, amount=10_000, currency="CZK"))
    led.add(tx("2024-01-03", TxType.EXCHANGE, amount=-2_000, currency="CZK"))
    led.add(tx("2024-01-03", TxType.EXCHANGE, amount=100, currency="USD"))
    led.add(tx("2024-01-04", TxType.BUY, symbol="X", quantity=1, price=90, currency="USD"))
    led.add(tx("2024-02-04", TxType.DIVIDEND, symbol="X", amount=2, currency="USD"))
    assert _zpravy(led) == []


def test_kontrola_najde_prodej_titulu_ktery_kniha_nezna():
    """Nejčastější překlep v tickeru: prodáte SPYY, které jste nekoupili.

    Bez kontroly to spadne až při výpočtu, a to hláškou o počtu kusů —
    ne o tom, že celý titul v knize chybí.
    """
    led = Ledger()
    led.add(tx("2024-01-02", TxType.BUY, symbol="SPY", quantity=1, price=100))
    led.add(tx("2024-06-02", TxType.SELL, symbol="SPZ", quantity=1, price=100))
    assert ("chyba", "prodej 1 ks SPZ, ale kniha ho nikdy nekoupila") in _zpravy(led)


def test_kontrola_najde_dividendu_u_nedrzeneho_titulu():
    """Může být v pořádku (rozhodný den bývá před výplatou), a proto je to
    jen podezření — ale typicky je to špatný ticker nebo chybějící nákup."""
    led = Ledger()
    led.add(tx("2024-04-02", TxType.DIVIDEND, symbol="X", amount=10))
    zavaznosti = [z for z, _ in _zpravy(led)]
    assert zavaznosti == ["podezreni"]


def test_kontrola_rozlisi_zaporne_koruny_od_zapornych_dolaru():
    """Dvě různé chyby, dvě různé rady.

    Mínus v domácí měně znamená chybějící vklad. Mínus v cizí měně je
    chybějící zápis směny. Splácat to do jedné hlášky vede k hledání
    vkladu tam, kde chybí převod.
    """
    bez_vkladu = Ledger()
    bez_vkladu.add(tx("2024-01-02", TxType.BUY, symbol="X", quantity=1, price=100, currency="CZK"))
    assert [z for z, _ in _zpravy(bez_vkladu)] == ["chyba"]
    assert "chybí vklad" in _zpravy(bez_vkladu)[0][1]

    bez_smeny = Ledger()
    bez_smeny.add(tx("2024-01-02", TxType.DEPOSIT, amount=10_000, currency="CZK"))
    bez_smeny.add(tx("2024-01-03", TxType.BUY, symbol="X", quantity=1, price=100, currency="USD"))
    zavaznost, zprava = _zpravy(bez_smeny)[0]
    assert zavaznost == "podezreni"
    assert "smena" in zprava


def test_kontrola_najde_datum_v_budoucnosti():
    """Skoro vždycky přehozený rok. Ocenit pozici kurzem, který ještě nebyl
    vyhlášen, nejde — a časový test by běžel od data, které nenastalo."""
    led = Ledger()
    led.add(tx("2027-01-02", TxType.DEPOSIT, amount=1000, currency="CZK"))
    assert [z for z, _ in _zpravy(led)] == ["chyba"]


def test_kontrola_najde_castku_o_rad_vedle():
    """Přebývající nula. Medián, ne průměr — jeden překlep by průměr zvedl
    tak, že by se sám schoval pod práh."""
    led = Ledger()
    led.add(tx("2024-01-02", TxType.DEPOSIT, amount=10_000, currency="CZK"))
    for den in range(3, 9):
        led.add(tx(f"2024-01-0{den}", TxType.FEE, amount=-100, currency="CZK"))
    led.add(tx("2024-01-09", TxType.FEE, amount=-100_000, currency="CZK"))

    podezrele = [z for z, _ in _zpravy(led) if z == "podezreni"]
    assert podezrele, "stotisícový poplatek mezi stokorunovými musí vybočit"
    assert any("nepřebývá nula" in zprava for _, zprava in _zpravy(led))


def test_kontrola_najde_rozpor_mezi_poctem_cenou_a_castkou():
    """Sloupec amount, který nesedí na počet × cenu, je překlep v jednom
    z nich. Kniha z něj počítá dál a nikde to nevyjde najevo, protože
    hotovost se bere z počtu a ceny, kdežto výpis od brokera z částky."""
    led = Ledger()
    led.add(
        Transaction(
            day=date(2024, 1, 2),
            type=TxType.BUY,
            symbol="X",
            quantity=1,
            price=512.40,
            amount=-12_998.50,
            currency="EUR",
        )
    )
    assert any("amount je" in zprava for _, zprava in _zpravy(led))


def test_expozice_ukazuje_kolik_portfolia_visi_na_kurzu():
    """Rozpad říká, co kurz udělal; expozice, co ještě může."""
    led = Ledger()
    led.add(tx("2024-01-02", TxType.BUY, symbol="X", quantity=1, price=100, currency="USD"))
    led.add(tx("2024-01-02", TxType.BUY, symbol="E", quantity=2, price=36, currency="EUR"))

    v = led.valuation_czk({"X": 100.0, "E": 36.0}, KURZY, k_datu=DNES)
    podily = {m["mena"]: m["podil_pct"] for m in v["meny"]}

    assert podily["USD"] == pytest.approx(50.0)
    assert podily["EUR"] == pytest.approx(50.0)
