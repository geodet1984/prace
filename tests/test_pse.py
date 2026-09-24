"""Kurzovní lístek Burzy cenných papírů Praha.

Struktura podle Popis_KL.pdf (soubor AK). Hlavní past jsou dny bez
obchodu: lístek uvede starý kurz a kdo to nepozná, vydává ho za dnešní.
"""

from datetime import date

from trading import pse

AK = (
    '"CZ0005112300","ČEZ               ","BAACEZ  ","2026/09/23",1350.00        ,-.95     ,'
    '1363.00        ,1134.00        ,1400.00        ,62462        ,84300000.00        ,'
    '"2026/09/23","T","2","F",1340.00        ,1365.00        ,1360.00        ,1      \n'
    '"CZ0009000121","KOFOLA ČS         ","BABKOFOL","2026/09/23",511.00         ,.00      ,'
    '511.00         ,450.00         ,520.00         ,0            ,.00                ,'
    '"2026/09/18","T","2","F",.00            ,.00            ,.00            ,1      \n'
    '"CZ0003561441","ACCOL.FC1 8,00/29 ","BDAACCFI","2026/09/23",102.00         ,.00      ,'
    '102.00         ,101.00         ,105.99         ,0            ,.00                ,'
    '"2026/09/22","T","2","F",.00            ,.00            ,.00            ,1      \n'
)


def test_sloupce_podle_popisu_burzy():
    k = pse.precti_ak(AK)["CZ0005112300"]
    assert k.nazev == "ČEZ" and k.zaverecny == 1350.0 and k.zmena_pct == -0.95
    assert (k.rocni_min, k.rocni_max) == (1134.0, 1400.0)
    assert k.kusu == 62462 and k.objem == 84_300_000.0
    assert k.den == date(2026, 9, 23) and k.obchodovano_dnes


def test_den_bez_obchodu_se_prizna():
    """Kofola se 23. 9. neobchodovala; lístek uvádí kurz z 18. 9. Vydávat
    ho za dnešní by byla lež, kterou z čísla nepoznáte."""
    k = pse.precti_ak(AK)["CZ0009000121"]
    assert not k.obchodovano_dnes
    assert "neobchodovalo" in pse.veta(k) and "18. 9. 2026" in pse.veta(k)


def test_dluhopisy_nejsou_akcie():
    assert not pse.precti_ak(AK)["CZ0003561441"].je_akcie


def test_ticker_se_hleda_jen_mezi_akciemi():
    """Převod ISIN → ticker jde přes síť; u dluhopisů je zbytečný."""
    listek = pse.precti_ak(AK)
    volane = []

    def prevod(isin):
        volane.append(isin)
        return {"CZ0005112300": "CEZ.PR"}.get(isin)

    assert pse.pro_ticker("cez.pr", listek, prevod).isin == "CZ0005112300"
    assert "CZ0003561441" not in volane


def test_mimo_prahu_se_lístek_nepouziva():
    assert pse.pro_ticker("CSPX.AS", pse.precti_ak(AK), lambda i: "CSPX.AS") is None


def test_ceske_formatovani():
    veta = pse.veta(pse.precti_ak(AK)["CZ0005112300"])
    assert "1 350,00 Kč" in veta and "-0,95 %" in veta and "62 462 ks" in veta


def test_den_s_nulovym_objemem_se_opravi_oficialnim_kurzem(monkeypatch):
    """Yahoo u Colt CZ dva měsíce opakovalo 1 020 Kč s nulovým objemem,
    přestože se obchodovalo za 906–996 Kč. Takový den se musí nahradit
    kurzem z burzy; skutečně neobchodovaný den zůstat, jak je."""
    import pandas as pd

    dny = pd.bdate_range("2026-06-15", periods=3)
    df = pd.DataFrame({"close": [1020.0, 1020.0, 1020.0], "volume": [0, 0, 500]}, index=dny)

    def kurz(den, cena, obchod):
        return pse.Kurz("CZ0009008942", "COLTCZ", "BAACZGCE", den, cena, 0, 0, 0, 0,
                        100, 1.0, obchod)

    listky = {
        dny[0].date(): {"CZ0009008942": kurz(dny[0].date(), 996.0, dny[0].date())},
        # druhý den se opravdu neobchodovalo — lístek nese starší obchod
        dny[1].date(): {"CZ0009008942": kurz(dny[1].date(), 996.0, dny[0].date())},
    }
    monkeypatch.setattr(pse, "pro_ticker",
                        lambda t, listek, p=None: next(iter(listek.values()), None))
    vychozi = listky[dny[0].date()]
    opraveno = pse.oprav_radu("COLT.PR", df, listek_k=lambda d: listky.get(d, vychozi))

    assert opraveno.loc[dny[0], "close"] == 996.0 and opraveno.loc[dny[0], "volume"] == 100
    assert opraveno.loc[dny[1], "close"] == 1020.0, "neobchodovaný den se nemění"
    assert opraveno.loc[dny[2], "close"] == 1020.0, "den s objemem je v pořádku"
    assert df.loc[dny[0], "close"] == 1020.0, "původní data se nepřepisují"
