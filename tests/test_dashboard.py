"""Testy přehledu.

Dashboard je pozorovatel. Testy proto hlídají dvě věci: že čísla, která
ukazuje, odpovídají stavu na disku, a hlavně že do účtu **nemá jak sáhnout**.
Kdyby přes něj šlo obchodovat, existovala by druhá cesta k penězům mimo
`test_live.py`.
"""

import json
import threading
import urllib.error
import urllib.request
from datetime import date

import pytest

from trading import dashboard, strategies
from trading import data as data_module
from trading.execution import ZERO_COST
from trading.fx import KurzyPodleDne
from trading.live import PaperTrader, make_config
from trading.risk import RiskConfig


@pytest.fixture
def ucet(tmp_path):
    """Skutečný paper účet přehraný přes syntetickou historii.

    Ručně psaný JSON by testoval jen náš vlastní výmysl. Tohle je formát,
    který doopravdy vypadne z `PaperTrader.save()`.
    """
    path = tmp_path / "state.json"
    config = make_config(
        100_000.0,
        ZERO_COST,
        RiskConfig(risk_per_trade=0.02, max_position_pct=1.0, max_daily_loss_pct=None),
    )
    trader = PaperTrader(strategies.create("sma_crossover"), config, path)
    trader.step({"X": data_module.synthetic("X", days=500, seed=6)})
    trader.save()
    return path


# --- výpočty ------------------------------------------------------------


def test_equity_matches_cash_plus_positions(ucet):
    """Hodnota účtu je hotovost plus pozice v posledních známých cenách.

    Snadná chyba: sečíst hotovost s *pořizovací* cenou pozic. Účet by pak
    vypadal, že nekolísá, dokud se nic nezavře.
    """
    raw = json.loads(ucet.read_text())
    snap = dashboard.snapshot(ucet)

    ocekavano = raw["cash"] + sum(
        p["quantity"] * raw["last_price"][p["symbol"]] for p in raw["positions"]
    )
    assert snap["ucet"]["equity"] == pytest.approx(ocekavano)


def test_profit_factor_is_none_without_losses(tmp_path):
    """Série bez ztráty nemá profit factor.

    Dělení nulou by dalo `inf`, což se v tabulce čte jako kvalita strategie,
    ne jako "málo dat". Proto `None`, ne nekonečno.
    """
    obchody = [{"pnl": 10.0, "exit_reason": "", "days_held": 3.0}]
    assert dashboard._trade_stats(obchody)["profit_factor"] is None


def test_pnl_is_after_fees(tmp_path):
    """Zisk obchodu je po poplatcích — hrubý zisk nikdo neinkasuje."""
    stav = {
        "trades": [
            {
                "symbol": "X",
                "quantity": 10.0,
                "entry_time": "2024-01-01T00:00:00",
                "entry_price": 100.0,
                "exit_time": "2024-01-10T00:00:00",
                "exit_price": 110.0,
                "fees": 25.0,
                "exit_reason": "signál",
            }
        ]
    }
    (obchod,) = dashboard._trade_rows(stav)
    assert obchod["pnl"] == pytest.approx(100.0 - 25.0)


def test_realized_curve_starts_at_initial_capital(ucet):
    """Křivka začíná na počátečním kapitálu a mění se jen uzavřenými obchody."""
    snap = dashboard.snapshot(ucet)
    krivka = snap["krivka"]
    assert krivka[0]["equity"] == pytest.approx(snap["ucet"]["initial_capital"])
    assert len(krivka) == len(snap["obchody"]) + 1

    posledni = snap["ucet"]["initial_capital"] + sum(t["pnl"] for t in snap["obchody"])
    assert krivka[-1]["equity"] == pytest.approx(posledni)


def test_snapshot_is_json_serialisable(ucet):
    """Přehled musí projít `json.dumps` bez NaN.

    NaN je validní float, ale ne validní JSON — prohlížeč by na něm spadl
    a stránka by zůstala prázdná bez jediné chybové hlášky.
    """
    json.dumps(dashboard.snapshot(ucet), allow_nan=False)


def test_missing_state_is_explained(tmp_path):
    with pytest.raises(dashboard.DashboardError, match="neexistuje"):
        dashboard.snapshot(tmp_path / "nikde.json")


def test_broken_state_is_explained(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{tohle není JSON")
    with pytest.raises(dashboard.DashboardError, match="JSON"):
        dashboard.snapshot(path)


# --- server -------------------------------------------------------------


@pytest.fixture
def server(ucet):
    """Server na volném portu; vlákno se ukončí s testem."""
    httpd = dashboard.make_server(ucet, port=0)
    vlakno = threading.Thread(target=httpd.serve_forever, daemon=True)
    vlakno.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", ucet
    httpd.shutdown()
    httpd.server_close()
    vlakno.join(timeout=5)


def test_server_serves_snapshot(server):
    url, _ = server
    with urllib.request.urlopen(f"{url}/api/prehled") as odpoved:
        payload = json.loads(odpoved.read())
    assert payload["ucet"]["strategy"].startswith("sma_crossover")


@pytest.mark.parametrize("metoda", ["POST", "PUT", "DELETE", "PATCH"])
def test_server_refuses_to_change_anything(server, metoda):
    """Zapisující metody jsou odmítnuté, ne nenaimplementované.

    Tohle je ta podstatná vlastnost: přehled nesmí být druhá cesta, jak
    hnout účtem. Ta jediná vede přes `trading paper`.
    """
    url, stav = server
    pred = stav.read_bytes()

    zadost = urllib.request.Request(f"{url}/api/prehled", data=b"{}", method=metoda)
    with pytest.raises(urllib.error.HTTPError) as chyba:
        urllib.request.urlopen(zadost)

    assert chyba.value.code == 405
    assert stav.read_bytes() == pred


def test_server_never_writes_to_state(server):
    """Ani čtení nesmí soubor se stavem změnit — dashboard je pozorovatel."""
    url, stav = server
    pred = stav.read_bytes()
    mtime = stav.stat().st_mtime

    for cesta in ("/", "/api/prehled", "/api/verze"):
        urllib.request.urlopen(f"{url}{cesta}").read()

    assert stav.read_bytes() == pred
    assert stav.stat().st_mtime == mtime


def test_template_is_read_from_disk_on_every_request(server, monkeypatch, tmp_path):
    """Šablona se čte při každém požadavku, ne jednou při startu.

    Kdyby se cachovala, editace `ui/index.html` by se projevila až po
    restartu serveru — a živé úpravy z editoru by přestaly fungovat.
    """
    url, _ = server
    sablona = tmp_path / "index.html"
    sablona.write_text("<h1>první</h1>", encoding="utf-8")
    monkeypatch.setattr(dashboard, "INDEX", sablona)

    assert b"prvn" in urllib.request.urlopen(url + "/").read()

    sablona.write_text("<h1>druhá</h1>", encoding="utf-8")
    assert b"druh" in urllib.request.urlopen(url + "/").read()


def test_version_endpoint_tracks_state_changes(server):
    """Otisk se změní, když se změní stav — jinak by se stránka neobnovila."""
    url, stav = server
    pred = json.loads(urllib.request.urlopen(f"{url}/api/verze").read())

    obsah = json.loads(stav.read_text())
    obsah["cash"] += 1.0
    stav.write_text(json.dumps(obsah))

    po = json.loads(urllib.request.urlopen(f"{url}/api/verze").read())
    assert po["stav"] != pred["stav"]


def test_unknown_path_is_404(server):
    url, _ = server
    with pytest.raises(urllib.error.HTTPError) as chyba:
        urllib.request.urlopen(f"{url}/api/neexistuje")
    assert chyba.value.code == 404


# --- skutečné portfolio -------------------------------------------------
#
# Kniha je volitelná. Tyhle testy hlídají hlavně to, že její nepřítomnost,
# rozbitost ani chybějící síť nesmí shodit zbytek přehledu — a že ani
# u ní není dashboard druhá cesta, jak něčím hnout.

KNIHA_USD = (
    "day,type,symbol,quantity,price,amount,fee,currency,note\n"
    "2024-01-02,vklad,,,,2000.00,,USD,vklad\n"
    "2024-01-02,nakup,X,1,100.00,-100.00,0,USD,\n"
)

KURZY_TEST = KurzyPodleDne({("USD", date(2024, 1, 2)): 20.0, ("USD", date(2026, 1, 2)): 18.0})


@pytest.fixture(autouse=True)
def bez_pameti():
    """Přehled portfolia se memoizuje kvůli síti; mezi testy to musí zmizet."""
    dashboard._PORTFOLIO_MEMO.clear()
    yield
    dashboard._PORTFOLIO_MEMO.clear()


@pytest.fixture
def kniha(tmp_path):
    cesta = tmp_path / "portfolio.csv"
    cesta.write_text(KNIHA_USD, encoding="utf-8")
    return cesta


@pytest.fixture
def server_s_knihou(ucet, kniha, monkeypatch):
    """Server s knihou a bez sítě — ceny i kurzy jsou podstrčené.

    Test, který sahá na Yahoo a ČNB, padá podle stavu připojení a začne
    se přeskakovat. Obě místa, kudy vede síť, jsou proto zaslepená.
    """
    monkeypatch.setattr(dashboard, "_ceny_pozic", lambda symbols: {"X": 110.0})
    monkeypatch.setattr(
        dashboard.fx_module, "Kurzovnik", lambda *a, **kw: KURZY_TEST  # noqa: ARG005
    )
    httpd = dashboard.make_server(ucet, port=0, ledger_path=kniha)
    vlakno = threading.Thread(target=httpd.serve_forever, daemon=True)
    vlakno.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", kniha
    httpd.shutdown()
    httpd.server_close()
    vlakno.join(timeout=5)


def test_portfolio_rozdeluje_zhodnoceni_na_aktivum_a_kurz(kniha):
    """Endpoint musí nést obě půlky rozpadu, ne jen výsledek.

    Bez nich je korunové číslo zavádějící: růst titulu a pohyb kurzu se
    v součtu vyruší a stránka ukáže nulu bez vysvětlení.
    """
    snap = dashboard.portfolio_snapshot(
        kniha, prices={"X": 110.0}, kurzovnik=KURZY_TEST, k_datu=date(2026, 1, 2)
    )
    v = snap["prehled"]

    assert v["vykon_aktiva"] == pytest.approx(200.0)
    assert v["kurzovy_rozdil"] == pytest.approx(-220.0)
    assert v["vykon_aktiva"] + v["kurzovy_rozdil"] == pytest.approx(
        v["nerealizovany_zisk"] + v["realizovany_zisk"]
    )
    assert any("kurz" in u for u in snap["upozorneni"]), "vyrušení kurzem se má říct nahlas"


def test_portfolio_snapshot_is_json_serialisable(kniha):
    """Datum ani NaN v JSONu neprojdou a stránka by zůstala prázdná bez hlášky."""
    snap = dashboard.portfolio_snapshot(
        kniha, prices={"X": 110.0}, kurzovnik=KURZY_TEST, k_datu=date(2026, 1, 2)
    )
    json.dumps(snap, allow_nan=False)


def test_bez_knihy_je_oddil_prazdny_ne_chyba(server):
    """Většina lidí skutečné portfolio nevede. Nevedená kniha není chyba.

    404 by v konzoli prohlížeče vypadalo jako rozbitý dashboard a svádělo
    k hledání závady, která žádná není.
    """
    url, _ = server
    with urllib.request.urlopen(f"{url}/api/portfolio") as odpoved:
        payload = json.loads(odpoved.read())
    assert payload["vedena"] is False
    assert payload["duvod"]


def test_neexistujici_kniha_neshodi_dashboard(ucet, tmp_path):
    """Zadaná, ale chybějící cesta je provozní stav, ne pád."""
    payload = dashboard._portfolio_payload(tmp_path / "nikde.csv")
    assert payload["vedena"] is False
    assert "neexistuje" in payload["duvod"]


def test_rozbita_kniha_neshodi_zbytek_prehledu(ucet, tmp_path):
    """Překlep v CSV je chyba uživatele, ne serveru — papírový účet musí dál fungovat."""
    cesta = tmp_path / "portfolio.csv"
    cesta.write_text(
        "day,type,symbol,quantity,price,amount,fee,currency,note\n2024-01-02,zaklinadlo,,,,1,,CZK,\n",
        encoding="utf-8",
    )
    payload = dashboard._portfolio_payload(cesta)
    assert payload["vedena"] is False


def test_server_serves_portfolio(server_s_knihou):
    url, _ = server_s_knihou
    with urllib.request.urlopen(f"{url}/api/portfolio") as odpoved:
        payload = json.loads(odpoved.read())
    assert payload["vedena"] is True
    assert payload["prehled"]["kurzovy_rozdil"] == pytest.approx(-220.0)


@pytest.mark.parametrize("metoda", ["POST", "PUT", "DELETE", "PATCH"])
def test_portfolio_endpoint_je_taky_jen_ke_cteni(server_s_knihou, metoda):
    """Nová cesta nesmí být dírou v pravidle, které platí pro celý dashboard.

    Účetní kniha je soubor uživatele; kdyby přes ni šlo psát, byl by
    přehled cestou, jak si přepsat vlastní evidenci z prohlížeče.
    """
    url, kniha = server_s_knihou
    pred = kniha.read_bytes()

    zadost = urllib.request.Request(f"{url}/api/portfolio", data=b"{}", method=metoda)
    with pytest.raises(urllib.error.HTTPError) as chyba:
        urllib.request.urlopen(zadost)

    assert chyba.value.code == 405
    assert kniha.read_bytes() == pred


def test_cteni_prehledu_knihu_nemeni(server_s_knihou):
    """Ani čtení nesmí do knihy sáhnout — je to evidence, ne pracovní soubor."""
    url, kniha = server_s_knihou
    pred, mtime = kniha.read_bytes(), kniha.stat().st_mtime

    for cesta in ("/", "/api/prehled", "/api/portfolio", "/api/verze"):
        urllib.request.urlopen(f"{url}{cesta}").read()

    assert kniha.read_bytes() == pred
    assert kniha.stat().st_mtime == mtime


def test_verze_sleduje_i_knihu(server_s_knihou):
    """Dopsaný obchod se má projevit stejně jako pohyb účtu.

    Bez otisku knihy by stránka ukazovala staré portfolio, dokud by ji
    někdo ručně neobnovil — a nikdo by nepoznal proč.
    """
    url, kniha = server_s_knihou
    pred = json.loads(urllib.request.urlopen(f"{url}/api/verze").read())

    kniha.write_text(KNIHA_USD + "2024-02-02,poplatek,,,,-10.00,,USD,\n", encoding="utf-8")

    po = json.loads(urllib.request.urlopen(f"{url}/api/verze").read())
    assert po["kniha"] != pred["kniha"]


def test_missing_state_reports_503_not_crash(tmp_path):
    """Chybějící účet je provozní stav, ne pád serveru."""
    httpd = dashboard.make_server(tmp_path / "nikde.json", port=0)
    vlakno = threading.Thread(target=httpd.serve_forever, daemon=True)
    vlakno.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as chyba:
            urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_address[1]}/api/prehled")
        assert chyba.value.code == 503
    finally:
        httpd.shutdown()
        httpd.server_close()
        vlakno.join(timeout=5)
