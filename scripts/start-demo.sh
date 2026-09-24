#!/usr/bin/env bash
#
# Rozjede demo paper účet na jedno spuštění. Funguje na macOS i Linuxu.
#
#     ./scripts/start-demo.sh
#
# Co udělá:
#   1. založí virtuální prostředí a doinstaluje závislosti
#   2. ověří, které tickery se dají stáhnout, a stáhne historii
#   3. ukáže, jaké signály platí dnes — nic neobchoduje
#   4. založí paper účet a přehraje na něm celou historii
#   5. vypíše řádek do crontabu
#
# Peníze jsou fiktivní. Nikam se nic neposílá: projekt nemá napojení na
# brokera zapnuté a `trading paper` počítá výhradně lokálně.

set -euo pipefail
cd "$(dirname "$0")/.."

CAPITAL="${CAPITAL:-1000}"
START="${START:-2010-01-01}"
STATE="${STATE:-data/paper_state.json}"
VENV="${VENV:-.venv}"

# Výchozí univerzum jsou US ETF. Pro demo je to správná volba: mají nejdelší
# a nejčistší veřejnou historii a nikde se nic doopravdy nekupuje.
# Na ostrý účet v EU je ale nepoužijete — viz UNIVERZUM.md.
SYMBOLS="${SYMBOLS:-SPY,IWM,EFA,EEM,TLT,IEF,LQD,HYG,TIP,GLD,SLV,DBC,VNQ,UUP}"

step() { printf '\n\033[1m── %s\033[0m\n' "$1"; }

step "1/5  Prostředí"
if ! command -v python3 >/dev/null; then
    echo "python3 nenalezen. Na macOS: brew install python@3.12" >&2
    exit 1
fi
python3 --version

# Homebrew i systémový Python na novějším macOS odmítají instalovat balíčky
# globálně (PEP 668). Virtuální prostředí to řeší a nic v systému nerozbije.
if [ ! -d "$VENV" ]; then
    echo "zakládám virtuální prostředí v $VENV"
    python3 -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python3 -m pip install --quiet --upgrade pip
python3 -m pip install --quiet -r requirements.txt
echo "závislosti nainstalované v $VENV"

step "2/5  Stažení historie"
if ! python3 -m trading fetch --symbols "$SYMBOLS" --start "$START"; then
    cat <<'EOF'

Stažení selhalo úplně. Nejčastější příčiny:
  • nefunkční připojení nebo firewall blokující Yahoo Finance
  • dočasný výpadek burzovního API — zkuste za chvíli znovu

Náhradní cesta přes CSV: stáhněte data odkudkoli, uložte jako
data/csv/SPY.csv atd. se sloupci date,open,high,low,close,volume
a všude přidejte --csv-dir data/csv
EOF
    exit 1
fi
echo
echo "Tickery, které se nestáhly, jsou vypsané výše jako ERROR — ty pak"
echo "ze SYMBOLS vyhoďte. Zbytek pokračuje dál."

step "3/5  Dnešní signály (jen náhled, nic se neobchoduje)"
python3 -m trading signals --symbols "$SYMBOLS" --start "$START" --strategy tsmom

step "4/5  Založení paper účtu a přehrání historie"
if [ -f "$STATE" ]; then
    echo "Stav $STATE už existuje — pokračuji v něm."
    echo "Chcete-li začít od nuly, smažte ho."
fi
SYMBOLS="$SYMBOLS" CAPITAL="$CAPITAL" START="$START" STATE="$STATE" \
    ./scripts/tydenni-report.sh

step "5/5  Automatické spouštění"
cat <<EOF
Přidejte si do crontabu (pátek po zavření US burzy):

    crontab -e
    23 23 * * 5  $(pwd)/scripts/tydenni-report.sh >> \$HOME/paper.log 2>&1

Na macOS musí být notebook v tu chvíli vzhůru. Když bývá zavřený, použijte
launchd s klíčem StartCalendarInterval, který úlohu dožene po probuzení —
nebo prostě skript spusťte ručně, kdykoli se vám hodí. Na výsledku to nic
nemění: engine přehrává bar po baru, takže na době spuštění nezáleží.

Za měsíc se ozvěte s obsahem ~/paper.log. Očekávejte, že čísla nebudou
o ničem vypovídat: při kapitálu $CAPITAL USD je měsíční očekávaný zisk
kolem 8 USD proti běžnému rozptylu ±68 USD. Smyslem prvního měsíce je
ověřit, že celý řetěz funguje — ne zjistit, jestli strategie vydělává.
EOF
