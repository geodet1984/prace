#!/usr/bin/env bash
#
# Zaregistruje denního hlídače portfolia do launchd (plánovač macOS).
#
#     ./scripts/nainstaluj-hlidace.sh          # zapne, výchozí 18:00
#     HODINA=9 ./scripts/nainstaluj-hlidace.sh # jinou hodinu
#     ./scripts/nainstaluj-hlidace.sh --zrusit # vypne
#
# Proč launchd a ne cron: na macOS je launchd domácí plánovač a umí
# dohnat běh, který zmeškal (počítač byl uspaný). Cron by ho tiše
# přeskočil, což je u něčeho, co běží jednou denně, dost.
#
# Hlídač poslouchá jen sám sebe: čte knihu, stahuje ceny a kurzy, píše
# oznámení. Nic neobchoduje a nikam nic neposílá.

set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"

NAZEV="cz.trader.hlidac"
PLIST="$HOME/Library/LaunchAgents/${NAZEV}.plist"
HODINA="${HODINA:-18}"

if [ "${1:-}" = "--zrusit" ]; then
    launchctl bootout "gui/$(id -u)/${NAZEV}" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Hlídač vypnut a odregistrován."
    exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLISTEND
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>${NAZEV}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${REPO}/scripts/hlidac.sh</string>
    </array>
    <key>WorkingDirectory</key><string>${REPO}</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>${HODINA}</integer>
        <key>Minute</key><integer>0</integer>
    </dict>
    <!-- Dohnat běh zmeškaný kvůli uspanému počítači. Bez tohohle by
         hlídač po víkendu s víkem dolů prostě mlčel. -->
    <key>RunAtLoad</key><false/>
    <key>StandardOutPath</key><string>${REPO}/data/hlidac_launchd.log</string>
    <key>StandardErrorPath</key><string>${REPO}/data/hlidac_launchd.log</string>
</dict>
</plist>
PLISTEND

launchctl bootout "gui/$(id -u)/${NAZEV}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

cat <<EOF

Hlídač zapnut. Poběží každý den v ${HODINA}:00.

  vyzkoušet hned:   ./scripts/hlidac.sh
  co hlásil:        tail -30 data/hlidac.log
  vypnout:          ./scripts/nainstaluj-hlidace.sh --zrusit

Při prvním oznámení si macOS řekne o povolení — potvrďte ho, jinak
oznámení nepůjdou (Nastavení → Oznámení → Skript Editor).

EOF
