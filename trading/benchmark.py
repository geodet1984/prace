"""Referenční srovnání: koupit na začátku a nedělat nic.

Tohle je laťka, kterou musí každá strategie přeskočit. Aktivní systém,
který po započtení poplatků nepřekoná pasivní držení indexu, stojí jen
čas a nervy navíc.

Benchmark schválně neprochází risk managerem — stop-loss by ho z trhu
vyhodil a měřili bychom pak něco jiného než "držení".
"""

from __future__ import annotations

import pandas as pd

from .execution import CostModel
from .models import BacktestResult, EquityPoint, Side, Trade


def buy_and_hold(
    data: dict[str, pd.DataFrame],
    initial_capital: float = 100_000.0,
    costs: CostModel | None = None,
    allow_fractional: bool = True,
) -> tuple[BacktestResult, float]:
    """Rovnoměrně rozdělí kapitál mezi tituly, nakoupí a drží do konce.

    Vrací (výsledek, celkové poplatky).
    """
    costs = costs or CostModel()
    symbols = sorted(s for s, df in data.items() if not df.empty)
    if not symbols:
        raise ValueError("žádná data pro benchmark")

    timeline = sorted({ts for s in symbols for ts in data[s].index})
    allocation = initial_capital / len(symbols)

    cash = initial_capital
    total_fees = 0.0
    holdings: dict[str, tuple[float, float, pd.Timestamp]] = {}

    # Nákup na otevírací ceně prvního baru, kde má titul data.
    for symbol in symbols:
        df = data[symbol]
        first_ts = df.index[0]
        price = costs.fill_price(Side.BUY, float(df["open"].iloc[0]))
        if price <= 0:
            continue
        quantity = allocation / price
        if not allow_fractional:
            quantity = float(int(quantity))
        if quantity <= 0:
            continue
        fees = costs.commission(quantity, price)
        cash -= quantity * price + fees
        total_fees += fees
        holdings[symbol] = (quantity, price, first_ts)

    last_price = {s: float(data[s]["close"].iloc[0]) for s in holdings}
    equity_curve: list[EquityPoint] = []

    for timestamp in timeline:
        for symbol in holdings:
            df = data[symbol]
            if timestamp in df.index:
                last_price[symbol] = float(df.at[timestamp, "close"])
        positions_value = sum(q * last_price[s] for s, (q, _, _) in holdings.items())
        equity_curve.append(EquityPoint(timestamp, cash, positions_value))

    # Prodej na posledním baru, aby výsledek zahrnul i výstupní náklady.
    trades: list[Trade] = []
    final_ts = timeline[-1]
    for symbol, (quantity, entry_price, entry_ts) in holdings.items():
        exit_price = costs.fill_price(Side.SELL, last_price[symbol])
        fees = costs.commission(quantity, exit_price)
        total_fees += fees
        entry_fees = costs.commission(quantity, entry_price)
        trades.append(
            Trade(
                symbol=symbol,
                quantity=quantity,
                entry_time=entry_ts,
                entry_price=entry_price,
                exit_time=final_ts,
                exit_price=exit_price,
                fees=entry_fees + fees,
                exit_reason="konec testovaného období",
            )
        )
        cash += quantity * exit_price - fees

    equity_curve.append(EquityPoint(final_ts, cash, 0.0))

    result = BacktestResult(
        strategy="buy & hold (benchmark)",
        symbols=symbols,
        initial_capital=initial_capital,
        trades=trades,
        equity_curve=equity_curve,
    )
    return result, total_fees
