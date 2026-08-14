"""Model transakčních nákladů a provádění příkazů.

Backtest bez poplatků a slippage je marketingový materiál, ne test.
Strategie s krátkou držbou a stovkami obchodů ročně na nákladech
běžně ztrácí několik procent kapitálu za rok — dost na to, aby se
"ziskový" systém překlopil do ztráty.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Side


@dataclass(frozen=True)
class CostModel:
    """Poplatky a skluz.

    Výchozí hodnoty odpovídají zhruba běžnému retailovému brokerovi
    s nulovou provizí (Alpaca, XTB) na likvidních US akciích: provize 0,
    ale skluz reálně existuje vždy.
    """

    commission_pct: float = 0.0
    """Provize jako podíl objemu obchodu (0.001 = 0,1 %)."""

    commission_per_share: float = 0.0
    """Provize za akcii (model Interactive Brokers)."""

    min_commission: float = 0.0
    """Minimální provize za příkaz."""

    slippage_bps: float = 5.0
    """Skluz v bazických bodech (5 bps = 0,05 %). Nákup dráž, prodej levněji."""

    def commission(self, quantity: float, price: float) -> float:
        """Provize za jeden příkaz."""
        fee = quantity * price * self.commission_pct + quantity * self.commission_per_share
        return max(fee, self.min_commission) if quantity > 0 else 0.0

    def fill_price(self, side: Side, price: float) -> float:
        """Realizační cena po započtení skluzu — vždy v náš neprospěch."""
        adjustment = price * self.slippage_bps / 10_000.0
        return price + adjustment if side is Side.BUY else price - adjustment


ZERO_COST = CostModel(slippage_bps=0.0)
"""Bezztrátové provádění — jen pro jednotkové testy, nikdy pro rozhodování."""


IBKR_LIKE = CostModel(commission_per_share=0.005, min_commission=1.0, slippage_bps=3.0)
"""Přibližný model Interactive Brokers pro US akcie."""


RETAIL_EU = CostModel(commission_pct=0.0015, min_commission=2.0, slippage_bps=10.0)
"""Přibližný model evropského retailového brokera."""


BINANCE_SPOT = CostModel(commission_pct=0.001, slippage_bps=10.0)
"""Binance spot, základní tarif: 0,10 % taker. Se slevou za BNB méně."""


KRAKEN_PRO = CostModel(commission_pct=0.0026, slippage_bps=10.0)
"""Kraken, základní tarif: 0,26 % taker. Kraken Pro stlačí na 0,10 %."""


COINBASE_ADVANCED = CostModel(commission_pct=0.006, slippage_bps=15.0)
"""Coinbase Advanced, nejnižší objemové pásmo: 0,60 % taker.

Tenhle preset je tu hlavně jako varování. Při 100 obratech ročně sežere
18 % kapitálu — víc, než kolik má strategie šanci vydělat.
"""


COST_PRESETS = {
    "zero": ZERO_COST,
    "default": CostModel(),
    "ibkr": IBKR_LIKE,
    "retail_eu": RETAIL_EU,
    "binance": BINANCE_SPOT,
    "kraken": KRAKEN_PRO,
    "coinbase": COINBASE_ADVANCED,
}
