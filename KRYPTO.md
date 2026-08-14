# Krypto a zpravodajství

Dvě otázky, které vypadají podobně, ale odpověď mají opačnou.

## Krypto: ano, ale rozhoduje o tom jiné číslo než u akcií

Engine je vůči třídě aktiv slepý — dostane OHLCV a je mu jedno, jestli je to
akcie nebo bitcoin. Data jdou přes `yfinance` beze změny:

```bash
python -m trading backtest --symbols BTC-USD,ETH-USD,SOL-USD \
    --start 2018-01-01 --costs binance --periods-per-year 365 \
    --fractional --min-position-value 2
```

Musí se ale změnit dvě věci, jinak jsou výsledky špatně.

### 1. Rok má 365 barů, ne 252

Krypto se obchoduje i o víkendu. Nechat výchozích 252 dní znamená
**podhodnotit Sharpe o faktor √(365/252) = 1,20**. Proto `--periods-per-year 365`.

### 2. Náklady jsou řádově jinde

Provize na burzách jsou v procentech, ne nula jako u ETF u Trading 212:

| Trh | Náklady na obrat | 31 obratů/rok | 100 obratů/rok |
|---|---|---|---|
| ETF (Trading 212) | 0,12 $ | 0,4 % | 1,2 % |
| Krypto (Binance, 0,10 %) | 0,48 $ | 1,5 % | 4,8 % |
| Krypto (Kraken, 0,26 %) | 0,96 $ | 3,0 % | 9,6 % |
| Krypto (Coinbase Adv, 0,60 %) | 1,80 $ | **5,6 %** | **18,0 %** |

*(pozice 12 % z 1 000 USD)*

Nákladový strop — kolik obratů ročně to unese, aby náklady zůstaly pod 2 %
kapitálu:

| Burza | Strop |
|---|---|
| ETF | 167 obratů/rok |
| Binance | 42 |
| Kraken | 21 |
| Coinbase Advanced | **11** |

**Coinbase Advanced je na systematické obchodování nepoužitelný.** Preset
`--costs coinbase` je v projektu hlavně proto, aby to bylo vidět v číslech.

### 3. A ještě volatilita

Cílování volatility drží krypto pozice velmi malé:

| Aktivum | Roční volatilita | Expozice pro cíl 12 % |
|---|---|---|
| Koš 14 ETF | 10 % | 120 % |
| SPY | 16 % | 75 % |
| BTC | 55 % | **22 %** |
| ETH | 75 % | **16 %** |

Za správně řízené riziko tedy dostanete jen pětinovou expozici — a náklady
platíte z obratu, ne z expozice.

### Co pro krypto mluví

Trend-following má v kryptu doloženou historii přes deset let a korelace
s akciemi je nižší než mezi akciemi navzájem. Jako **přídavek k univerzu**
kvůli breadth to smysl dává. Jako hlavní trh spíš ne.

Rozumný začátek: BTC-USD a ETH-USD vedle stávajících ETF, s `--costs binance`,
a pak `python -m trading ensemble` na kontrolu, jestli efektivní počet sázek
skutečně stoupl.

### Daně

Kryptoaktiva **nejsou cenné papíry**. Od roku 2026 se na ně vztahuje časový
test i hodnotový limit 100 000 Kč, ale se stropem 40 milionů, který se nově
týká právě jen krypta. Než na tom postavíte cokoli většího, proberte to
s daňovým poradcem — pravidla se v posledních dvou letech měnila opakovaně.

## Zpravodajství jako signál: ne

Otázka „mohla by aplikace sama sbírat informace o dění ve světě a nabízet
nákup nebo prodej" má technicky snadnou odpověď — RSS, API, sentiment. Ale:

**Zprávy jsou v ceně během sekund.** Než se článek dostane do RSS, obchodovaly
na něm algoritmy s přímým napojením na agentury. Retailový nástroj čte
historii, ne příležitost.

**Je to přesně ta past s přeoptimalizováním.** Textové rysy jsou
vysokodimenzionální a signál v nich slabý. To je ideální prostředí na nalezení
souvislostí, které v datech nejsou. Vzpomeňte na Sullivana, Timmermanna
a Whitea: 7 846 pravidel, po korekci na data snooping žádné ziskové.

**A hlavně: jazykový model vám na tuhle otázku vždycky odpoví.** Nikdy neřekne
„nevím", vždycky vyrobí souvislý příběh o tom, proč trh zareaguje tak či onak.
Ten příběh nejde odlišit od skutečné analýzy jinak než tím, že ho otestujete
na datech — a na to potřebujete point-in-time archiv zpráv, který zdarma
nedostanete.

## Zpravodajství jako řízení rizika: ano

Existuje užší použití, které obstojí, protože **nic nepředpovídá**:

**Blackout kolem známých událostí.** Neotvírat nové pozice den před zasedáním
Fedu, zveřejněním CPI nebo výsledků firmy. Nepotřebuje to předpověď — kalendář
je veřejný — a snižuje to riziko mezery přes noc, kterou stop-loss neochrání.

**Detekce výjimečného stavu.** Když volatilita překročí několikanásobek
obvyklé úrovně, snížit expozici nebo zastavit vstupy. To už v projektu částečně
je (`--vol-target`, kill-switch na drawdown) a dá se to rozšířit o vnější
ukazatel typu VIX.

Rozdíl je v tom, na co se ptáte:

| Otázka | Odpověď |
|---|---|
| „Co udělá trh, když vyjde tahle zpráva?" | Nikdo neví, model si vymyslí |
| „Je dnes den, kdy se zvyšuje riziko mezery?" | Kalendář, ověřitelné |

První je předpověď, druhá je fakt. Stavte na druhém.
