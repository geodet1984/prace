"""Denní hlídač portfolia.

Hlídač má dvě selhání a obě jsou tiché. Buď se ozývá pořád — pak si ho
člověk vypne a přijde i o to jedno důležité hlášení. Nebo se neozve, když
měl. Testy míří na obojí.
"""

from datetime import date, timedelta

import pytest

from trading.hlidac import (
    DNI_PRED_LHUTOU,
    PRAH_POHYBU_PCT,
    STATE_VERSION,
    Stav,
    Zprava,
    nove,
    serad,
    shrnuti,
    zapamatuj,
    zpravy,
)
from trading.ledger import Ledger, Transaction, TxType


def tx(day, typ, **kw):
    d = date.fromisoformat(day)
    if "amount" not in kw:
        znamenko = -1 if typ is TxType.BUY else 1
        kw["amount"] = znamenko * kw.get("quantity", 0) * kw.get("price", 0)
    return Transaction(day=d, type=typ, **kw)


@pytest.fixture
def kniha():
    led = Ledger()
    led.add(tx("2022-01-10", TxType.DEPOSIT, amount=100_000, currency="CZK"))
    led.add(tx("2022-01-10", TxType.BUY, symbol="CSPX.AS", quantity=10, price=400,
               currency="CZK"))
    return led


PREHLED = {"hodnota": 5_000.0, "mena": "CZK", "bez_ceny": [], "bez_kurzu": []}


# --- neopakovat se ------------------------------------------------------


def test_tataz_zprava_se_podruhe_neoznami(kniha):
    """Hlídač, který hlásí totéž každý den, se přestane číst.

    A s ním i to jedno hlášení, kvůli kterému existoval.
    """
    den = date(2025, 1, 10)
    stav = Stav()

    prvni = nove(zpravy(kniha, PREHLED, stav, k_datu=den), stav)
    assert prvni, "první běh musí něco najít (dávka prošla lhůtou)"
    zapamatuj(prvni, stav, PREHLED["hodnota"], den)

    druhy = nove(zpravy(kniha, PREHLED, stav, k_datu=den + timedelta(days=1)), stav)
    assert druhy == [], "podruhé už se totéž hlásit nesmí"


def test_nova_udalost_projde_i_kdyz_stara_uz_byla(kniha):
    """Umlčet jednu zprávu nesmí umlčet všechny."""
    den = date(2025, 1, 10)
    stav = Stav()
    zapamatuj(nove(zpravy(kniha, PREHLED, stav, k_datu=den), stav), stav,
              PREHLED["hodnota"], den)

    kniha.add(tx("2025-01-11", TxType.BUY, symbol="IWDA.AS", quantity=5, price=100,
                 currency="CZK"))
    pozdeji = date(2028, 1, 12)
    dalsi = nove(zpravy(kniha, PREHLED, stav, k_datu=pozdeji), stav)

    assert any("IWDA.AS" in z.text for z in dalsi)


# --- časový test --------------------------------------------------------


def test_blizici_se_lhuta_se_ohlasi_vcas(kniha):
    """Prodej den před uplynutím lhůty je zdanitelný, den po ní ne.

    Je to jediné místo v evidenci, kde má konkrétní datum přímý finanční
    dopad — a zapomene se na něj snadno, protože jde o tři roky.
    """
    lot = kniha.positions()[0]
    den = lot.osvobozeno_od() - timedelta(days=DNI_PRED_LHUTOU - 5)
    zpr = zpravy(kniha, PREHLED, Stav(), k_datu=den)

    lhuty = [z for z in zpr if z.kod.startswith("lhuta-blizi")]
    assert len(lhuty) == 1
    assert lhuty[0].zavaznost == "pozor"
    assert "CSPX.AS" in lhuty[0].text


def test_daleka_lhuta_se_neohlasi(kniha):
    """Upozorňovat dva roky předem znamená šum, ne informaci."""
    lot = kniha.positions()[0]
    den = lot.osvobozeno_od() - timedelta(days=DNI_PRED_LHUTOU + 30)
    zpr = zpravy(kniha, PREHLED, Stav(), k_datu=den)
    assert not [z for z in zpr if z.kod.startswith("lhuta")]


def test_kod_lhuty_nezavisi_na_poctu_zbyvajicich_dni(kniha):
    """Kdyby byl počet dní v kódu, každý další den by vyrobil "novou"
    zprávu a hlídač by hlásil totéž celé dva měsíce."""
    lot = kniha.positions()[0]
    a = zpravy(kniha, PREHLED, Stav(), k_datu=lot.osvobozeno_od() - timedelta(days=30))
    b = zpravy(kniha, PREHLED, Stav(), k_datu=lot.osvobozeno_od() - timedelta(days=20))
    kod_a = [z.kod for z in a if z.kod.startswith("lhuta-blizi")]
    kod_b = [z.kod for z in b if z.kod.startswith("lhuta-blizi")]
    assert kod_a == kod_b


# --- pohyb hodnoty ------------------------------------------------------


def test_velky_pohyb_se_ohlasi(kniha):
    stav = Stav(posledni_hodnota=10_000.0)
    prehled = {**PREHLED, "hodnota": 10_000 * (1 - (PRAH_POHYBU_PCT + 2) / 100)}
    zpr = [z for z in zpravy(kniha, prehled, stav, k_datu=date(2025, 6, 1))
           if z.kod.startswith("pohyb")]
    assert len(zpr) == 1
    assert zpr[0].zavaznost == "pozor", "propad má být nápadnější než růst"


def test_maly_pohyb_se_neohlasi(kniha):
    stav = Stav(posledni_hodnota=10_000.0)
    prehled = {**PREHLED, "hodnota": 10_000 * (1 - (PRAH_POHYBU_PCT - 2) / 100)}
    assert not [z for z in zpravy(kniha, prehled, stav, k_datu=date(2025, 6, 1))
                if z.kod.startswith("pohyb")]


def test_prvni_beh_pohyb_nehlasi(kniha):
    """Bez minulé hodnoty není proti čemu měřit — a vymýšlet si základ
    znamená falešné hlášení hned při zavedení."""
    assert not [z for z in zpravy(kniha, PREHLED, Stav(), k_datu=date(2025, 6, 1))
                if z.kod.startswith("pohyb")]


def test_jeden_propad_nehlasi_kazdy_den(kniha):
    """Během vleklého propadu se hlásí jednou denně nanejvýš, ne s každým
    dalším procentem."""
    den = date(2025, 6, 1)
    stav = Stav(posledni_hodnota=10_000.0)
    prvni = [z for z in zpravy(kniha, {**PREHLED, "hodnota": 9_000.0}, stav, k_datu=den)
             if z.kod.startswith("pohyb")]
    druhy = [z for z in zpravy(kniha, {**PREHLED, "hodnota": 8_000.0}, stav, k_datu=den)
             if z.kod.startswith("pohyb")]
    assert prvni[0].kod == druhy[0].kod


# --- zastaralá čísla ----------------------------------------------------


def test_chybejici_cena_se_prizna(kniha):
    """Tichý odhad vydávaný za skutečnost je horší než přiznaná nejistota."""
    zpr = zpravy(kniha, {**PREHLED, "bez_ceny": ["ICOM.L"]}, Stav(),
                 k_datu=date(2025, 6, 1))
    assert any("ICOM.L" in z.text and "odhad" in z.text for z in zpr)


def test_chybejici_kurz_se_prizna(kniha):
    zpr = zpravy(kniha, {**PREHLED, "bez_kurzu": ["USD"]}, Stav(), k_datu=date(2025, 6, 1))
    assert any("kurz" in z.text and "USD" in z.text for z in zpr)


# --- chyby v knize ------------------------------------------------------


def test_chyba_v_knize_se_hlasi_jako_chyba():
    """Když si evidence odporuje, jsou všechna ostatní čísla nespolehlivá."""
    led = Ledger()
    led.add(tx("2030-01-01", TxType.BUY, symbol="X", quantity=1, price=100))
    zpr = zpravy(led, PREHLED, Stav(), k_datu=date(2025, 6, 1))
    assert any(z.zavaznost == "chyba" for z in zpr)


def test_pouhe_podezreni_se_nehlasi():
    """Podezření bývá v pořádku — dividenda po prodeji pozice třeba.
    Hlásit ho denně je nejrychlejší cesta k vypnutému oznámení."""
    led = Ledger()
    led.add(tx("2024-01-02", TxType.BUY, symbol="X", quantity=1, price=100))
    led.add(tx("2024-02-02", TxType.SELL, symbol="X", quantity=1, price=110))
    led.add(tx("2024-03-02", TxType.DIVIDEND, symbol="X", amount=5))
    zpr = zpravy(led, PREHLED, Stav(), k_datu=date(2025, 6, 1))
    assert all(z.zavaznost != "chyba" for z in zpr)


# --- stav přes disk -----------------------------------------------------


def test_stav_prezije_ulozeni_a_nacteni(tmp_path):
    """Bod 5 z CLAUDE.md: co se hromadí v čase, musí přežít restart.

    Kdyby se nepamatoval, hlídač by po každém spuštění zopakoval všechno,
    co kdy řekl.
    """
    cesta = tmp_path / "stav.json"
    stav = Stav()
    zapamatuj([Zprava("a", "první"), Zprava("b", "druhá")], stav, 1234.5, date(2025, 6, 1))
    stav.uloz(cesta)

    nacteny = Stav.nacti(cesta)
    assert nacteny.oznamene == {"a", "b"}
    assert nacteny.posledni_hodnota == pytest.approx(1234.5)
    assert nacteny.posledni_beh == "2025-06-01"


def test_stav_jine_verze_zacne_od_nuly(tmp_path):
    """Číst starý formát "jak to půjde" znamená hlídače, který po změně
    tiše přestane hlásit část věcí."""
    cesta = tmp_path / "stav.json"
    cesta.write_text('{"version": 0, "oznamene": ["a"]}', encoding="utf-8")
    nacteny = Stav.nacti(cesta)
    assert nacteny.oznamene == set()
    assert nacteny.version == STATE_VERSION


def test_poskozeny_stav_neshodi_hlidace(tmp_path):
    cesta = tmp_path / "stav.json"
    cesta.write_text("{tohle není JSON", encoding="utf-8")
    assert Stav.nacti(cesta).oznamene == set()


def test_chybejici_stav_neni_chyba(tmp_path):
    assert Stav.nacti(tmp_path / "neexistuje.json").oznamene == set()


# --- výpis --------------------------------------------------------------


def test_chyby_jsou_nahore():
    zpr = serad([Zprava("c", "info", "info"), Zprava("a", "chyba", "chyba"),
                 Zprava("b", "pozor", "pozor")])
    assert [z.zavaznost for z in zpr] == ["chyba", "pozor", "info"]


def test_shrnuti_prazdne():
    assert shrnuti([]) == "Nic nového."


def test_shrnuti_jedne_zpravy_je_ta_zprava():
    assert shrnuti([Zprava("a", "Tohle je zpráva")]) == "Tohle je zpráva"


def test_shrnuti_zminuje_chyby_prednostne():
    text = shrnuti([Zprava("a", "x", "chyba"), Zprava("b", "y", "info")])
    assert "chyba" in text


# --- změna stavu trhu ---------------------------------------------------


def _trh(symbol, propad="u maxima", trend="nahoru", den="2026-09-24"):
    return [{"symbol": symbol, "den": den,
             "kategorie": {"propad": propad, "trend": trend, "neklid": "běžný"},
             "srovnani": {"veta": "Podobný stav nastal 40×."}}]


def test_zmena_stavu_trhu_se_ohlasi(kniha):
    """Hlásí se změna, ne stav. „U maxima" každý den by nikdo nečetl."""
    stav = Stav(kategorie={"SPY": {"propad": "u maxima", "trend": "nahoru", "neklid": "běžný"}})
    zpr = zpravy(kniha, PREHLED, stav, k_datu=date(2026, 9, 24), trh=_trh("SPY", "korekce"))
    trh = [z for z in zpr if z.kod.startswith("trh:")]
    assert len(trh) == 1
    assert "u maxima → korekce" in trh[0].text


def test_beze_zmeny_stavu_se_trh_nehlasi(kniha):
    stav = Stav(kategorie={"SPY": {"propad": "u maxima", "trend": "nahoru", "neklid": "běžný"}})
    zpr = zpravy(kniha, PREHLED, stav, k_datu=date(2026, 9, 24), trh=_trh("SPY"))
    assert not [z for z in zpr if z.kod.startswith("trh:")]


def test_prvni_pozorovani_titulu_se_nehlasi(kniha):
    """Nebylo s čím srovnat. Hlásit „nový stav" u všeho při zavedení je šum."""
    zpr = zpravy(kniha, PREHLED, Stav(), k_datu=date(2026, 9, 24), trh=_trh("SPY"))
    assert not [z for z in zpr if z.kod.startswith("trh:")]


def test_kategorie_trhu_prezije_restart(tmp_path):
    """Bod 5 z CLAUDE.md: bez uložení by se po restartu každá změna ztratila
    — hlídač by vždycky viděl „první pozorování" a nehlásil nikdy nic."""
    stav = Stav()
    zapamatuj([], stav, None, date(2026, 9, 24), trh=_trh("SPY", "korekce"))
    stav.uloz(tmp_path / "s.json")
    assert Stav.nacti(tmp_path / "s.json").kategorie["SPY"]["propad"] == "korekce"
