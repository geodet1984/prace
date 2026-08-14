#!/usr/bin/env bash
#
# Rozjede demo paper účet na jedno spuštění.
#
#     ./scripts/start-demo.sh
#
# Co udělá:
#   1. ověří Pythona a doinstaluje závislosti
#   2. stáhne historii sedmi ETF (jednou, pak se cachuje)
#   3. ukáže, jaké signály platí dnes — nic neobchoduje
#   4. založí paper účet a přehraje na něm celou historii
#   5. vypíše řádek do crontabu
#
# Peníze jsou fiktivní. Nikam se nic neposílá — projekt nemá napojení na
# brokera zapnuté a `trading paper` počítá výhradně lokálně.

set -euo pipefail
cd "$(dirname "$0")/.."

CAPITAL="${CAPITAL:-1000}"
SYMBOLS="${SYMBOLS:-SPY,EFA,EEM,TLT,GLD,DBC,VNQ}"
START="${START:-2010-01-01}"
STATE="${STATE:-data/paper_state.json}"

step() { printf '\n\033[1m── %s\033[0m\n' "$1"; }

step "1/5  Prostředí"
python3 --version
python3 -m pip install --quiet -r requirements.txt
echo "závislosti v pořádku"

step "2/5  Stažení historie"
if ! python3 -m trading fetch --symbols "$SYMBOLS" --start "$START"; then
    cat <<'EOF'

Stažení selhalo. Nejčastější příčiny:
  • nefunkční připojení nebo firewall blokující Yahoo Finance
  • dočasný výpadek na straně burzovního API — zkuste za chvíli znovu

Náhradní cesta: stáhněte si CSV odkudkoli (Yahoo, Stooq, váš broker),
uložte je jako data/csv/SPY.csv atd. se sloupci
date,open,high,low,close,volume a pak všude přidejte --csv-dir data/csv
EOF
    exit 1
fi

step "3/5  Dnešní signály (jen náhled, nic se neobchoduje)"
python3 -m trading signals --symbols "$SYMBOLS" --start "$START" --strategy tsmom

step "4/5  Založení paper účtu a přehrání historie"
if [ -f "$STATE" ]; then
    echo "Stav $STATE už existuje — pokračuji v něm."
    echo "Chcete-li začít znovu od nuly, smažte ho."
fi
./scripts/tydenni-report.sh

step "5/5  Automatické spouštění"
cat <<EOF
Přidejte si do crontabu (pátek po zavření US burzy):

    crontab -e
    23 23 * * 5  $(pwd)/scripts/tydenni-report.sh >> \$HOME/paper.log 2>&1

Report pak najdete v ~/paper.log. Frekvence spouštění výsledek nemění —
engine přehrává bar po baru, takže na tom, kdy skript pustíte, nezáleží.

Za měsíc se vraťte s obsahem ~/paper.log. Očekávejte, že čísla nebudou
o ničem vypovídat: při kapitálu $CAPITAL USD je měsíční očekávaný zisk
kolem 8 USD proti běžnému rozptylu ±68 USD. Smyslem prvního měsíce je
ověřit, že celý řetěz funguje — ne zjistit, jestli strategie vydělává.
EOF
