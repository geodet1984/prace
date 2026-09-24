"""Kurzy ČNB.

Testy míří na chyby, po kterých vyjde portfolio v jiném řádu nebo se
tiše změní podle toho, jestli je zrovna internet: špatně přečtené
"množství" u jenu, desetinná čárka, kurz z jiného dne, a hlavně rozdíl
mezi cestou ze sítě a cestou z cache.

**Na síť tady nesahá jediný test.** Odpověď ČNB se podstrkuje jako text,
protože testy, které padají podle stavu připojení, se dřív nebo později
začnou přeskakovat.
"""

from datetime import date, timedelta

import pytest

from trading import fx

# Zkrácený, ale skutečný tvar odpovědi ČNB z 19. 1. 2024 (pátek).
LISTEK_19_01 = """19.01.2024 #14
země|měna|množství|kód|kurz
EMU|euro|1|EUR|24,715
Japonsko|jen|100|JPY|15,396
USA|dolar|1|USD|22,793
"""

LISTEK_15_03 = """15.03.2024 #53
země|měna|množství|kód|kurz
EMU|euro|1|EUR|25,320
Japonsko|jen|100|JPY|15,050
USA|dolar|1|USD|23,215
"""


# --- čtení lístku -------------------------------------------------------


def test_mnozstvi_se_deli_na_jeden_kus():
    """Jen je na lístku za 100 kusů.

    Přečíst "15,396" jako kurz jednoho jenu znamená ocenit japonskou
    pozici stokrát dráž. Je to chyba, kterou v součtu nikdo nepozná,
    dokud se nepodívá na jednu konkrétní pozici.
    """
    listek = fx.parse_listek(LISTEK_19_01)
    assert listek.kurz("JPY") == pytest.approx(0.15396)
    assert listek.kurz("USD") == pytest.approx(22.793), "dolar je za jeden kus, nedělí se"


def test_desetinna_carka_je_desetinna():
    """"24,715" není 24715. Čárka je oddělovač desetin, ne tisíců."""
    assert fx.parse_listek(LISTEK_19_01).kurz("EUR") == pytest.approx(24.715)


def test_platnost_je_z_hlavicky_ne_z_dotazu():
    """ČNB na sobotu vrátí páteční lístek a v hlavičce to přizná.

    Kdybychom datum platnosti brali z dotazu, nešlo by odlišit "kurz
    k datu" od "poslední známý kurz" — a přehled by tvrdil jistotu,
    kterou nemá.
    """
    assert fx.parse_listek(LISTEK_19_01).platnost == date(2024, 1, 19)


def test_domaci_mena_ma_kurz_jedna():
    assert fx.parse_listek(LISTEK_19_01).kurz("CZK") == 1.0


def test_neznama_mena_je_lookuperror():
    """Účetní kniha chytá ``LookupError``, aby o modulu kurzů nemusela vědět.

    Kdyby ``KurzChybi`` dědil odjinud, kniha by chybějící kurz nezachytila
    a přehled by spadl místo toho, aby měnu přiznal jako nepřepočtenou.
    """
    with pytest.raises(LookupError):
        fx.parse_listek(LISTEK_19_01).kurz("XYZ")


def test_nesmyslny_listek_hlasi_chybu():
    with pytest.raises(fx.FxError):
        fx.parse_listek("<html>chyba 500</html>")


# --- cache versus síť ---------------------------------------------------


@pytest.fixture
def sit(monkeypatch):
    """Falešná ČNB. Počítá, kolikrát se na ni sáhlo."""
    volani = []

    def stahni(den, timeout=10.0):  # noqa: ARG001 - podpis skutečné funkce
        volani.append(den)
        return {date(2024, 1, 19): LISTEK_19_01, date(2024, 3, 15): LISTEK_15_03}.get(
            den, LISTEK_19_01
        )

    monkeypatch.setattr(fx, "stahni_listek", stahni)
    return volani


def test_kurz_z_cache_je_stejny_jako_ze_site(tmp_path, sit):
    """**Nejdůležitější test modulu.**

    Kdyby se do cache ukládal už rozparsovaný lístek, byla by cesta
    z disku jiný kus kódu než cesta ze sítě — a dvě spuštění téhož
    přehledu by mohla dát dvě různá čísla podle toho, jestli byl zrovna
    internet. Ukládá se proto surový text a parser je jeden.
    """
    ze_site = fx.Kurzovnik(cache_dir=tmp_path).kurz("USD", date(2024, 1, 19))
    z_cache = fx.Kurzovnik(cache_dir=tmp_path, povolit_sit=False).kurz("USD", date(2024, 1, 19))

    assert z_cache == ze_site
    assert len(sit) == 1, "podruhé se na síť sahat nemělo"


def test_cache_uklada_surovy_text(tmp_path, sit):
    """Cache má být čitelná i lidským okem — je to archiv kurzovních lístků."""
    fx.Kurzovnik(cache_dir=tmp_path).kurz("USD", date(2024, 1, 19))
    (soubor,) = list(tmp_path.glob("*.txt"))
    assert soubor.read_text(encoding="utf-8") == LISTEK_19_01


def test_bez_site_a_bez_cache_je_chyba(tmp_path):
    """Radši hlasitá chyba než tichý kurz 1:1.

    Dosadit jedničku by z dolarové pozice udělalo dvacetinu skutečné
    korunové hodnoty a nikde by to nebylo poznat.
    """
    with pytest.raises(fx.KurzChybi):
        fx.Kurzovnik(cache_dir=tmp_path, povolit_sit=False).kurz("USD", date(2024, 1, 19))


def test_starsi_listek_jako_nouzovka_se_prizna(tmp_path, sit):
    """Bez sítě se sáhne po nejbližším starším lístku — ale musí to být vidět.

    Kurz z jiného dne je odhad. Použít ho tiše znamená vydávat odhad za
    účetní údaj; proto se zapisuje do ``nahrady`` a přehled to hlásí.
    """
    fx.Kurzovnik(cache_dir=tmp_path).kurz("USD", date(2024, 1, 19))

    offline = fx.Kurzovnik(cache_dir=tmp_path, povolit_sit=False)
    assert offline.kurz("USD", date(2024, 6, 1)) == pytest.approx(22.793)
    assert offline.nahrady == {date(2024, 6, 1): date(2024, 1, 19)}
    assert offline.listek(date(2024, 6, 1)).zdroj == "cache-starsi"


def test_nouzovka_nebere_kurz_z_budoucnosti(tmp_path, sit):
    """Náhradní lístek smí být jen starší, nikdy novější.

    Ocenit lednový nákup březnovým kurzem je pohled do budoucnosti se
    vším všudy: použije informaci, která v tu chvíli neexistovala.
    """
    fx.Kurzovnik(cache_dir=tmp_path).kurz("USD", date(2024, 3, 15))

    offline = fx.Kurzovnik(cache_dir=tmp_path, povolit_sit=False)
    with pytest.raises(fx.KurzChybi):
        offline.kurz("USD", date(2024, 1, 10))


def test_odpoved_na_budouci_datum_se_z_cache_neprebira(tmp_path, sit, monkeypatch):
    """Lístek k dnešku a dál se ještě změní.

    ČNB vyhlašuje odpoledne a na budoucí datum vrací zatím poslední známý
    kurz. Uložit takovou odpověď natrvalo by znamenalo evidenci, která si
    pamatuje kurz, jenž nikdy neplatil.
    """
    zitra = date.today() + timedelta(days=1)
    kurzovnik = fx.Kurzovnik(cache_dir=tmp_path)
    kurzovnik.kurz("USD", zitra)
    assert len(sit) == 1

    monkeypatch.setattr(fx, "CERSTVOST_S", -1)  # jako by soubor zestárl
    fx.Kurzovnik(cache_dir=tmp_path).kurz("USD", zitra)
    assert len(sit) == 2, "zítřejší kurz se měl ověřit znovu"


def test_stary_kurz_se_z_cache_bere_navzdy(tmp_path, sit, monkeypatch):
    """Vyhlášený kurz se zpětně nemění, takže cache staršího data nikdy nestárne."""
    fx.Kurzovnik(cache_dir=tmp_path).kurz("USD", date(2024, 1, 19))
    monkeypatch.setattr(fx, "CERSTVOST_S", -1)
    fx.Kurzovnik(cache_dir=tmp_path).kurz("USD", date(2024, 1, 19))
    assert len(sit) == 1


def test_poskozena_cache_se_stahne_znovu(tmp_path, sit):
    """Půlka souboru na disku nesmí znamenat půlku portfolia."""
    cesta = tmp_path / "2024-01-19.txt"
    cesta.write_text("uřízlé", encoding="utf-8")

    assert fx.Kurzovnik(cache_dir=tmp_path).kurz("USD", date(2024, 1, 19)) == pytest.approx(22.793)
    assert len(sit) == 1


def test_domaci_mena_nesaha_na_sit(tmp_path):
    """Kurz koruny ke koruně je jedna. Ptát se na něj ČNB je zbytečné —
    a hlavně by to znamenalo, že korunové portfolio bez sítě nefunguje."""
    kurzovnik = fx.Kurzovnik(cache_dir=tmp_path, povolit_sit=False)
    assert kurzovnik.kurz("CZK", date(2024, 1, 19)) == 1.0
