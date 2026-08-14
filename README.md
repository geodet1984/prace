# Trading — backtest, validace a paper trading akciových strategií

Nástroj, který sleduje akciový trh, vyhodnocuje stav titulů podle definované
strategie a obchoduje je s řízeným rizikem — nejdřív na historii (backtest),
pak proti poctivé validaci, pak nanečisto proti živým cenám (paper trading).

## Nejdřív to důležité

**Aplikace, která je vždy v zisku, neexistuje a tenhle projekt jí není.**

Cena v okamžiku nákupu je jediná známá veličina, budoucí cena je náhodná.
„Nikdy neprodělat" jde jen předstírat tím, že se ztrátové pozice nikdy
neuzavřou — účetně pak ztráta není vidět, reálně máte zamrzlý kapitál
a rostoucí riziko.

Jak nepříjemné to je: Sullivan, Timmermann a White (1999) prohnali **7846**
technických obchodních pravidel přes Dow Jones a S&P 500 s korekcí na data
snooping a nenašli **žádné** ziskové. Ne že by v backtestu nevydělávala —
vydělávala. Šlo o to, že když zkoušíte tisíce variant, nejlepší z nich
vypadá skvěle čirou náhodou.

Reálný cíl je **kladná očekávaná hodnota při propadu, který se dá ustát**.

## Matematika, která podle dat pomáhá nejvíc

Tohle je jádro projektu. Není to další indikátor — indikátory jsou to
nejméně cenné, co v systematickém obchodování je.

### 1. Diverzifikace: jediný oběd zdarma

Zprůměrujete-li N **nekorelovaných** strategií, výnos zůstane a volatilita
klesne √N-krát. Sharpe se vynásobí √N. Grinoldův fundamentální zákon říká
totéž obecně: `IR = IC · √breadth`. Informační koeficient (schopnost
předpovídat) roste těžko — je to boj proti zbytku trhu. Breadth roste
snadno: víc titulů, víc tříd aktiv, víc **rodin** signálů.

Pět strategií se Sharpe 0,4, které spolu nekorelují, dá dohromady 0,89.

Háček je ve slově *nezávislých*. Pět variant klouzavého průměru není pět
sázek, je to jedna sázka pětkrát. Modul `ensemble.py` proto kromě vážení
počítá průměrnou korelaci, **efektivní počet sázek** a diverzifikační poměr.
Rodiny, které podle literatury korelují slabě nebo záporně: hodnota vs.
momentum (−0,4 až −0,7), trend-following vs. návrat k průměru, různé
časové horizonty.

```bash
python -m trading ensemble --symbols SPY --start 2010-01-01
```

### 2. Cílování volatility

Harvey a spol. (2018) prohnali 60 aktiv s daty od roku 1926. Škálování
expozice na konstantní cílovou volatilitu **zvyšuje Sharpe u akcií a kreditu**
a napříč všemi třídami aktiv **snižuje pravděpodobnost extrémních výnosů**.
U dluhopisů, měn a komodit je vliv na Sharpe zanedbatelný, omezení chvostů
zůstává. Mechanismus je tzv. leverage effect: u akcií jsou volatilita
a výnosy záporně korelované, takže snížení expozice při rostoucí volatilitě
funguje jako vedlejší momentum overlay.

Poctivá poznámka z vlastního měření: na simulovaných GARCH datech s konstantním
driftem cílování spolehlivě zkrátilo chvosty (špičatost 4,11 → 3,38, nejhorší
den −5,0 % → −4,1 %), ale Sharpe **snížilo**. Zlepšení Sharpe závisí na tom,
jestli aktivum tu zápornou vazbu skutečně vykazuje — omezení chvostů dostanete
vždycky, výnos ne.

```bash
python -m trading backtest --symbols SPY --vol-target 0.10
```

### 3. Zlomkové Kellyho kritérium

Kelly určuje podíl kapitálu maximalizující dlouhodobý růst: `f* = μ/σ²`, což
je pro normální výnosy totéž co `SR/σ`. Plné Kelly je optimální jen v limitě,
kde znáte pravděpodobnost výhry přesně.

Růstová funkce `g(f) = f·μ − f²σ²/2` je kolem maxima velmi plochá a chyba je
brutálně asymetrická. Ověřeno v testech:

| Sázka | Podíl maximálního růstu |
|-------|------------------------|
| ¼ Kelly | 44 % |
| **½ Kelly** | **75 %** |
| 1× Kelly | 100 % |
| 1,5× Kelly | 75 % |
| 2× Kelly | **0 %** |

Poloviční Kelly zachová tři čtvrtiny růstu při zhruba polovičním drawdownu.
Dvojnásobek nevydělá nic. Proto se chybuje směrem dolů.

```bash
python -m trading backtest --symbols SPY --kelly 0.25
```

### 4. Statistika, která umí strategii zamítnout

Tohle má **vyšší hodnotu než jakýkoli signál**. Modul `stats.py`:

* **Probabilistic Sharpe Ratio** — pravděpodobnost, že skutečný Sharpe je nad
  prahem, se zohledněním šikmosti a špičatosti. Dvě strategie se stejným
  Sharpe si nezaslouží stejnou důvěru: záporná šikmost a tlusté chvosty
  pravděpodobnost snižují, aniž by se Sharpe pohnul.
* **Deflated Sharpe Ratio** — totéž po korekci na počet vyzkoušených variant.
  Když vyzkoušíte 100 konfigurací a Sharpe jednotlivých pokusů kolísá se
  směrodatnou odchylkou 0,5, nejlepší z nich bude mít Sharpe kolem **1,3
  čistě náhodou**. To je laťka, kterou musíte přeskočit.
* **Minimum Track Record Length** — kolik historie vůbec potřebujete. Bývá to
  brutální: pro Sharpe 0,7 vychází přes 5 let jen na prokázání, že je nad nulou.
* **Probability of Backtest Overfitting** (CSCV) — jak často vítěz
  z in-sample propadne out-of-sample. Kolem 0,5 znamená, že vaše kritérium
  výběru nemá s budoucností nic společného.
* **Blokový bootstrap** — interval spolehlivosti pro Sharpe. Bloky, ne
  jednotlivé dny: výnosy jsou autokorelované a obyčejný bootstrap by dal
  falešně úzký interval.

### 5. Walk-forward validace

Backtest na celé historii popisuje minulost. Walk-forward ladí parametry na
prvním úseku, testuje na navazujícím a posouvá okno. Spojené out-of-sample
úseky jsou **jediná equity křivka, která má smysl někomu ukazovat**.

Všechno tohle dohromady spustí jeden příkaz:

```bash
python -m trading validate --symbols SPY --start 2005-01-01 \
    --strategy sma_crossover --grid fast=5,10,20 --grid slow=50,100,150
```

Ukázka na náhodné procházce, kde žádný edge neexistuje — nástroj to pozná:

```
1) WALK-FORWARD
  Průměrný IS Sharpe                  0.51
  Průměrný OOS Sharpe                -0.33
  Propad IS -> OOS                    0.84
  Stabilita parametrů                50.0%

2) DEFLATED SHARPE
  Pozorovaný Sharpe              0.14
  Laťka z   63 pokusů            0.89
  Deflated Sharpe Ratio         0.023   NEPRŮKAZNÉ
  95% interval Sharpe           -0.53 .. 0.84
  ! Interval obsahuje nulu — data neumí odlišit schopnost od náhody.

3) PBO
  PBO                           50.0%   PŘEOPTIMALIZOVÁNO

Strategie neprošla. Výsledek se nedá odlišit od šťastného hledání.
```

## Instalace

```bash
pip install -r requirements.txt      # pandas, numpy, yfinance
pip install -e ".[dev]"              # + pytest, ruff
```

Python 3.10+. Jádro potřebuje jen pandas a numpy; `scipy` schválně ne —
hierarchické shlukování pro HRP i normální rozdělení pro PSR jsou napsané
proti standardní knihovně.

## Příkazy

| Příkaz | Co dělá |
|--------|---------|
| `backtest` | Jedna strategie na historii, kompletní report |
| `compare` | Všechny strategie proti sobě a proti pasivnímu držení |
| `validate` | **Walk-forward + deflated Sharpe + PBO** |
| `ensemble` | Kombinace strategií a měření přínosu diverzifikace |
| `signals` | Aktuální signály, nic neobchoduje |
| `paper` | Posun paper účtu o nová data |
| `fetch` | Stažení a nacachování dat |

Nápověda: `python -m trading <příkaz> --help`.

## Provoz na malém účtu

Pro kapitál kolem 500 USD / 10 000 Kč je připravený `scripts/tydenni-report.sh`:
sedm ETF napříč třídami aktiv, `tsmom`, nulová provize, zlomkové akcie.
V hlavičce skriptu je zdůvodnění každé volby.

```bash
chmod +x scripts/tydenni-report.sh
crontab -e
23 23 * * 5  /cesta/k/projektu/scripts/tydenni-report.sh >> ~/paper.log 2>&1
```

Frekvence spouštění **nemění výsledek**. Engine přehrává bar po baru, takže
týdenní běh zpracuje pět barů stejně, jako by je zpracoval pět denních běhů —
ověřuje to `test_result_does_not_depend_on_how_often_you_run_it`. Řidší
hlášení je proto zadarmo, a psychologicky lepší: u strategie se Sharpe 0,8
ukáže hodinový pohled ztrátu v 49 % případů (806× ročně), týdenní v 46 %
(24× ročně). Stejná strategie, o dva řády míň příležitostí do ní sáhnout.

Nákladový strop: aby poplatky snědly nejvýš pětinu očekávaného výnosu, vychází
maximum kolem **80 obratů ročně** — a to nezávisle na velikosti účtu, protože
pozice i náklady škálují spolu. Všechny strategie v projektu leží pod ním
(5–60). Intradenní obchodování se do něj nevejde ani omylem: 10 obchodů za
hodinu je 8 190 obratů ročně, tedy 205 % kapitálu na samotném skluzu.

## Připojení brokera

Vrstva `trading/brokers/` odděluje rozhodování od provádění. Strategie
nepozná, jestli běží proti simulaci nebo reálnému účtu.

| Modul | Role |
|-------|------|
| `base.py` | Rozhraní `Broker` a datové typy |
| `simulated.py` | Plná implementace nad interním portfoliem |
| `guard.py` | Bezpečnostní obal — limity, kill-switch, povinné potvrzení |
| `alpaca.py` | Adaptér na Alpaca REST API v2 |

```python
from trading.brokers import SimulatedBroker, GuardedBroker, SafetyLimits, BrokerOrder
from trading.models import Side

broker = GuardedBroker(
    SimulatedBroker(100_000),
    SafetyLimits(max_order_value=5_000, max_orders_per_day=20),
    allow_live=False,   # bez tohohle na ostrý účet neodejde nic
    dry_run=True,       # a takhle se příkazy jen zaznamenají
)
broker.connect()
broker.submit_order(BrokerOrder("AAPL", Side.BUY, 10))
```

### Proč je ostrý režim nepohodlný schválně

Největší riziko automatu není špatná strategie — ta prodělává pomalu a je
vidět. Je to chyba v kódu, která pošle příkaz stokrát. `GuardedBroker`
kontroluje **před** odesláním: strop na objem jednoho příkazu i na denní
součet, strop na počet příkazů za den, seznam povolených titulů, obchodní
hodiny, podíl na kapitálu, kill-switch. Příkaz, jehož hodnotu nelze ověřit
(chybí cena), neprojde. Rušení příkazů se naopak neblokuje nikdy — je to
vždy krok k menšímu riziku.

### Vlastní adaptér

Implementujte `Broker` z `base.py`: `connect`, `disconnect`, `is_connected`,
`get_account`, `get_positions`, `submit_order`, `cancel_order`, `get_order`,
`get_open_orders`, `is_market_open`. Deset metod. Když projde stejnou sadou
testů jako `SimulatedBroker` (`tests/test_brokers.py`), je šance, že se bude
chovat stejně.

Návrhová pravidla, která rozhraní vynucuje:

* **Příkazy jsou asynchronní.** `submit_order` vrací stav `SUBMITTED`, ne
  hotové plnění. Reálný broker potvrzuje se zpožděním a částečně plní.
* **Pravdu má broker, ne naše evidence.** Po výpadku spojení se stavy
  rozejdou a smiřovat se musí podle brokera.
* **`client_order_id` je povinná praxe.** Jediná obrana proti duplicitnímu
  odeslání po restartu nebo timeoutu.

## Architektura

```
data ──► strategie ──► risk manager ──► broker ──► portfolio
 │        signál         velikost        plnění      hotovost,
 │        BUY/SELL       + stopy         + poplatky   pozice
 │                          ▲
 │                    vol targeting
 │                    zlomkové Kelly
 │                                                       │
 └────────► metriky ◄── validace ◄──────────────────────┘
            Sharpe      walk-forward
            maxDD       deflated Sharpe, PBO
```

| Modul | Odpovědnost |
|-------|-------------|
| `data.py` | yfinance / CSV / generátor, cache, úprava o splity |
| `indicators.py` | SMA, EMA, RSI, ATR, Bollinger, Donchian |
| `strategies.py` | Rozhodnutí *jestli* držet pozici |
| `risk.py` | Rozhodnutí *za kolik* a *kdy to utnout* |
| `sizing.py` | Cílování volatility, Kellyho kritérium |
| `ensemble.py` | Diverzifikace, vážení (1/N, inverse-vol, HRP), diagnostika |
| `stats.py` | PSR, deflated Sharpe, PBO, MinTRL, bootstrap |
| `validation.py` | Walk-forward, mřížka parametrů, matice pro PBO |
| `portfolio.py` | Účetnictví hotovosti, pozic a obchodů |
| `execution.py` | Poplatky a skluz |
| `backtest.py` | Událostní smyčka bar po baru |
| `live.py` | Paper trading nad stejnou smyčkou, stav na disku |
| `metrics.py` | Výkonnostní metriky a varování |
| `benchmark.py` | Pasivní držení jako laťka |
| `brokers/` | Připojení k brokerovi |
| `cli.py` | Příkazová řádka |

### Proč se tomuhle backtestu dá věřit víc než většině

* **Plnění za otevírací cenu dalšího baru.** Signál vzniká na závěrečné ceně,
  ale za tu už nikdo neobchoduje.
* **Strategie vidí jen aktuální bar.** `BarContext` neobsahuje zbytek série,
  takže nakouknout dopředu nejde ani omylem. Ověřuje to test, který spustí
  stejný běh nad zkrácenými a plnými daty a porovná obchody v překryvu.
* **Mezery přes noc se plní poctivě.** Otevře-li trh pod stopem, plní se za
  otevírací cenu, ne za stop.
* **Poplatky a skluz jsou zapnuté i ve výchozím nastavení.**
* **Otevřené pozice se na konci uzavřou**, ať se ztráta neschová mimo statistiku.
* **Živý běh používá stejnou smyčku jako backtest.** Test ověřuje, že
  krokovaný paper trading dá přesně stejné obchody jako jednorázový backtest.

## Strategie

| Název | Typ | Chování |
|-------|-----|---------|
| `tsmom` | časové momentum | Nejlépe doložený jednoduchý signál (Moskowitz, Ooi, Pedersen 2012 — 58 futures, 25+ let). Jediný parametr. |
| `sma_crossover` | trend following | Nízká úspěšnost, velké zisky z mála obchodů. Trpí v postranním trhu. |
| `rsi_reversion` | návrat k průměru | Vysoká úspěšnost, malé zisky proti občasné velké ztrátě. Má filtr trendu. |
| `donchian_breakout` | momentum | Jádro Turtle systému. Dva parametry, tedy odolnější vůči přeoptimalizování. |

Realita trend-followingu: výnosy jsou v posledním desetiletí výrazně nižší
než historicky a u nejrychlejších variant se pokles Sharpe podařilo
**statisticky prokázat**. Signál není mrtvý, ale čekat čísla z osmdesátých
let by byla chyba.

### Vlastní strategie

```python
from trading.strategies import Strategy, BarContext, REGISTRY
from trading.models import Signal, SignalType, HOLD
from trading import indicators as ind

class MojeStrategie(Strategy):
    name = "moje"

    def prepare(self, df):
        df = df.copy()
        df["ema"] = ind.ema(df["close"], 50)   # jen indikátory hledící do minulosti
        return df

    @property
    def warmup(self) -> int:
        return 51

    def on_bar(self, ctx: BarContext) -> Signal:
        if not ctx.in_position and ctx.close > ctx.row["ema"]:
            return Signal(SignalType.ENTER_LONG, 1.0, "nad EMA50")
        if ctx.in_position and ctx.close < ctx.row["ema"]:
            return Signal(SignalType.EXIT_LONG, 1.0, "pod EMA50")
        return HOLD

REGISTRY[MojeStrategie.name] = MojeStrategie
```

Strategie neřeší velikost pozice ani stopy — to dělá risk manager.

## Řízení rizika

Velikost pozice se počítá **od rizika, ne od kapitálu**:

```
riskovaná částka = kapitál × risk_per_trade × [vol targeting] × [Kelly]
vzdálenost stopu = ATR × stop_loss_atr_mult
počet kusů       = riskovaná částka / vzdálenost stopu   (omezeno expozicí a hotovostí)
```

| Přepínač | Výchozí | Význam |
|----------|---------|--------|
| `--risk-per-trade` | 0.01 | Podíl kapitálu riskovaný na obchodu |
| `--stop-atr` | 2.0 | Stop-loss v násobcích ATR |
| `--target-atr` | — | Take-profit v násobcích ATR |
| `--trailing-atr` | — | Posuvný stop, jde jen nahoru |
| `--vol-target` | — | Cílová roční volatilita equity křivky |
| `--kelly` | — | Zlomek Kelly (0.25 = čtvrtinové) |
| `--max-position` | 0.25 | Strop expozice na titul |
| `--max-positions` | 5 | Počet současných pozic |
| `--max-daily-loss` | 0.03 | Denní stop účtu |
| `--max-drawdown` | 0.20 | Kill-switch |
| `--reentry-cooldown` | 0 | Pauza po vyražení stopem |

Zvyšovat `risk-per-trade` nad 2 % znamená, že série deseti ztrát — a ta
přijde — ukousne pětinu účtu.

## Data

Výchozí zdroj je Yahoo Finance přes `yfinance`, s CSV cache v `data/cache/`.
Ceny se upravují o splity a dividendy.

```bash
python -m trading backtest --csv-dir moje_data --symbols AAPL   # čte moje_data/AAPL.csv
python -m trading backtest --synthetic --symbols X              # generovaná řada, offline
```

Syntetická data slouží k testům a demu. Backtest na náhodné procházce
nevypovídá **nic** o kvalitě strategie, jen o tom, že kód běží.

## Testy

```bash
python -m pytest
```

353 testů. Těžiště není na pokrytí řádků, ale na vlastnostech, jejichž
porušení by tiše vyrobilo falešně ziskový systém:

* zkrácení dat nesmí změnit dřívější rozhodnutí (lookahead bias)
* krokovaný živý běh musí dát stejné obchody jako jednorázový backtest
* mezera přes noc se nesmí plnit za stop
* na čistém šumu musí PBO vyjít kolem 0,5 a deflated Sharpe zamítnout
* Kelly musí na známých případech dát učebnicové hodnoty
* na ostrý účet nesmí projít příkaz bez explicitního povolení

Tři skutečné chyby, které testy odhalily během vývoje (a mají teď regresní test):
Sharpe 5,7·10¹⁵ u konstantní equity křivky (std vyšla 1,7·10⁻¹⁸ místo nuly),
duplicitní časové razítko na konci equity křivky rozbíjející walk-forward,
a efektivní počet sázek počítaný přes vlastní báze, která u blízkých vlastních
čísel není určená jednoznačně.

## Jak s tím pracovat rozumně

1. **Backtest na dlouhé historii** — aspoň 10 let, ať to zažije pokles i růst.
2. **`validate`, ne `backtest`.** In-sample číslo neříká nic. Poctivě přiznejte
   počet vyzkoušených variant — podhodnotit ho znamená obelhat sám sebe.
3. **Srovnejte s benchmarkem.** Strategie, která nepřekoná pasivní držení,
   není k ničemu.
4. **Diverzifikujte přes rodiny signálů, ne přes parametry.** `ensemble`
   ukáže, jestli jste přidali sázku, nebo jen kopii.
5. **Paper trading aspoň několik měsíců.**
6. **Reálné peníze až nakonec**, v částce, kterou unesete ztratit, a s
   `GuardedBroker` a nízkými limity.

## Co projekt neumí

* Jen long pozice, žádné shorty, páka ani deriváty.
* Jen denní a hodinové bary, ne intradenní tick data.
* Bez daňové evidence.
* Adaptér na Alpaca **nebyl ověřen proti živému API** — prostředí, kde vznikl,
  nemá na burzovní servery přístup. Vyzkoušejte si ho na paper účtu dřív, než
  přes něj pošlete první příkaz.

## Právní a daňové upozornění

Tohle je software, ne investiční doporučení. Autor není finanční poradce.
Obchodování může vést ke ztrátě části nebo celé investované částky.

V ČR je každý prodej cenného papíru zdanitelnou transakcí; časový test tří let
u automatizovaného systému prakticky nikdy neprojde. Před nasazením s reálnými
penězi si to ověřte u daňového poradce.

## Zdroje

* Sullivan, Timmermann, White (1999), *Data-Snooping, Technical Trading Rule
  Performance, and the Bootstrap*, Journal of Finance
* Bailey, López de Prado (2014), *The Deflated Sharpe Ratio*
* Bailey, Borwein, López de Prado, Zhu (2015), *The Probability of Backtest
  Overfitting*
* Harvey, Hoyle, Korgaonkar, Rattray, Sargaison, Van Hemert (2018),
  *The Impact of Volatility Targeting*, Journal of Portfolio Management
* Moskowitz, Ooi, Pedersen (2012), *Time Series Momentum*, JFE
* Asness, Moskowitz, Pedersen (2013), *Value and Momentum Everywhere*
* López de Prado (2016), *Building Diversified Portfolios that Outperform
  Out of Sample* (HRP)
* Grinold (1989), *The Fundamental Law of Active Management*
