"""Pozice investorů (COT, insideři), sledované tituly a přístupy.

Hlavní past je čas: COT popisuje úterý, ale vychází v pátek, a Form 4
se počítá podle data podání. Kdo použije datum stavu, dívá se o pár dní
do budoucnosti — backtest o tom ví, člověk ne.
"""


import numpy as np
import pandas as pd
import pytest

from trading import dashboard
from trading import pozice_investoru as pi


def ceny(n=3000, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2012-01-02", periods=n)
    return pd.DataFrame({"close": 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n)))},
                        index=idx)


def cot_rada(n=600, seed=5):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2013-01-01", periods=n, freq="W-TUE") + pi.COT_ZPOZDENI
    long = 200_000 + np.cumsum(rng.normal(0, 5_000, n))
    return pd.DataFrame({"oi": 1_000_000.0, "spek_long": long, "spek_short": 200_000.0,
                         "zaj_long": 0.0, "zaj_short": 0.0}, index=idx)


def test_cot_se_pocita_od_data_zverejneni():
    """Úterní stav je veřejný až v pátek. Index musí být pátek, ne úterý."""
    assert pi.COT_ZPOZDENI.days == 3
    assert (cot_rada().index.dayofweek == 4).all()


def test_percentil_cot_nezavisi_na_budoucich_tydnech():
    """Bod 1 z CLAUDE.md: postavení k minulému týdnu se nesmí změnit tím,
    že přibudou další týdny."""
    df = cot_rada()
    minule = pi.cot_priznaky(df.iloc[:300])["percentil"].iloc[-1]
    dnes = pi.cot_priznaky(df)["percentil"].iloc[299]
    assert minule == pytest.approx(dnes)


def test_cot_rozbor_srovnava_s_beznym_chovanim():
    r = pi.cot_rozbor("CSPX.AS", ceny(), cot_rada())
    assert r.zdroj == "COT"
    assert "obvykle" in r.srovnani or "málo" in r.srovnani


def test_form4_bere_jen_obchody_na_trhu():
    """Opce (M), odměny (A) a daňové srážky (F) o názoru na cenu nic
    neříkají. Kdyby se počítaly, ředitel s akciovými odměnami by vypadal
    jako věčný kupec."""
    xml = "".join(
        f"<nonDerivativeTransaction><transactionCode>{k}</transactionCode>"
        f"<transactionShares><value>10</value></transactionShares>"
        f"<transactionPricePerShare><value>5</value></transactionPricePerShare>"
        f"</nonDerivativeTransaction>" for k in ("P", "S", "A", "M", "F"))
    assert [k for k, _, _ in pi.obchody_z_form4(xml)] == ["P", "S"]


def test_insider_nakup_se_pocita_az_den_po_podani():
    """Podání může přijít po zavření burzy — obchodovat na něm šlo nejdřív
    další den."""
    c = ceny()
    den = c.index[1000]
    obchody = pd.DataFrame({"kod": ["P"], "kusu": [1.0], "hodnota": [1.0]},
                           index=pd.DatetimeIndex([den]))
    r = pi.insideri_rozbor("X", c, obchody, k_datu=c.index[-1].date())
    assert "1×" in r.srovnani or "málo" in r.srovnani


def test_evropske_tituly_nemaji_form4():
    assert pi.je_americka_akcie("TSLA")
    assert not pi.je_americka_akcie("CSPX.AS")
    assert not pi.je_americka_akcie("SPY"), "SPY je ETF, Form 4 k němu není"


def test_bez_kontaktu_se_na_sec_nesaha(monkeypatch):
    """Adresa se nikam neposílá, dokud ji uživatel nezadá — a bez ní se
    insideři přeskočí, ne spadnou."""
    monkeypatch.setattr(pi, "sec_kontakt", lambda: None)
    monkeypatch.setattr(pi, "stahni_insidery",
                        lambda *a, **k: pytest.fail("bez kontaktu se nesmí stahovat"))
    (r,) = pi.rozbor("TSLA", ceny())
    assert "kontakt" in r["vety"][0]


# --- sledované tituly a přístupy -----------------------------------------


def test_sledovane_pridat_odebrat(tmp_path):
    soubor = tmp_path / "sledovane.txt"
    assert dashboard.uprav_sledovane(soubor, "pridat", "nvda") == ["NVDA"]
    assert dashboard.uprav_sledovane(soubor, "pridat", "^GDAXI") == ["NVDA", "^GDAXI"]
    assert dashboard.uprav_sledovane(soubor, "pridat", "NVDA") == ["NVDA", "^GDAXI"]
    assert dashboard.uprav_sledovane(soubor, "odebrat", "NVDA") == ["^GDAXI"]


@pytest.mark.parametrize("spatne", ["", "a b", "<script>", "X" * 30])
def test_sledovane_odmitne_nesmysl(tmp_path, spatne):
    """Do souboru nesmí projít nic, co by se pak vypsalo do stránky jako kód."""
    with pytest.raises(ValueError):
        dashboard.uprav_sledovane(tmp_path / "s.txt", "pridat", spatne)


def test_pristup_sec_chce_jmeno_a_email(tmp_path):
    with pytest.raises(ValueError):
        dashboard.uloz_pristup("sec", "jen-email@example.cz", tmp_path / "k.txt")
    dashboard.uloz_pristup("sec", "Jan Novák jan@example.cz", tmp_path / "k.txt")
    assert "jan@example.cz" in (tmp_path / "k.txt").read_text()
    dashboard.uloz_pristup("sec", "", tmp_path / "k.txt")
    assert not (tmp_path / "k.txt").exists(), "prázdná hodnota přístup smaže"


def test_stav_pristupu_neukazuje_celou_hodnotu(monkeypatch):
    """Stránka má vědět, že je vyplněno — ne vypsat e-mail komukoli, kdo
    se podívá na obrazovku."""
    monkeypatch.setattr(pi, "sec_kontakt", lambda: "Jan Novák jan@example.cz")
    sec = next(p for p in dashboard.stav_pristupu() if p["id"] == "sec")
    assert sec["vyplneno"] and "jan@example.cz" not in sec["nahled"]


