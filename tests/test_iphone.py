"""Přístup z iPhonu v domácí síti.

Tohle je jediné místo, kde je přehled vidět z jiného zařízení než z Macu.
Chyba tu neznamená špatné číslo, ale portfolio otevřené celé Wi-Fi —
proto testy míří na to, co musí zůstat zavřené.
"""

import json
import threading
import urllib.error
import urllib.request

import pytest

from trading import dashboard, iphone


@pytest.fixture
def server(tmp_path, monkeypatch):
    """Server, který se tváří, že každý požadavek přichází z jiného zařízení."""
    monkeypatch.setattr(iphone, "je_mistni", lambda adresa: False)
    klic_soubor = tmp_path / "klic.txt"
    klic = iphone.novy_klic(klic_soubor)
    httpd = dashboard.make_server(tmp_path / "stav.json", port=0,
                                  ledger_path=tmp_path / "kniha.csv",
                                  klic_soubor=klic_soubor, sit_zapnout=False,
                                  sledovane=tmp_path / "sledovane.txt")
    vlakno = threading.Thread(target=httpd.serve_forever, daemon=True)
    vlakno.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", klic
    httpd.shutdown()
    httpd.server_close()


def _get(url, hlavicky=None):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=hlavicky or {})) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def _post(url, telo, hlavicky=None):
    h = {"Content-Type": "application/json", "X-Trader-Zapis": "1", **(hlavicky or {})}
    req = urllib.request.Request(url, data=json.dumps(telo).encode(), headers=h, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


@pytest.mark.parametrize("cesta", ["/", "/api/portfolio", "/api/trh", "/api/sledovane",
                                   "/manifest.json", "/api/verze"])
def test_bez_klice_nic_neuvidi(server, cesta):
    """Kdokoli na Wi-Fi bez spárování dostane 401 — ani kousek dat."""
    url, _ = server
    kod, telo, _ = _get(url + cesta)
    assert kod == 401
    assert b"portfolio" not in telo.lower() or b"sp\xc3\xa1rovan" in telo


def test_spatny_klic_neprojde(server):
    url, _ = server
    kod, _, _ = _get(url + "/?klic=hadam-klic")
    assert kod == 401


def test_klic_z_qr_se_ulozi_do_cookie(server):
    """Po naskenování QR nese klíč adresa; další požadavky stránky už cookie."""
    url, klic = server
    kod, _, hlavicky = _get(f"{url}/?klic={klic}")
    assert kod == 200
    cookie = hlavicky["Set-Cookie"]
    assert klic in cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie

    kod, _, _ = _get(url + "/api/sledovane", {"Cookie": f"{iphone.COOKIE}={klic}"})
    assert kod == 200


def test_iphone_si_nemuze_vystavit_novy_klic(server):
    """Spárovaný telefon nesmí zapnout přístup dalším zařízením ani
    vyměnit klíč — to jde jen na Macu."""
    url, klic = server
    kod, _ = _post(url + "/api/iphone", {"akce": "vymenit"},
                   {"Cookie": f"{iphone.COOKIE}={klic}"})
    assert kod == 403


def test_iphone_neuvidi_qr_s_klicem(server):
    url, klic = server
    kod, telo, _ = _get(url + "/api/iphone", {"Cookie": f"{iphone.COOKIE}={klic}"})
    data = json.loads(telo)
    assert kod == 200 and "odkaz" not in data and "qr" not in data


def test_zapis_z_iphonu_ze_stejne_stranky_projde(server):
    url, klic = server
    kod, _ = _post(url + "/api/sledovane", {"akce": "pridat", "symbol": "IWDA.AS"},
                   {"Cookie": f"{iphone.COOKIE}={klic}", "Origin": url})
    assert kod == 200


def test_zapis_z_ciziho_webu_i_s_cookie_neprojde(server):
    """Kdyby cookie nějak unikla do cizí stránky, zápis ho pořád zastaví
    kontrola původu."""
    url, klic = server
    kod, _ = _post(url + "/api/sledovane", {"akce": "pridat", "symbol": "IWDA.AS"},
                   {"Cookie": f"{iphone.COOKIE}={klic}", "Origin": "http://zly-web.example"})
    assert kod == 403


def test_novy_klic_zneplatni_stary(tmp_path):
    soubor = tmp_path / "k.txt"
    stary = iphone.novy_klic(soubor)
    novy = iphone.novy_klic(soubor)
    assert not iphone.over(stary, soubor) and iphone.over(novy, soubor)
    iphone.vypnout(soubor)
    assert not iphone.over(novy, soubor), "po vypnutí neplatí žádný klíč"


def test_soubor_s_klicem_cte_jen_vlastnik(tmp_path):
    soubor = tmp_path / "k.txt"
    iphone.novy_klic(soubor)
    assert oct(soubor.stat().st_mode)[-3:] == "600"


def test_qr_se_zmensi_cely_ne_jen_vyrez():
    """S pevnou šířkou a výškou stránka při zmenšení ořízla kód na levý
    horní roh a telefon ho nepřečetl. Musí mít viewBox a žádnou pevnou velikost."""
    import re

    svg = iphone.qr_svg("http://mac.local:8766/?klic=abc")
    hlavicka = re.match(r"\s*<svg[^>]*>", svg).group(0)
    assert "viewBox" in hlavicka
    assert not re.search(r'\s(width|height)="', hlavicka)


# --- server v síti (NAS) --------------------------------------------------


def test_parovani_vypise_odkaz_s_klicem(tmp_path, monkeypatch, capsys):
    from trading.cli import main

    monkeypatch.setattr(iphone, "KLIC", tmp_path / "k.txt")
    assert main(["parovani", "--adresa", "http://nas:8766/"]) == 0
    klic = iphone.nacti_klic(tmp_path / "k.txt")
    assert f"http://nas:8766/?klic={klic}" in capsys.readouterr().out


def test_parovani_novy_zneplatni_stary(tmp_path, monkeypatch, capsys):
    from trading.cli import main

    monkeypatch.setattr(iphone, "KLIC", tmp_path / "k.txt")
    main(["parovani", "--adresa", "http://nas:8766"])
    stary = iphone.nacti_klic(tmp_path / "k.txt")
    main(["parovani", "--adresa", "http://nas:8766", "--novy"])
    assert not iphone.over(stary, tmp_path / "k.txt")


def test_server_v_siti_bez_klice_nepusti_nikoho(tmp_path, monkeypatch):
    """Na NASu jde každý požadavek z jiného stroje. Bez klíče by se
    k portfoliu nedostal nikdo — ani s ním by nesměl vidět všechno."""
    monkeypatch.setattr(iphone, "je_mistni", lambda adresa: False)
    httpd = dashboard.make_server(tmp_path / "s.json", port=0, klic_soubor=tmp_path / "k.txt",
                                  sit_zapnout=False, sledovane=tmp_path / "s.txt")
    vlakno = threading.Thread(target=httpd.serve_forever, daemon=True)
    vlakno.start()
    try:
        kod, _, _ = _get(f"http://127.0.0.1:{httpd.server_address[1]}/?klic=cokoli")
        assert kod == 401, "bez založeného klíče se nesmí projít s jakýmkoli"
    finally:
        httpd.shutdown()
        httpd.server_close()
