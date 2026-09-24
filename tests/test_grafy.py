"""Data pro grafy.

Graf hodnoty portfolia je nejsnazší místo, kde se vloudí pohled do
budoucnosti: dnešní cena nebo kurz na starý týden dá hladkou křivku,
která nikdy neexistovala.
"""

from datetime import date

import pandas as pd
import pytest

from trading.fx import KurzyPodleDne
from trading.grafy import MAX_BODU, cenova_rada, portfolio_v_case
from trading.ledger import Ledger, Transaction, TxType


def ceny(hodnoty, od="2024-01-01"):
    return pd.DataFrame({"close": hodnoty}, index=pd.bdate_range(od, periods=len(hodnoty)))


def test_rada_je_zredena_ale_konci_poslednim_barem():
    df = ceny(list(range(1, 3000)))
    r = cenova_rada(df, let=20)
    assert len(r["ceny"]) <= MAX_BODU + 1
    assert r["ceny"][-1] == 2999.0, "graf nesmí skončit den před posledním barem"


def test_portfolio_oceni_kazdy_tyden_tehdejsi_cenou():
    """Titul koupený za 100, který později roste na 200: první týden musí
    ukázat 100, ne dnešních 200."""
    kniha = Ledger()
    kniha.add(Transaction(day=date(2024, 1, 1), type=TxType.DEPOSIT, amount=1000, currency="CZK"))
    kniha.add(Transaction(day=date(2024, 1, 1), type=TxType.BUY, symbol="X", quantity=10,
                          price=100, amount=-1000, currency="CZK"))
    historie = {"X": ceny([100.0] * 5 + [200.0] * 40)}
    body = portfolio_v_case(kniha, historie, KurzyPodleDne({}), k_datu=date(2024, 2, 20))

    assert body[0]["hodnota"] == pytest.approx(1000.0)
    assert body[-1]["hodnota"] == pytest.approx(2000.0)
    assert all(b["vlozeno"] == pytest.approx(1000.0) for b in body)


def test_nevyinvestovana_hotovost_neni_ztrata():
    """Vklad, který ještě neleží v titulu, patří do majetku — jinak by
    graf ukázal propad jen proto, že peníze čekají na účtu."""
    kniha = Ledger()
    kniha.add(Transaction(day=date(2024, 1, 1), type=TxType.DEPOSIT, amount=5000, currency="CZK"))
    body = portfolio_v_case(kniha, {}, KurzyPodleDne({}), k_datu=date(2024, 1, 20))
    assert all(b["hodnota"] == pytest.approx(5000.0) for b in body)


def test_prazdna_kniha_nema_graf():
    assert portfolio_v_case(Ledger(), {}, KurzyPodleDne({})) == []
