#!/usr/bin/env bash
#
# Postaví nativní macOS aplikaci „Moje investice" do ~/Applications.
#
#     ./scripts/postav-aplikaci.sh
#
# Aplikace je okno nad výpočetním jádrem projektu: sama spustí
# `python -m trading dashboard`, ukáže ho a při zavření ho zastaví.
# Logika zůstává jen v Pythonu — po změně kódu stačí aplikaci znovu
# otevřít, stavět ji je potřeba jen při změně souborů v macos/.
#
# Staví se překladačem z Command Line Tools, takže nevadí nepotvrzená
# licence Xcode.

set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"

# Command Line Tools i s jejich SDK. Vybraný vývojářský adresář je Xcode,
# a dokud nemá potvrzenou licenci, překladač by si z něj vzal knihovny
# pro jinou verzi systému a skončil chybou "unable to load standard library".
export DEVELOPER_DIR=/Library/Developer/CommandLineTools
SWIFTC="$DEVELOPER_DIR/usr/bin/swiftc"
SWIFT_FLAGS=(-O -sdk "$DEVELOPER_DIR/SDKs/MacOSX.sdk" -target arm64-apple-macosx14.0)

CIL="${CIL:-$HOME/Applications/Moje investice.app}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Kreslím ikonu…"
"$SWIFTC" "${SWIFT_FLAGS[@]}" macos/ikona.swift -o "$TMP/ikona"
"$TMP/ikona" "$TMP/ikona.png"
mkdir -p "$TMP/Ikona.iconset"
for s in 16 32 128 256 512; do
    sips -z $s $s "$TMP/ikona.png" --out "$TMP/Ikona.iconset/icon_${s}x${s}.png" >/dev/null
    d=$((s * 2))
    sips -z $d $d "$TMP/ikona.png" --out "$TMP/Ikona.iconset/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$TMP/Ikona.iconset" -o "$TMP/Ikona.icns"

echo "Překládám aplikaci…"
mkdir -p "$TMP/app/Contents/MacOS" "$TMP/app/Contents/Resources"
"$SWIFTC" "${SWIFT_FLAGS[@]}" macos/MojeInvestice.swift -o "$TMP/app/Contents/MacOS/MojeInvestice"
cp "$TMP/Ikona.icns" "$TMP/app/Contents/Resources/"

cat > "$TMP/app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Moje investice</string>
    <key>CFBundleDisplayName</key><string>Moje investice</string>
    <key>CFBundleIdentifier</key><string>cz.trader.mojeinvestice</string>
    <key>CFBundleExecutable</key><string>MojeInvestice</string>
    <key>CFBundleIconFile</key><string>Ikona</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>LSMinimumSystemVersion</key><string>14.0</string>
    <key>NSHighResolutionCapable</key><true/>
    <!-- Okno načítá stránku z localhost po HTTP; bez výjimky by ji
         systém kvůli App Transport Security zablokoval. -->
    <key>NSAppTransportSecurity</key>
    <dict><key>NSAllowsLocalNetworking</key><true/></dict>
    <key>LSEnvironment</key>
    <dict><key>TRADER_REPO</key><string>${REPO}</string></dict>
</dict>
</plist>
PLIST

# Podpis ad-hoc: bez něj Apple Silicon aplikaci vůbec nespustí.
codesign --force --deep --sign - "$TMP/app" >/dev/null

mkdir -p "$(dirname "$CIL")"
rm -rf "$CIL"
mv "$TMP/app" "$CIL"
# Finder a Dock si jinak pamatují starou ikonu.
touch "$CIL"

echo
echo "Hotovo: $CIL"
echo "Otevřete ji z Finderu (Aplikace ve vaší domovské složce) nebo přetáhněte do Docku."
