#!/usr/bin/env bash
#
# Denní hlídač portfolia — pošle oznámení na macOS, jen když je co říct.
#
#     ./scripts/hlidac.sh
#
# Určeno ke spouštění na pozadí (launchd, viz níž). Když není co hlásit,
# neudělá nic a nic nevypíše — hlídač, který se ozývá denně, se po týdnu
# přestane číst.
#
# Nic neobchoduje a nic nedoporučuje. Říká, co se stalo.
#
# Proměnné: KNIHA, STAV, VENV

set -uo pipefail
cd "$(dirname "$0")/.."

KNIHA="${KNIHA:-data/portfolio.csv}"
STAV="${STAV:-data/hlidac_stav.json}"
VENV="${VENV:-.venv}"
LOG="${LOG:-data/hlidac.log}"

if [ ! -d "$VENV" ]; then
    echo "Virtuální prostředí $VENV chybí — spusťte nejdřív ./scripts/start-demo.sh" >&2
    exit 1
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

if [ ! -f "$KNIHA" ]; then
    echo "Kniha $KNIHA neexistuje. Založte ji přes 'python -m trading zapis' nebo 'import'." >&2
    exit 1
fi

# Nejdřív celý výpis do logu — když se pak na oznámení kliknete až za dva
# dny, chcete si přečíst, co přesně to bylo.
VYPIS="$(python3 -m trading hlidac --kniha "$KNIHA" --stav "$STAV" --nezapisovat 2>&1)"
KOD=$?

{
    echo "── $(date '+%Y-%m-%d %H:%M:%S') ──────────────────────────────"
    echo "$VYPIS"
} >> "$LOG"

if [ "$KOD" -ne 10 ]; then
    exit 0
fi

# Teprve teď se běh zapamatuje. Kdyby se stav uložil dřív a oznámení pak
# selhalo, zpráva by se ztratila a podruhé už by se neposlala.
SHRNUTI="$(python3 -m trading hlidac --kniha "$KNIHA" --stav "$STAV" --strucne 2>/dev/null)"

# osascript je součást systému, takže žádná závislost navíc. Uvozovky
# v textu by hlášku rozbily, proto se zdvojují.
BEZPECNE="${SHRNUTI//\"/\\\"}"
osascript -e "display notification \"${BEZPECNE}\" with title \"Portfolio\" sound name \"Submarine\"" \
    2>/dev/null || echo "$SHRNUTI"

echo "$VYPIS"
