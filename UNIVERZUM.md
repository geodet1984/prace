# Výběr titulů

## Proč křížem přes třídy aktiv, ne víc akcií

Diverzifikace zvedá Sharpe √N-krát jen tehdy, když jsou složky **nezávislé**.
SPY a QQQ mají korelaci kolem 0,9 — to nejsou dvě sázky, to je jedna sázka
dvakrát. Dvacet akciových ETF se v krizi hýbe jako jedno.

Proto univerzum obsahuje akcie, dluhopisy, kredit, komodity, nemovitosti
a měnu. Přesně na tomhle typu koše ověřili Moskowitz, Ooi a Pedersen časové
momentum — na 58 futures napříč akciovými indexy, měnami, komoditami
a dluhopisy.

**Neberte počet titulů jako počet sázek.** Po každé změně univerza spusťte:

```bash
python -m trading ensemble --symbols SPY,IWM,EFA,... --start 2010-01-01
```

a podívejte se na řádek *efektivní počet sázek*. Když přidáte pět titulů
a číslo se nezvedne, přidal jste kopie.

## Demo: US ETF

Pro paper trading jsou správnou volbou US ETF. Mají nejdelší a nejčistší
veřejnou historii, data jdou zdarma z Yahoo Finance a nikde se nic doopravdy
nekupuje.

```
SPY  IWM  EFA  EEM          akcie: US large, US small, rozvinuté ex-US, rozvíjející se
TLT  IEF  LQD  HYG  TIP     dluhopisy: dlouhé, střední, korporátní, high yield, inflační
GLD  SLV  DBC               komodity: zlato, stříbro, široký koš
VNQ                         nemovitosti
UUP                         dolarový index
```

## Ostrý účet v EU: US ETF koupit **nesmíte**

Od 1. ledna 2018 vyžaduje evropská regulace PRIIPs u každého produktu
nabízeného retailovému investorovi dokument KID. Američtí správci ho
nevydávají, protože je to evropský formát. Bez KID nesmí evropský broker
produkt prodat — a drží se toho **všichni**: Interactive Brokers, DEGIRO,
Trade Republic i Trading 212.

Řešením jsou UCITS verze, obvykle irské, které sledují stejné indexy a KID
mají. Většinou je vydává tentýž správce.

## Dostupné evropské univerzum

Ověřeno přes `yfinance` **15. 8. 2026** (119 tickerů, z toho 99 s daty).
Podrobný rozbor včetně zdrojů, korelací a měřených čísel je ve
`scratchpad/univerzum-eu.md`, surová data v `overeni_vse.csv`.

| US (demo) | UCITS náhrada | ISIN | TER | acc/dist | měna | let dat |
|---|---|---|---|---|---|---|
| SPY | `CSPX.AS` | IE00B5BMR087 | 0,07 % | acc | EUR | 16,2 |
| IWM | `XRS2.DE` | IE00BJZ2DD79 | 0,30 % | acc | EUR | 11,4 |
| EFA (1/2) | `VEUR.AS` | IE00B945VV12 | 0,10 % | dist | EUR | 13,2 |
| EFA (2/2) | `VJPN.L` | IE00B95PGT31 | 0,10 % | dist | GBP | 13,2 |
| EEM | `EMIM.AS` | IE00BKM4GZ66 | 0,18 % | acc | EUR | 12,2 |
| TLT | `IDTL.L` | IE00BSKRJZ44 | 0,07 % | dist | USD | 11,6 |
| IEF | `IDTM.L` | IE00B1FZS798 | 0,07 % | dist | USD | 17,6 |
| LQD | `LQDE.L` | IE0032895942 | 0,20 % | dist | USD | 18,6 |
| HYG | `IHYU.L` | IE00B4PY7Y77 | 0,50 % | dist | USD | 14,9 |
| TIP | `ITPS.L` | IE00B1FZSC47 | 0,10 % | acc | **GBP** | 18,6 |
| GLD | `IGLN.L` (ETC) | IE00B4ND3602 | 0,12 % | — | USD | 15,3 |
| SLV | `PHAG.L` (ETC) | JE00B1VS3333 | 0,49 % | — | USD | 18,6 |
| DBC | `ICOM.L` | IE00BDFL4P12 | 0,19 % | acc | USD | 9,1 |
| VNQ | `IWDP.AS` † | IE00B1FZS350 | 0,59 % | dist | EUR | 18,6 |
| UUP | **— díra —** | — | — | — | — | — |

† Londýnská kotace téhož fondu, `IWDP.L`, je v **pencích** — do univerza
patří ta amsterodamská. Podrobně níž v „Pozor na pence".

ISIN a TER pocházejí z rešerše (justETF, KIID a prospekty emitentů), ne
z měření. TER je křížově potvrzený polem `netExpenseRatio` z Yahoo u těch
deseti titulů, kde ho Yahoo vrací. **Ověřte si je proti KID u svého
brokera** a k zápisu do kódu přidejte datum ověření — TER se mění.

### Jeden fond, čtyři tickery, tři měny

Do univerza patří **jedna** kotace fondu. Kdyby jich tam bylo víc,
strategie by „diverzifikovala" mezi totožná aktiva — korelace 1,0, ne 0,9 —
a risk manager by to nepoznal, protože vidí jen ceny.

Výše uvedený výběr se řídí nejnižším podílem barů s nulovým objemem, při
shodě rozhoduje délka historie a obrat.

**Pozor na pence.** LSE kotuje řadu ETF v `GBp`, tedy setinách libry:

| fond | kotace A | kotace B | poměr |
|---|---|---|---|
| iShares Core MSCI World | `SWDA.L` = 11 021,35 **GBp** | `IWDA.AS` = 128,96 EUR | 85,5 |
| iShares Physical Gold | `SGLN.L` = 6 267,00 **GBp** | `IGLN.L` = 84,63 USD | 74,1 |
| iShares Dev. Mkts Property | `IWDP.L` = 1 948,00 **GBp** | `IWDP.AS` = 22,82 EUR | 85,4 |

Na výnosech se to neprojeví (podíl cen pence vykrátí), zato všude, kde se
pracuje s **absolutní úrovní ceny**: velikost pozice v `risk.py`,
`--min-position-value`, minimální provize v `CostModel`, ATR stopy
v penězích. Yahoo rozlišuje `GBp` od `GBP` v poli `currency` — je to
jediné místo, kde se to pozná automaticky.

**Z celého univerza je v pencích jediný titul, `IWDP.L`.** Ověřeno polem
`currency` u všech čtrnácti 15. 8. 2026. Zvlášť snadné je splést si
`ITPS.L`: je v `GBP`, ne `GBp` (cena 190,28), a v tabulce je zvýrazněný
jen proto, že není v USD. Zaměňovat GBP za pence je stejná chyba obráceně.

Vypořádat se s tím jde dvěma způsoby a ten druhý je lepší:

1. **dělit stovkou při načtení.** Musela by to znát každá cesta ke kódu —
   `paper`, dashboard, `ledger.py`, broker adaptér. Jedno zapomenuté
   místo a chyba je zpátky.
2. **vzít jinou kotaci téhož fondu.** `IWDP.AS` je `IE00B1FZS350` v EUR,
   tedy týž fond bez pencí, a navíc to srovnává měnu s eurovým blokem.
   Drží to na jednom řádku seznamu symbolů — proto to tak má
   `scripts/tydenni-report-eu.sh`.

Změřeno, co pence provedou, když se nechají být: `tsmom` na jinak
totožném univerzu dá s `IWDP.L` **6,98 %** místo 7,37 % ročně a 246
obchodů místo 239. **Nespadne to** — jen tiše vrátí číslo, které vypadá
stejně věrohodně jako to správné.

**Ticker není fond.** `IDTM.L`, `IBTM.L` a `IUSM.DE` jsou tatáž třída
téhož fondu (`IE00B1FZS798`) na třech burzách, ne trojice různých tříd.
`yfinance` ISIN nevrací (`t.isin` dá `-`, a když výjimečně něco vrátí, je
to špatně). Jediné spolehlivé kritérium totožnosti je **ISIN z KID**.

### Čemu se v tabulce vyhnout

* `IHYG.L` vypadá lépe než `IHYU.L`, ale je to **€** High Yield, ne **$** —
  jiný index, jiný trh.
* `ZPRS.DE` a `IUSN.DE` **nejsou** Russell 2000, ale MSCI World Small Cap.
* `SSLN.L` má data, ale **97,5 % barů s nulovým objemem**.
* `DX-Y.NYB` je index, ne fond: 100 % nulových objemů, nekupitelný. Totéž
  měnové páry `EURUSD=X` a spol. — jako data v pořádku, koupit se nedají.
* `EIMI.L`, `EMIM.L`, `IS3N.DE` a `EMIM.AS` jsou **jeden fond**.

### Díry a zkrácené historie

| co | dopad |
|---|---|
| UUP (dolar) | **náhrada neexistuje**, složka vypadává |
| EFA | žádná 1:1; buď dvě položky, nebo `EXUS` s 2,4 roku dat |
| DBC → `ICOM.L` | 9,1 roku místo ~20 (chybí 2008 i cyklus 2011–2015) |
| TLT → `IDTL.L` | 11,6 roku místo ~23 |
| IWM → `XRS2.DE` | 11,4 roku; Russell 2000 je v UCITS vzácný |
| VNQ → `IWDP.AS` | jiná expozice (svět místo USA); londýnská kotace navíc v pencích |

Nejkratší člen řetězu určuje start portfolia: **9,1 roku** (`ICOM.L`, od
2017-07). Po rozehřátí na dvanáctiměsíční momentum zbývá zhruba **8 let**
společné historie proti 15+ u US univerza. Tohle **musí** do
`deflated_sharpe_ratio` a MinTRL — kratší vzorek znamená širší interval
spolehlivosti, ne stejný výsledek.

### Změřeno: strategie tohle univerzum neporazí

Přeměřeno 15. 8. 2026 konfigurací ze `scripts/tydenni-report-eu.sh`
(kapitál 1000, `tsmom(252,252)`, `--costs default`, stop 3×ATR,
`--max-position 0.12`, `--max-positions 10`, zlomkové akcie). Okno
**2018-07-12 .. 2026-08-13**, tedy 2 076 barů = 8,09 roku po rozehřátí
momenta; ve stejném okně i laťky. Plná zpráva:
`scratchpad/mereni-eu.md`.

| | CAGR | vol | Sharpe | maxDD |
|---|---|---|---|---|
| **`tsmom` na tomhle univerzu** | **7,37 %** | 10,7 % | 0,706 | 19,0 % |
| koš 14 rovnoměrně, denní rebalance | 8,72 % | 9,2 % | **0,938** | 21,1 % |
| `IWDA.AS` (MSCI World) prostě držet | 13,19 % | 15,7 % | 0,855 | 33,6 % |
| `CSPX.AS` (S&P 500) prostě držet | **15,18 %** | 16,7 % | 0,915 | 33,6 % |

Stejný obrázek jako na US univerzu: strategie **neporazí ani vlastní
rovnoměrně vážený koš**, natož index. Mezera se nezavře ani po
přeškálování na volatilitu laťky (na volatilitě `CSPX.AS` by strategie
dala 11,17 % proti 15,18 %). Rozklad: volba univerza stojí **−6,46 pp**
proti držení S&P 500, samotné časování a řízení rizika **−1,35 pp**.

Statistika při poctivě započtených **672** parametrických pokusech
(624 zděděných z předchozí diagnostiky + 48 nových):
deflated Sharpe **0,591** → zamítá; PSR proti nule 0,976, ale PSR proti
koši jen **0,259**; `min_track_record_length` proti koši vychází
**nekonečno** (při Sharpe pod laťkou ho žádná délka historie neprokáže);
bootstrap Sharpe 95 % **[0,039 ; 1,372]**; PBO **0,399** proti 0,144 na
US datech — na kratším vzorku je volba parametrů výrazně vratší.

Jediné, v čem strategie vede, je drawdown: 19 % proti 34 %.

**Efektivní počet sázek je 3,00 ze 14** (průměrná korelace 0,258,
diverzifikační poměr 1,731). Čtrnáct titulů nejsou čtrnáct sázek.

### Nákladový preset rozhoduje o všem

Táž konfigurace, totéž okno, jen jiné `--costs`:

| preset | CAGR | Sharpe | zaplacené provize |
|---|---|---|---|
| `zero` | 7,58 % | 0,727 | 0 |
| `default` (0 provize, 5 bps skluz) | 7,37 % | 0,706 | 0 |
| `retail_eu` (0,15 %, min 2, 10 bps) | **−5,76 %** | −0,965 | **420 USD** |

Minimum 2 USD na příkaz proti pozici ~120 USD je 3,3 % na obrátku a za
osm let sežere 42 % počátečního kapitálu. **Broker s nulovou provizí není
detail, je to podmínka** — u UCITS ETF platí totéž, co STAV.md říká
o IBKR.

Nezměřené zůstává, o kolik jsou reálné spready UCITS verzí širší než
u amerických originálů. Z denních barů to nezjistíte.

### Zlato a komodity jsou ETC, ne ETF

Čl. 50 odst. 2 písm. b) směrnice 2009/65/ES zakazuje UCITS *„acquire
either precious metals or certificates representing them"*. Fond držící
jen zlato tedy nemůže být UCITS ETF — a to ještě před pravidlem 5/10/40.

Evropské řešení strukturu fondu obchází: **ETC není fond, ale dluhový
cenný papír.** U ETF vlastníte podíl na majetku fondu, **u ETC pohledávku
za emitentem** — účelovou společností, která koupila kov a dala ho do
zástavy ve prospěch trustee.

Prakticky to znamená riziko, které **v cenové řadě není vidět a žádná
metrika ze `stats.py` ho nezachytí**, protože se počítá z výnosů: cenová
řada zlata a cenová řada nároku na zlato se liší jen ve scénáři, který
v historickém vzorku nenastal. Je to riziko mimo model.

Mírnější podoba téhož platí pro `ICOM.L`: formálně plnohodnotný UCITS ETF,
ale expozici bere **nefondovaným swapem** — tedy opět přes protistranu.

Podrobněji (alokované vs. nealokované sloupky, kdo vymáhá zajištění,
*limited recourse*) viz `scratchpad/univerzum-eu.md`.

### Měna: co engine zatím neumí

UCITS ETF se obchodují v EUR/GBP, **podklad je v USD**. Změřeno na `IWDA`
(tentýž fond ve dvou kotacích, 2 922 dní od 2015): `var(EURUSD) /
var(IWDA.AS)` = **24,8 %**. Čtvrtina kolísání eurové kotace není pohyb
světových akcií, ale pohyb dolaru.

**Backtestovací engine měnu neřeší vůbec.** `portfolio.py`, `backtest.py`
ani `data.py` o měně nevědí; pole `currency` v `ledger.py` je pro ně jen
popisek. V univerzu celém v USD to nevadilo. Jakmile ale běží `CSPX.AS`
(EUR), `IDTL.L` (USD), `VJPN.L` (GBP) a `IWDP.L` (GBp) současně, engine
**sčítá hodnoty v různých měnách jako čísla**. Nespadne to, jen tiše
počítá nesmysl.

(Pozor na rozlišení: `trading/fx.py` s kurzy ČNB a `ledger.valuation_czk`
řeší **evidenci** — přecenění knihy do korun. Cenové řady, ze kterých
počítá backtest, nepřevádí nic.)

Nejjednodušší východisko: **zvolit jednu měnu kotace pro celé univerzum.**
V USD existují `CSPX.L`, `IDTL.L`, `IDTM.L`, `LQDE.L`, `IHYU.L`, `IGLN.L`,
`PHAG.L`, `ICOM.L`, `EIMI.L`; v EUR `CSPX.AS`, `EMIM.AS`, `VEUR.AS`,
`IS04.DE`, `XRS2.DE`, `4GLD.DE`, `IWDP.AS`.

Co **nedělat**: přepočítávat řady denním kurzem z Yahoo bez kontroly
zarovnání. Korelace `(r_EUR − r_USD)` s `(−r_EURUSD)` vychází **0,22 při
posunu 0, ale 0,75 při posunu o den** — řada `EURUSD=X` je proti burzovním
barům posunutá. Případná konverze patří do `data.py` k načtení, ne
dodatečně na výnosy, a podle bodu 5 v CLAUDE.md musí do `live.py`
v `save()` i `load()` a zvednout `STATE_VERSION`.

### Krypto

Ověřeno, všechny **0 % barů s nulovým objemem**: `BTC-USD` (4 351 barů od
2014-09-17), `ETH-USD` (3 202 od 2017-11-09), `SOL-USD` (2 319 od
2020-04-10), dále `LTC/XRP/ADA/DOGE/BNB-USD` a eurové `BTC-EUR`, `ETH-EUR`.

**Doporučené univerzum jsou dva až tři tituly.** Naměřený efektivní počet
sázek při rovných vahách: BTC+ETH = **1,10**, s SOL = **1,28**, všech osm
kryptoměn = **1,56 z 8**. Víc titulů přidá obrat a poplatky, ne
diverzifikaci. Proti ETF univerzu ale krypto skutečně diverzifikuje —
korelace `BTC-USD` s `IWDA.AS` je 0,19, s `IDTL.L` −0,02.

Naměřeno **365,3 barů na rok** proti ~252 u ETF. Bez
`--periods-per-year 365` vyjde Sharpe podhodnocený o √(365/252) = **1,204**.
Méně zjevný důsledek: **lookback je v barech, ne v kalendáři** —
`tsmom(lookback=252)` je u krypta osm a půl měsíce, ne rok.

Volatilita je jinde o řád: `BTC-USD` 64 % ročně a propad −82 %, `ETH-USD`
84 % a −94 %, `SOL-USD` 118 % a −96 % — proti 16 % a −34 % u `IWDA.AS`.
Cílování volatility v `sizing.py` tím přestává být kosmetika a kill-switch
nastavený na akciové propady se na kryptu spustí prakticky pořád.

**Změřeno (BTC+ETH, 2018-07-19 .. 2026-08-15, 2 950 barů, 365 barů/rok):**

| | CAGR | Sharpe | maxDD | obchodů |
|---|---|---|---|---|
| `tsmom(252,252)`, `--costs binance` | 17,35 % | 0,730 | 43,9 % | 56 |
| `tsmom(365,365)`, `--costs binance` | 11,67 % | 0,560 | 43,0 % | 22 |
| `BTC-USD` prostě držet | **30,21 %** | **0,741** | 76,6 % | 1 |
| BTC+ETH rovnoměrně, denní rebalance | 27,72 % | 0,706 | 76,3 % | — |

**Táž odpověď jako u ETF: držení to neporazí.** Sharpe 0,730 proti 0,741
je shoda; strategie jen půlí drawdown (44 % z 77 %). Deflated Sharpe pro
365/365 vychází **0,063**, bootstrap Sharpe 95 % **[−0,136 ; 1,295]**
nulu nevylučuje a MinTRL proti nule je 3 232 barů = **8,85 roku**, tedy
o devět měsíců víc, než kolik ETH vůbec existuje. Na kryptu je tvrzení
slabší než na ETF, ne silnější.

Že 252 barů dopadlo lépe než 365, jsou dva pokusy ze dvou — vyvozovat
z toho „na kryptu funguje kratší momentum" by bylo přesně to, proti čemu
je `stats.py`.

**Přidání krypta k ETF univerzu při rovných vahách nediverzifikuje:**
efektivní počet sázek 3,00 ze 14 → **3,03 ze 16**. Korelace tam chybí
(BTC s `CSPX.AS` 0,167, s `IDTL.L` 0,019), jenže BTC svou volatilitou
pohltí tolik rizika portfolia, že se efekt nedostaví. Bez cílování
volatility to nemá smysl.

Podrobnosti k poplatkům a míchání kalendářů viz `KRYPTO.md`
a `scratchpad/univerzum-eu.md`.

## Jak si to ověřit sám

```bash
python -m trading fetch --symbols CSPX.AS,IDTL.L,EMIM.AS --start 2015-01-01
```

Co projde, vypíše se s počtem barů. Co ne, skončí jako `ERROR`.

Pozor na past, na kterou narazil první pokus o tohle ověření:
**výpadek sítě vypadá stejně jako neexistující ticker.** `yfinance` vrátí
prázdnou odpověď v obou případech. Poznávací znamení je souvislá řada
selhání od určitého tickeru dál — skutečná nedostupnost se takhle
neshlukuje. Při ověřování většího seznamu ukládejte průběžně a výsledek
si přeověřte; skutečné 404 pozná `yfinance` textem
*„Quote not found for symbol"*.

## Na co si dát pozor u UCITS verzí

* **Kratší historie.** Řada evropských fondů vznikla po roce 2010. Pro
  dvanáctiměsíční momentum plus rozehřátí potřebujete aspoň 1,5 roku dat
  navíc před začátkem obchodování.
* **Menší likvidita.** Spready bývají širší než u amerických originálů,
  takže reálný skluz bude vyšší než výchozích 5 bps. Do `--costs` to zatím
  nepromítnete jinak než volbou přísnějšího presetu.
* **Akumulační vs. distribuční.** Akumulační (`acc`) reinvestují dividendy
  uvnitř fondu. Pro strategii je to jedno, pro daně ne — proberte s daňovým
  poradcem.

## Známé omezení výběru pozic

Když má univerzum 14 titulů a `--max-positions` je 6, engine obsadí sloty
**v abecedním pořadí symbolů**, ne podle síly signálu. Při shodně silných
signálech (což je případ `tsmom`, kde je síla vždy 1,0) to nevadí, ale je
to slabina, jakmile budete chtít vybírat těch „nejlepších N".

Obejít se to zatím dá jen tím, že necháte projít víc pozic a limit necháte
na hotovosti — proto je v `tydenni-report.sh` `--max-position 0.12`
a `--max-positions 10`.

## Skripty

| skript | univerzum | stav |
|---|---|---|
| `scripts/tydenni-report.sh` | 14 US ETF (demo, v EU nekupitelné) | `data/paper_state.json` |
| `scripts/tydenni-report-eu.sh` | 14 UCITS titulů z tabulky výš | `data/paper_state_eu.json` |
