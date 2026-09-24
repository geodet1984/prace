"""Smoke testy CLI — běží na syntetických datech, takže nepotřebují síť."""

import json
import re
from datetime import date

import pytest

from trading.cli import _coerce, _parse_params, main


def run(argv, capsys):
    code = main(argv)
    return code, capsys.readouterr().out


def test_backtest_prints_report(capsys):
    code, out = run(["backtest", "--symbols", "X", "--synthetic"], capsys)
    assert code == 0
    assert "Sharpe ratio" in out
    assert "Max. drawdown" in out


def test_backtest_json_output_is_parseable(capsys):
    code, out = run(["backtest", "--symbols", "X", "--synthetic", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert "sharpe" in payload and "max_drawdown_pct" in payload


def test_backtest_with_trade_list(capsys):
    _, out = run(["backtest", "--symbols", "X", "--synthetic", "--trades"], capsys)
    assert "Poslední obchody" in out or "Žádné uzavřené obchody" in out


def test_compare_lists_every_strategy_and_benchmark(capsys):
    from trading import strategies

    code, out = run(["compare", "--symbols", "X", "--synthetic"], capsys)
    assert code == 0
    for name in strategies.REGISTRY:
        assert name in out
    assert "benchmark" in out


def test_signals_command(capsys):
    code, out = run(["signals", "--symbols", "X,Y", "--synthetic"], capsys)
    assert code == 0
    assert "Signály" in out
    assert "X" in out and "Y" in out


def test_paper_creates_and_reuses_state(tmp_path, capsys):
    state = str(tmp_path / "state.json")
    args = ["paper", "--symbols", "X", "--synthetic", "--state", state]

    code, out = run(args, capsys)
    assert code == 0
    assert "PAPER ÚČET" in out

    code, out = run(args, capsys)
    assert code == 0
    assert "Žádné nové příkazy" in out


def test_paper_dry_run_writes_no_state(tmp_path, capsys):
    state = tmp_path / "state.json"
    run(["paper", "--symbols", "X", "--synthetic", "--state", str(state), "--dry-run"], capsys)
    assert not state.exists()


def test_equity_export(tmp_path, capsys):
    out_file = tmp_path / "equity.csv"
    run(["backtest", "--symbols", "X", "--synthetic", "--equity-csv", str(out_file)], capsys)
    assert out_file.exists()
    assert "equity" in out_file.read_text(encoding="utf-8").splitlines()[0]


def test_strategy_params_reach_the_strategy(capsys):
    _, out = run(
        ["backtest", "--symbols", "X", "--synthetic", "--param", "fast=7", "--param", "slow=21"],
        capsys,
    )
    assert "fast=7" in out and "slow=21" in out


def test_risk_flags_change_the_outcome(capsys):
    _, tight = run(
        ["backtest", "--symbols", "X", "--synthetic", "--risk-per-trade", "0.001", "--json"], capsys
    )
    _, loose = run(
        ["backtest", "--symbols", "X", "--synthetic", "--risk-per-trade", "0.02", "--json"], capsys
    )
    assert json.loads(tight)["final_equity"] != json.loads(loose)["final_equity"]


def test_missing_csv_returns_error_code(tmp_path, capsys):
    code, _ = run(["backtest", "--symbols", "NOPE", "--csv-dir", str(tmp_path)], capsys)
    assert code == 1


def test_unknown_strategy_is_rejected():
    with pytest.raises(SystemExit):
        main(["backtest", "--strategy", "neexistuje", "--synthetic"])


# --- portfolio a korunový přepočet --------------------------------------


KNIHA = """day,type,symbol,quantity,price,amount,fee,currency,note
2024-01-10,vklad,,,,50000.00,,CZK,
2024-01-10,nakup,X,10,100,-1000.00,,USD,
"""


@pytest.fixture
def kniha_csv(tmp_path):
    cesta = tmp_path / "portfolio.csv"
    cesta.write_text(KNIHA, encoding="utf-8")
    return str(cesta)


@pytest.fixture
def bez_site(monkeypatch):
    """Utne CLI od sítě: pevná cena titulu a pevné kurzy k datům.

    Kurz 25 → 22,5 Kč za dolar je záměrně posílení koruny o 10 %, zatímco
    titul roste o 10 %. Součin 1,1 × 0,9 není 1, takže výsledek musí být
    lehce záporný — a hlavně musí jít rozložit na dvě velké části proti
    sobě, ne na jedno malé číslo.
    """
    import pandas as pd

    from trading import cli as cli_module
    from trading.fx import KurzyPodleDne

    def fake_fetch(symbol, *a, **kw):
        return pd.DataFrame({"close": [110.0]})

    kurzy = KurzyPodleDne(
        {("USD", date(2024, 1, 10)): 25.0, ("USD", date(2025, 1, 10)): 22.5}
    )
    monkeypatch.setattr(cli_module.data_module, "fetch", fake_fetch)
    monkeypatch.setattr(cli_module.fx_module, "Kurzovnik", lambda *a, **kw: kurzy)
    return kurzy


def _cislo(out, popisek):
    """Vytáhne číslo z řádku výpisu — výpis je jediné, co uživatel vidí."""
    for radek in out.splitlines():
        if popisek in radek:
            m = re.search(r"(-?[\d\s,]+\.\d{2})", radek.replace(" ", " "))
            if m:
                return float(m.group(1).replace(" ", "").replace(",", ""))
    raise AssertionError(f"řádek {popisek!r} ve výpisu není:\n{out}")


def test_czk_rozpad_se_scita_na_zisk_z_pozic(kniha_csv, bez_site, capsys, monkeypatch):
    """Výkon aktiv a pohyb kurzu se sčítají na zisk z POZIC, ne na "celkem".

    Přičíst je k rozpadu podle druhu (dividendy, daně, poplatky) by tytéž
    peníze započetlo dvakrát — jsou to dva pohledy na stejné zhodnocení,
    jednou podle druhu a jednou podle příčiny.
    """
    code, out = run(["portfolio", "--kniha", kniha_csv, "--czk"], capsys)
    assert code == 0

    vykon = _cislo(out, "Výkon aktiv")
    kurz = _cislo(out, "Pohyb kurzu")
    nerealizovany = _cislo(out, "Nerealizovaný zisk")
    realizovany = _cislo(out, "Realizovaný zisk")

    assert vykon + kurz == pytest.approx(nerealizovany + realizovany, abs=0.01)
    # Dvě velké částky proti sobě, ne jedna malá: aktivum +2500, kurz -2750.
    assert vykon > 2000 and kurz < -2000
    assert vykon + kurz < 0


def test_czk_neprictene_k_celkem(kniha_csv, bez_site, capsys):
    """Kdyby se rozpad podle příčiny přičetl k "celkem", součet by se
    zdvojil — tenhle test to chytí na konkrétním čísle."""
    _, out = run(["portfolio", "--kniha", kniha_csv, "--czk"], capsys)
    assert _cislo(out, "Celkem") == pytest.approx(
        _cislo(out, "Nerealizovaný zisk") + _cislo(out, "Realizovaný zisk"), abs=0.01
    )


def test_smisena_kniha_bez_czk_varuje(kniha_csv, bez_site, capsys):
    """Bez přepočtu se sčítají dolary s korunami. Z čísla to poznat nejde,
    takže to musí být vidět jinak — jinak uživatel uvěří nesmyslu."""
    code = main(["portfolio", "--kniha", kniha_csv])
    zachyceno = capsys.readouterr()
    assert code == 0
    assert "míchá měny" in zachyceno.err
    assert "--czk" in zachyceno.err


def test_czk_funguje_i_kdyz_cena_neni(kniha_csv, capsys, monkeypatch):
    """Bez sítě se přehled nesmí složit — ocení se pořizovací cenou a řekne to."""
    from trading import cli as cli_module
    from trading.data import DataError
    from trading.fx import KurzyPodleDne

    def padne(symbol, *a, **kw):
        raise DataError(f"{symbol}: síť není")

    monkeypatch.setattr(cli_module.data_module, "fetch", padne)
    monkeypatch.setattr(
        cli_module.fx_module,
        "Kurzovnik",
        lambda *a, **kw: KurzyPodleDne({("USD", date(2024, 1, 10)): 25.0}),
    )

    code, out = run(["portfolio", "--kniha", kniha_csv, "--czk"], capsys)
    assert code == 0
    assert "Bez aktuální ceny" in out


# --- pomocné funkce -----------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("10", 10),
        ("1.5", 1.5),
        ("true", True),
        ("False", False),
        ("none", None),
        ("abc", "abc"),
    ],
)
def test_coerce_types(text, expected):
    assert _coerce(text) == expected


def test_parse_params():
    assert _parse_params(["fast=10", "slow=30"]) == {"fast": 10, "slow": 30}


def test_malformed_param_is_rejected():
    with pytest.raises(SystemExit):
        _parse_params(["fast"])
