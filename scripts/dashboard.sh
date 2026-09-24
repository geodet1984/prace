#!/usr/bin/env bash
#
# Rozjede přehled paper účtu v prohlížeči a rovnou ho otevře.
#
#     ./scripts/dashboard.sh
#
# Přehled je **jen ke čtení**. Nespouští engine, nic neukládá a nemá jak
# cokoli obchodovat — účet posouvá výhradně `trading paper`, tedy skripty
# tydenni-report.sh / denni-report.sh.
#
# Server poslouchá jen na loopbacku (127.0.0.1). Stav účtu je soukromá věc
# a do sítě ho vystavovat není důvod.
#
# Proměnné: PORT, STATE, VENV, BROWSER
#
#     BROWSER=Firefox ./scripts/dashboard.sh    # když Safari blokuje HTTP

set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-8765}"
STATE="${STATE:-data/paper_state.json}"
# Skutečné portfolio. Nemusí existovat — založí ho první zápis z formuláře.
KNIHA="${KNIHA:-data/portfolio.csv}"
VENV="${VENV:-.venv}"
BROWSER="${BROWSER:-}"

if [ -d "$VENV" ]; then
    # shellcheck disable=SC1091
    source "$VENV/bin/activate"
else
    echo "Virtuální prostředí $VENV chybí — spusťte nejdřív ./scripts/start-demo.sh" >&2
    exit 1
fi

# Do prohlížeče jde "localhost", ne "127.0.0.1": Safari s vynuceným HTTPS
# (mj. anonymní okna) číselnou loopback adresu po HTTP zablokuje, zatímco
# localhost má výjimku. Server sám dál poslouchá na 127.0.0.1.
URL="http://localhost:${PORT}"
CHECK="http://127.0.0.1:${PORT}"

# Běží-li přehled už z minula, druhý server se na port nenaváže a Python
# skončí tracebackem, ze kterého není poznat, že vlastně všechno funguje.
# Otevřeme tedy ten běžící a končíme.
if curl -sf -o /dev/null "$CHECK/api/verze" 2>/dev/null; then
    echo "Přehled už běží na $URL — otevírám ho."
    otevri() { [ -n "$BROWSER" ] && open -a "$BROWSER" "$URL" || open "$URL"; }
    [ "$(uname -s)" = "Darwin" ] && otevri
    exit 0
fi

# Prohlížeč otevřeme až ve chvíli, kdy server odpovídá. Otevřít ho dřív
# znamená prázdnou stránku a ruční F5.
(
    for _ in $(seq 1 40); do
        if curl -sf -o /dev/null "$CHECK/api/verze" 2>/dev/null; then
            case "$(uname -s)" in
                # BROWSER přebije výchozí prohlížeč. Safari s vynuceným HTTPS
                # odmítne i http://localhost, a přepnout celý systém kvůli
                # jedné stránce nedává smysl.
                Darwin) [ -n "$BROWSER" ] && open -a "$BROWSER" "$URL" || open "$URL" ;;
                Linux)  command -v xdg-open >/dev/null && xdg-open "$URL" >/dev/null 2>&1 ;;
            esac
            exit 0
        fi
        sleep 0.25
    done
    echo "Server nenaběhl do 10 s — otevřete $URL ručně." >&2
) &

exec python3 -m trading dashboard --state "$STATE" --port "$PORT" --kniha "$KNIHA"
