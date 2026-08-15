#!/usr/bin/env bash
#
# Týdenní report paper účtu na **evropském (UCITS) univerzu** — tedy na
# titulech, které si evropský retail smí koupit. Jinak totéž co
# `tydenni-report.sh`: stejná strategie, stejné limity, stejný kapitál.
#
# Instalace (pátek po zavření evropských burz):
#
#     chmod +x scripts/tydenni-report-eu.sh
#     crontab -e
#     23 18 * * 5  /cesta/k/projektu/scripts/tydenni-report-eu.sh >> ~/paper-eu.log 2>&1
#
# Pozor na čas: LSE i Euronext zavírají dřív než US burzy, ale `IDTL.L`
# a spol. sledují americký podklad. Data z Yahoo jsou denní bary z burzy
# kotace, takže rozhoduje zavírací čas té burzy, ne New Yorku.
#
# ────────────────────────────────────────────────────────────────────────
# PROČ TAKHLE
#
# Titul za titulem odpovídá tabulce UCITS náhrad v UNIVERZUM.md. Čtrnáct
#   titulů napříč třídami aktiv, ne čtrnáct akcií — důvod je stejný jako
#   u US verze. Naměřený efektivní počet sázek je ale jen **3,00 ze 14**
#   (průměrná korelace 0,258): čtrnáct titulů nejsou čtrnáct sázek.
#
# IWDP.AS, ne IWDP.L. Je to **tentýž fond** (IE00B1FZS350), jen kotovaný
#   v Amsterodamu v eurech místo v Londýně v **pencích**. Pencová kotace
#   je stokrát vyšší číslo (1948 GBp proti 22,82 EUR), a protože engine
#   měny nerozlišuje, počítal by z ní velikost pozice, minimum pozice
#   i ATR stop stokrát vedle. Nespadne to — jen tiše počítá nesmysl.
#   Změřeno: s IWDP.L vyjde CAGR 6,98 % místo 7,37 %.
#   Pokud tenhle seznam měníte, u každého nového tickeru si ověřte pole
#   `currency` z yfinance. `GBp` != `GBP` a je to jediné místo, kde se to
#   pozná automaticky.
#
# UUP nemá náhradu. Dolarová složka z univerza vypadává — UCITS ETF na
#   dolarový index neexistuje a `DX-Y.NYB` je index, ne fond.
#
# START=2017-07-18. První bar ICOM.L, nejkratšího člena univerza. Dřívější
#   datum na tom nic nezmění: engine stejně čeká, až bude mít 252 barů pro
#   dvanáctiměsíční momentum, takže obchodovat začne až ~2018-07.
#
# Poplatky: preset `default` = nulová provize + skluz 5 bps, tedy Trading 212
#   nebo Alpaca. **U evropského brokera s minimem 2 EUR na příkaz to
#   nefunguje**: preset `retail_eu` dá na téhle konfiguraci CAGR −5,76 %
#   a za osm let 420 USD na provizích z tisícidolarového účtu. Broker
#   s nulovou provizí není detail, je to podmínka.
#
# ────────────────────────────────────────────────────────────────────────
# CO O TOMHLE UNIVERZU VÍME (změřeno, scratchpad/mereni-eu.md)
#
# Za okno 2018-07-12 .. 2026-08-13 (8,09 roku po rozehřátí momenta):
#
#     tsmom na tomhle univerzu     CAGR  7,37 %   Sharpe 0,706   maxDD 19,0 %
#     koš 14 rovnoměrně            CAGR  8,72 %   Sharpe 0,938   maxDD 21,1 %
#     IWDA.AS (MSCI World) držet   CAGR 13,19 %   Sharpe 0,855   maxDD 33,6 %
#     CSPX.AS (S&P 500) držet      CAGR 15,18 %   Sharpe 0,915   maxDD 33,6 %
#
# Strategie **neporazila ani jednu z těch laťek** — ani výnosem, ani
# Sharpem, ani po přeškálování na jejich volatilitu. Deflated Sharpe při
# poctivě započtených 672 pokusech vychází 0,591, tedy zamítá. Jediné,
# v čem strategie vede, je drawdown: 19 % proti 34 %.
#
# Tenhle skript je tedy nástroj na **provozní ověření a sběr dat na
# papíře**, ne doložená cesta k výnosu. Kdo od něj čeká víc, ať si
# nejdřív přečte mereni-eu.md.
# ────────────────────────────────────────────────────────────────────────

set -euo pipefail

cd "$(dirname "$0")/.."

# Čtrnáct UCITS titulů podle tabulky v UNIVERZUM.md.
#   akcie      CSPX.AS (S&P 500), XRS2.DE (Russell 2000),
#              VEUR.AS + VJPN.L (rozvinuté ex-US), EMIM.AS (rozvíjející se)
#   dluhopisy  IDTL.L (dlouhé), IDTM.L (střední), LQDE.L (korporátní),
#              IHYU.L (high yield), ITPS.L (inflační)
#   komodity   IGLN.L (zlato, ETC), PHAG.L (stříbro, ETC), ICOM.L (koš)
#   nemovitosti IWDP.AS
# Po každé změně ověřte `python -m trading ensemble`, jestli přibyly
# sázky, nebo jen kopie.
SYMBOLS="${SYMBOLS:-CSPX.AS,XRS2.DE,VEUR.AS,VJPN.L,EMIM.AS,IDTL.L,IDTM.L,LQDE.L,IHYU.L,ITPS.L,IGLN.L,PHAG.L,ICOM.L,IWDP.AS}"

CAPITAL="${CAPITAL:-1000}"
START="${START:-2017-07-18}"     # první bar ICOM.L, nejkratšího člena
STATE="${STATE:-data/paper_state_eu.json}"

echo "════════════════════════════════════════════════════════════"
echo "  TÝDENNÍ REPORT — EU (UCITS) — $(date '+%Y-%m-%d %H:%M %Z')"
echo "════════════════════════════════════════════════════════════"

python3 -m trading paper \
    --symbols "$SYMBOLS" \
    --start "$START" \
    --capital "$CAPITAL" \
    --strategy tsmom \
    --costs default \
    --state "$STATE" \
    --fractional \
    --min-position-value 2 \
    `# --risk-per-trade tady nic neřídí: se stopem 3×ATR ho vždycky` \
    `# přebije --max-position 0.12. Viz RiskConfig.risk_per_trade.` \
    --risk-per-trade 0.015 \
    --stop-atr 3.0 \
    --max-position 0.12 \
    --max-positions 10 \
    --max-drawdown 0.35 \
    --reentry-cooldown 10

echo
