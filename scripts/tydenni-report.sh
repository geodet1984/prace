#!/usr/bin/env bash
#
# Týdenní report paper účtu pro malý kapitál (~1000 USD / 20 000 Kč).
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
# Tituly: čtrnáct ETF napříč třídami aktiv, ne čtrnáct amerických akcií. Diverzifikace
#   funguje jen mezi věcmi, které spolu nekorelují. SPY a QQQ mají korelaci
#   kolem 0,9 — to je jedna sázka dvakrát. Dluhopisy, zlato a komodity se
#   v krizi chovají jinak než akcie, a právě to dělá ten √N efekt.
#   POZOR: US ETF v EU jako retail nekoupíte (PRIIPs). Pro demo to nevadí,
#   pro ostrý účet viz tabulku UCITS alternativ v UNIVERZUM.md.
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
# Zlomkové akcie jsou nutnost: bez nich se za 200 USD nedá koupit ani jedna
#   akcie SPY a celá diverzifikace padá.
#
# Kill-switch na 35 %, ne na 25 %. V paper fázi je smyslem se učit, ne chránit
#   kapitál — a přísný kill-switch systém utne dřív, než stihne ukázat, jak se
#   chová v propadu. Na ostrý účet ho stáhněte zpátky.
# ────────────────────────────────────────────────────────────────────────

set -euo pipefail

cd "$(dirname "$0")/.."

# Čtrnáct titulů napříč třídami aktiv. Podrobnosti a UCITS alternativy pro
# ostrý účet v EU jsou v UNIVERZUM.md. Po každé změně ověřte přes
# `python -m trading ensemble`, jestli přibyly sázky, nebo jen kopie.
SYMBOLS="${SYMBOLS:-SPY,IWM,EFA,EEM,TLT,IEF,LQD,HYG,TIP,GLD,SLV,DBC,VNQ,UUP}"

CAPITAL="${CAPITAL:-1000}"
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
    --max-position 0.12 \
    --max-positions 10 \
    --max-drawdown 0.35 \
    --reentry-cooldown 10

echo
