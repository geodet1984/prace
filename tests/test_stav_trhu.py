"""Popis stavu trhu a historické srovnání.

Modul, který ukazuje „co následovalo v podobných situacích", má tři
snadné způsoby, jak lhát: podívat se do budoucnosti, počítat jednu
dlouhou epizodu jako mnoho případů, a ukázat četnost bez srovnání
s tím, co titul dělá běžně. Testy míří na všechny tři.
"""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from trading import data as data_module
from trading.calendar import MarketEvent
from trading.stav_trhu import MIN_PRIPADU, casova_osa, popis, priznaky, rozbor, srovnani


def rada(n=1500, seed=1, drift=0.0003):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(drift, 0.012, n)))
    idx = pd.bdate_range("2015-01-01", periods=n)
    return pd.DataFrame({"close": c, "volume": rng.integers(1e5, 2e5, n)}, index=idx)


def test_popis_nezavisi_na_budoucich_barech():
    """Bod 1 z CLAUDE.md: stav k minulému dni se nesmí změnit tím, že
    přibudou další dny. Jinak by historické srovnání porovnávalo stavy,
    které tehdy nikdo vidět nemohl."""
    df = rada()
    minule = priznaky(df.iloc[:1000]).iloc[-1]
    dnes = priznaky(df).iloc[999]
    pd.testing.assert_series_equal(minule, dnes, check_names=False)


def test_srovnani_nepocita_jednu_epizodu_vicekrat():
    """Propad trvá týdny a každý jeho den by byl „případ". Vybrané případy
    musí být od sebe aspoň o horizont, jinak by jedna epizoda počítala za
    dvacet a četnost by vypadala jistě."""
    s = srovnani(rada(3000), horizont=63)
    dny = pd.to_datetime(s.data)
    mezery = [len(pd.bdate_range(a, b)) - 1 for a, b in zip(dny, dny[1:], strict=False)]
    assert all(m >= 63 for m in mezery)


def test_srovnani_vzdy_nese_bezne_chovani_titulu():
    """„Rostl v 62 % případů" nic neznamená, když titul roste v 60 % všech
    období. Bez běžného chování by každá četnost vypadala jako signál."""
    s = srovnani(rada(3000))
    assert 0 < s.bezne_podil_rustu < 1
    assert "obvykle" in s.veta() or "málo" in s.veta()


def test_na_nahodne_prochazce_neni_signal():
    """Na čistém šumu by srovnání mělo většinou hlásit „stejně jako jindy".
    Průměrováno přes seedy — z jedné řady by to byl přesně ten druh
    závěru, proti kterému je stats.py."""
    rozdily = []
    for seed in range(12):
        s = srovnani(rada(3000, seed=seed, drift=0.0))
        if s.dost_dat:
            rozdily.append(s.podil_rustu - s.bezne_podil_rustu)
    assert rozdily, "aspoň některé seedy musí mít dost případů"
    assert abs(np.mean(rozdily)) < 0.12


def test_malo_pripadu_nedava_cislo():
    s = srovnani(rada(3000))
    s.pripadu = MIN_PRIPADU - 1
    assert "málo" in s.veta()
    assert "%" not in s.veta().split("—")[1]


def test_kratka_historie_je_chyba_ne_tichy_nesmysl():
    with pytest.raises(ValueError, match="málo historie"):
        popis("X", rada(40))


def test_casova_osa_neukazuje_budouci_udalosti():
    """Událost po posledním baru se ještě nestala; ukázat k ní „reakci"
    by byl pohled do budoucnosti."""
    df = rada(300)
    konec = df.index[-1].date()
    udalosti = [MarketEvent(df.index[-5].date(), "CPI"), MarketEvent(date(2099, 1, 1), "FOMC")]
    osa = casova_osa(df, udalosti, "X")
    assert [u["udalost"] for u in osa] == ["CPI"]
    assert all(date.fromisoformat(u["den"]) <= konec for u in osa)


def test_casova_osa_respektuje_symbol():
    df = rada(300)
    den = df.index[-5].date()
    osa = casova_osa(df, [MarketEvent(den, "výsledky", "AAPL")], "MSFT")
    assert osa == []


def test_rozbor_je_serializovatelny():
    import json

    json.dumps(rozbor("X", rada(1500)))


def test_fetch_bez_zacatku_nezkrati_cache(tmp_path, monkeypatch):
    """Chyba nalezená při stavbě tohohle modulu: yfinance při zadaném konci
    a chybějícím začátku vrátí jen poslední měsíc. Ten se uložil místo
    patnáctileté historie a tiše zničil cache, ze které běží backtesty."""
    import sys
    import types

    dlouha = rada(1000).assign(open=1.0, high=1.0, low=1.0)
    data_module.save_csv(data_module._normalize(dlouha, "X"), tmp_path / "X_1d.csv")

    kratka = rada(20).assign(open=1.0, high=1.0, low=1.0)
    kratka.index = pd.bdate_range(dlouha.index[-1], periods=20)
    volano = {}

    def download(symbol, start=None, **kw):
        volano["start"] = start
        return kratka.rename(columns=str.capitalize)

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(download=download))
    df = data_module.fetch("X", end="2030-01-01", cache_dir=tmp_path, use_cache=False)

    assert volano["start"] is not None, "bez začátku yfinance vrátí jen měsíc"
    assert len(data_module.load_csv(tmp_path / "X_1d.csv", "X")) >= 1000
    assert len(df) >= 1000
