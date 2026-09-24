#!/usr/bin/env bash
#
# Nahraje program na QNAP do sdílené složky, ze které ho spouští kontejner.
#
#     ./scripts/nahraj-na-nas.sh                         # /Volumes/Container/trader
#     CIL=/Volumes/Jina/trader ./scripts/nahraj-na-nas.sh
#
# Předtím připojte sdílenou složku NASu ve Finderu (⌘K, smb://<IP-NASu>/Container),
# přes VPN nebo doma. Kopíruje kód; data jen poprvé (viz níž).
#
# DATA: při prvním nahrání se zkopíruje i vaše kniha, seznam sledovaných
# a převodní tabulka ISIN. Pak už je hlavní kopie na NASu — skript ji
# nepřepíše, aby starší stav z Macu nepřepsal novější zápisy z iPhonu.

set -euo pipefail
cd "$(dirname "$0")/.."
CIL="${CIL:-/Volumes/Container/trader}"

if [ ! -d "$(dirname "$CIL")" ]; then
    echo "Sdílená složka $(dirname "$CIL") není připojená." >&2
    echo "Ve Finderu: ⌘K → smb://<IP-NASu>/Container" >&2
    exit 1
fi
mkdir -p "$CIL/data"

rsync -rt --delete \
    --exclude .venv --exclude .git --exclude __pycache__ --exclude .pytest_cache \
    --exclude .ruff_cache --exclude data/ --exclude '*.pyc' \
    ./ "$CIL/"

for soubor in portfolio.csv sledovane.txt isin_tickery.csv udalosti.csv; do
    if [ -f "data/$soubor" ] && [ ! -f "$CIL/data/$soubor" ]; then
        cp "data/$soubor" "$CIL/data/$soubor"
        echo "  data: $soubor zkopírován (první nahrání)"
    fi
done
# Kalendář událostí je součást programu, ne vaše data — ten se aktualizuje vždy.
cp data/udalosti.csv "$CIL/data/udalosti.csv"

echo
echo "Nahráno do $CIL."
echo "Na NASu restartujte kontejner trader (Container Station), ať se změny projeví."
