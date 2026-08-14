#!/usr/bin/env bash
#
# Týdenní report paper účtu pro malý kapitál (~500 USD / 10 000 Kč).
#
# Spouští se jednou týdně, ale zpracuje všechny bary, které od minule přibyly.
# Engine přehrává bar po baru, takže výsledek je **bit po bitu stejný**, jako
# kdybyste ho pouštěl každý den — ověřuje to test
# `test_result_does_not_depend_on_how_often_you_run_it`.
#
# Instalace (pátek po zavření US burzy):
#
#     chmod +x scripts/tydenni-report.sh
#     crontab -e
#     23 23 * * 5  /cesta/k/projektu/scripts/tydenni-report.sh >> ~/paper.log 2>&1
#
# ────────────────────────────────────────────────────────────────────────
# PROČ TAKHLE
#
# Tituly: sedm ETF napříč třídami aktiv, ne sedm amerických akcií. Diverzifikace
#   funguje jen mezi věcmi, které spolu nekorelují. SPY a QQQ mají korelaci
#   kolem 0,9 — to je jedna sázka dvakrát. Dluhopisy, zlato a komodity se
#   v krizi chovají jinak než akcie, a právě to dělá ten √N efekt.
#
# Strategie: tsmom (12měsíční momentum). Nejlépe doložený jednoduchý signál —
#   Moskowitz, Ooi a Pedersen ho ověřili na 58 futures napříč třídami aktiv.
#   Má jediný parametr, takže není moc kam zavrtat přeoptimalizování, a dělá
#   zhruba 20 obratů ročně, což je hluboko pod nákladovým stropem.
#
# Poplatky: preset `default` = nulová provize + skluz 5 bps. Odpovídá
#   Trading 212 nebo Alpaca. S IBKR by minimum 1 $ za příkaz sežralo
#   ~20 % kapitálu ročně — na téhle velikosti účtu se nedá použít.
#
# Zlomkové akcie jsou nutnost: bez nich se za 100 USD nedá koupit ani jedna
#   akcie SPY a celá diverzifikace padá.
# ────────────────────────────────────────────────────────────────────────

set -euo pipefail

cd "$(dirname "$0")/.."

# Sedm tříd aktiv. Nechte tak, dokud nebudete mít důvod to změnit —
# a ten důvod ověřte přes `python -m trading ensemble`, ne odhadem.
SYMBOLS="${SYMBOLS:-SPY,EFA,EEM,TLT,GLD,DBC,VNQ}"

CAPITAL="${CAPITAL:-500}"
START="${START:-2010-01-01}"     # dost historie na 252denní momentum + rozehřátí
STATE="${STATE:-data/paper_state.json}"

echo "════════════════════════════════════════════════════════════"
echo "  TÝDENNÍ REPORT — $(date '+%Y-%m-%d %H:%M %Z')"
echo "════════════════════════════════════════════════════════════"

python3 -m trading paper \
    --symbols "$SYMBOLS" \
    --start "$START" \
    --capital "$CAPITAL" \
    --strategy tsmom \
    --costs default \
    --state "$STATE" \
    --fractional \
    --risk-per-trade 0.015 \
    --stop-atr 3.0 \
    --max-position 0.20 \
    --max-positions 6 \
    --max-drawdown 0.25 \
    --reentry-cooldown 10

echo
