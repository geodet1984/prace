#!/usr/bin/env bash
#
# Denní posun paper účtu a výpis stavu. Určeno pro cron.
#
# Proč denně a ne hodinově: strategie v tomhle projektu pracují s denními
# bary. Dokud burza nezavře, žádný nový bar neexistuje a hodinový běh nemá
# co zpracovat — vypsal by přesně totéž co před hodinou. Hodinové hlášení má
# smysl jen s hodinovými daty (--interval 1h), a i tam je poměr šumu k signálu
# zhruba 60:1.
#
# Instalace (spouští se po zavření US burzy, 22:00 SEČ / 23:00 SELČ):
#
#     chmod +x scripts/denni-report.sh
#     crontab -e
#     7 23 * * 1-5  /cesta/k/projektu/scripts/denni-report.sh >> /cesta/paper.log 2>&1
#
# Minuta 7 místo 0 schválně — v 0 minut startuje kdekdo a burzovní API
# to poznají.

set -euo pipefail

cd "$(dirname "$0")/.."

SYMBOLS="${SYMBOLS:-SPY,QQQ,IWM}"
CAPITAL="${CAPITAL:-500}"
STRATEGY="${STRATEGY:-tsmom}"
START="${START:-2015-01-01}"
STATE="${STATE:-data/paper_state.json}"

echo "════════════════════════════════════════════════════════════"
echo "  $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "════════════════════════════════════════════════════════════"

python3 -m trading paper \
    --symbols "$SYMBOLS" \
    --start "$START" \
    --capital "$CAPITAL" \
    --strategy "$STRATEGY" \
    --costs default \
    --state "$STATE" \
    --fractional \
    --risk-per-trade 0.01 \
    --max-positions 3

echo
