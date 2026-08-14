"""Smoke testy CLI — běží na syntetických datech, takže nepotřebují síť."""

import json

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
