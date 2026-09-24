#!/usr/bin/env bash
#
# Stáhne termíny CPI a NFP přímo z BLS a doplní je do kalendáře.
#
#     ./scripts/stahni-kalendar.sh [rok]
#
# Proč skriptem a ne ručně vypsané v repozitáři: termíny se mění a schéma
# nemá pravidelnost, ze které by se daly dopočítat. Data za listopad 2026
# vycházejí 18. prosince, tedy mimo obvyklý druhý týden. Stáhnout je od
# zdroje je jediný způsob, jak mít jistotu.
#
# FOMC skript nestahuje — Fed publikuje termíny v HTML tabulce, jejíž
# struktura se rok od roku mění a parsovat ji spolehlivě nejde. Zasedání
# je osm za rok, takže je rychlejší je jednou ročně opsat ručně
# z federalreserve.gov/monetarypolicy/fomccalendars.htm do data/udalosti.csv.

set -euo pipefail
cd "$(dirname "$0")/.."

YEAR="${1:-$(date +%Y)}"
OUT="${OUT:-data/udalosti-stazene.csv}"
SRC="https://www.bls.gov/schedule/news_release/${YEAR}_sched.htm"

echo "Stahuji plán zveřejnění BLS za rok $YEAR"
echo "  zdroj: $SRC"

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

if ! curl -fsSL --max-time 30 -A "trading-calendar/1.0" "$SRC" -o "$TMP"; then
    cat <<EOF

Stažení selhalo. BLS občas mění adresu ročního plánu. Zkuste:

  1. otevřít https://www.bls.gov/schedule/news_release/cpi.htm v prohlížeči
  2. termíny opsat do data/udalosti.csv ve tvaru:
         2026-10-13,CPI,
  3. nebo najít správnou adresu ročního plánu na https://www.bls.gov/schedule/

Je jich dvanáct za rok, takže je to pár minut jednou ročně.
EOF
    exit 1
fi

python3 - "$TMP" "$YEAR" "$OUT" <<'PYTHON'
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

raw, year, out = Path(sys.argv[1]).read_text(errors="replace"), sys.argv[2], Path(sys.argv[3])


class Stripper(HTMLParser):
    """Vytáhne holý text. BLS mění strukturu tabulek, ale texty zůstávají."""

    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


stripper = Stripper()
stripper.feed(raw)
text = re.sub(r"\s+", " ", " ".join(stripper.parts))

MONTHS = {
    m: i
    for i, m in enumerate(
        "January February March April May June July August September October "
        "November December".split(),
        start=1,
    )
}

# Řádky typu "Consumer Price Index ... Tuesday, October 13, 2026"
WANTED = {"Consumer Price Index": "CPI", "Employment Situation": "NFP"}
found = {}

for label, short in WANTED.items():
    for match in re.finditer(
        rf"{label}.{{0,120}}?(January|February|March|April|May|June|July|August|"
        rf"September|October|November|December)\s+(\d{{1,2}}),\s*({year})",
        text,
    ):
        month, day, yr = match.group(1), int(match.group(2)), match.group(3)
        found[(f"{yr}-{MONTHS[month]:02d}-{day:02d}", short)] = True

rows = sorted(found)
if not rows:
    print("  Ve stránce se nepodařilo najít žádný termín — struktura se změnila.")
    print("  Opište termíny ručně, viz nápověda výše.")
    sys.exit(1)

out.parent.mkdir(parents=True, exist_ok=True)
with out.open("w", encoding="utf-8") as handle:
    handle.write(f"# Staženo z BLS, plán za rok {year}\n")
    handle.write("# Před použitím zkontrolujte proti bls.gov/schedule/news_release/cpi.htm\n")
    handle.write("date,name,symbol\n")
    for day, name in rows:
        handle.write(f"{day},{name},\n")

print(f"  nalezeno {len(rows)} termínů, zapsáno do {out}")
for day, name in rows:
    print(f"    {day}  {name}")
PYTHON

cat <<EOF

Hotovo. Sloučit se stávajícím kalendářem můžete takhle:

    cat data/udalosti.csv $OUT | grep -v '^#' | grep -v '^date,' | sort -u \\
        > /tmp/slouceno.csv
    { echo 'date,name,symbol'; cat /tmp/slouceno.csv; } > data/udalosti-vse.csv

    python3 -m trading backtest --symbols SPY --start 2020-01-01 \\
        --calendar data/udalosti-vse.csv --blackout-before 1

Termíny si před ostrým nasazením zkontrolujte proti zdroji — parsování cizí
HTML stránky je vždycky křehké.
EOF
