# Trading — backtest a paper trading akciových strategií

Nástroj, který sleduje akciový trh, vyhodnocuje stav titulů podle definované
strategie a obchoduje je s řízeným rizikem — nejdřív na historii (backtest),
potom nanečisto proti živým cenám (paper trading).

## Nejdřív to důležité

**Aplikace, která je vždy v zisku, neexistuje a tenhle projekt jí není.**

Cena v okamžiku nákupu je jediná známá věličina, budoucí cena je náhodná.
Žádný signál nemá stoprocentní úspěšnost, a kdyby ji měl, kapitál by do něj
natekl a výhoda by zmizela. „Nikdy neprodělat" jde jen předstírat tím, že se
ztrátové pozice nikdy neuzavřou — účetně pak ztráta není vidět, reálně máte
zamrzlý kapitál a rostoucí riziko. Přesně na tomhle stojí martingale
a průměrování ztrát a přesně tak vypadají účty, které skončí na nule.

Reálný cíl je **kladná očekávaná hodnota při propadu, který se dá ustát**.
Proto tenhle projekt počítá poplatky, skluz, drawdown a Sharpe ratio, srovnává
každou strategii s pasivním držením a upozorní vás, když je vzorek obchodů
příliš malý na jakýkoli závěr.

## Instalace

```bash
pip install -r requirements.txt      # pandas, numpy, yfinance
pip install -e ".[dev]"              # volitelně: editovatelná instalace + pytest
```

Python 3.10 nebo novější.

## Rychlý start

Bez připojení k internetu, na vygenerovaných datech:

```bash
python -m trading compare --symbols X --synthetic
```

```
strategie                     výnos    CAGR  Sharpe   maxDD  obch.  úspěš.     PF
─────────────────────────────────────────────────────────────────────────────────
donchian_breakout(entry=2     -0.3%   -0.1%   -0.02   -6.1%     15     20%   0.96
rsi_reversion(period=14,      -1.5%   -0.5%   -1.06   -1.5%      1      0%   0.00
sma_crossover(fast=20, sl     12.0%    4.0%    0.90   -5.0%      6     33%   4.14
buy & hold (benchmark)        22.3%    7.4%    0.40  -42.5%      1    100%      ∞
```

Takhle vypadá typický poctivý výsledek: aktivní strategie **nepřekonala**
pasivní držení ve výnosu, ale prošla obdobím s pětiprocentním propadem místo
dvaačtyřicetiprocentního. To je celá podstata řízení rizika — a zároveň důkaz,
že se nedá čekat zázrak.

Na reálných datech:

```bash
python -m trading backtest --symbols AAPL,MSFT --start 2015-01-01 --trades
python -m trading compare  --symbols SPY --start 2010-01-01 --costs ibkr
```

## Příkazy

| Příkaz     | Co dělá |
|------------|---------|
| `backtest` | Spustí jednu strategii na historii a vypíše kompletní report |
| `compare`  | Porovná všechny strategie mezi sebou a s pasivním držením |
| `signals`  | Ukáže aktuální signály pro zadané tituly, nic neobchoduje |
| `paper`    | Posune paper trading účet o nová data a uloží stav |
| `fetch`    | Stáhne a nacachuje data z Yahoo Finance |

Nápověda ke každému: `python -m trading <příkaz> --help`.

### Paper trading

Spouštějte jednou denně po zavření burzy:

```bash
python -m trading paper --symbols AAPL,MSFT,SPY \
    --strategy sma_crossover --start 2022-01-01 \
    --state data/paper_state.json
```

Stav účtu (hotovost, pozice, čekající příkazy, historie obchodů) se ukládá do
JSON a při dalším spuštění se načte, takže se každý bar zpracuje právě jednou.
Signál z dnešního close se provede za zítřejší otevírací cenu — stejně jako
v backtestu.

Jako cron:

```cron
0 23 * * 1-5  cd /cesta/k/projektu && python -m trading paper --symbols SPY >> paper.log 2>&1
```

## Jak to funguje

```
data ──► strategie ──► risk manager ──► broker (paper) ──► portfolio
 │        signál         velikost         plnění za          hotovost,
 │        BUY/SELL       pozice,          open dalšího       pozice,
 │                       stop-loss        baru + poplatky    obchody
 │                                                              │
 └──────────────────────► metriky ◄────────────────────────────┘
                     Sharpe, maxDD, profit factor
```

| Modul | Odpovědnost |
|-------|-------------|
| `data.py` | Načítání dat (yfinance, CSV, generátor), cache, úprava o splity |
| `indicators.py` | SMA, EMA, RSI, ATR, Bollinger, Donchian |
| `strategies.py` | Rozhodnutí *jestli* držet pozici |
| `risk.py` | Rozhodnutí *za kolik* a *kdy to utnout* |
| `portfolio.py` | Účetnictví hotovosti, pozic a obchodů |
| `execution.py` | Poplatky a skluz |
| `backtest.py` | Událostní smyčka bar po baru |
| `live.py` | Paper trading nad stejnou smyčkou, se stavem na disku |
| `metrics.py` | Výkonnostní metriky a varování |
| `benchmark.py` | Pasivní držení jako srovnávací laťka |

### Proč se tomuhle backtestu dá věřit víc než většině

Nejčastější způsob, jak vyrobit strategii, která vydělává jen v tabulce, je
nechat ji nakouknout do budoucnosti. Tady tomu brání konstrukce:

* **Plnění za otevírací cenu dalšího baru.** Signál vzniká na závěrečné ceně,
  ale za tu už nikdo neobchoduje. Plnění „za dnešní close" je nejčastější
  zdroj falešných zisků.
* **Strategie vidí jen aktuální bar.** `BarContext` neobsahuje ani zbytek
  série, takže nakouknutí dopředu není možné ani omylem. Test
  `test_future_bars_do_not_change_past_decisions` to ověřuje tak, že spustí
  stejný běh nad zkrácenými a plnými daty a porovná obchody v překryvu.
* **Mezery přes noc se plní poctivě.** Když trh otevře pod stop-lossem, plní
  se za otevírací cenu, ne za stop. Opačný předpoklad systematicky nadhodnocuje
  výsledky.
* **Poplatky a skluz jsou zapnuté i ve výchozím nastavení.**
* **Otevřené pozice se na konci uzavřou,** aby se nerealizovaná ztráta
  neschovala mimo statistiku obchodů.
* **Živý běh používá stejnou smyčku jako backtest.** Test
  `test_incremental_steps_match_one_shot_backtest` ověřuje, že krokovaný paper
  trading dá přesně stejné obchody jako jednorázový backtest na téže historii.

## Řízení rizika

Velikost pozice se počítá **od rizika, ne od kapitálu**: nejdřív kolik peněz je
přijatelné ztratit, pak kam patří stop, a teprve z toho vyjde počet kusů.

```
riskovaná částka = kapitál × risk_per_trade
vzdálenost stopu = ATR × stop_loss_atr_mult
počet kusů       = riskovaná částka / vzdálenost stopu     (omezeno expozicí a hotovostí)
```

| Přepínač | Výchozí | Význam |
|----------|---------|--------|
| `--risk-per-trade` | 0.01 | Podíl kapitálu riskovaný na jednom obchodu |
| `--stop-atr` | 2.0 | Stop-loss v násobcích ATR |
| `--target-atr` | — | Take-profit v násobcích ATR |
| `--trailing-atr` | — | Posuvný stop, který jde jen nahoru |
| `--max-position` | 0.25 | Strop expozice na jeden titul |
| `--max-positions` | 5 | Počet současně otevřených pozic |
| `--max-daily-loss` | 0.03 | Denní stop účtu |
| `--max-drawdown` | 0.20 | Kill-switch: propad, po kterém se přestává vstupovat |
| `--reentry-cooldown` | 0 | Pauza v barech po vyražení stopem |

Zvyšovat `risk-per-trade` nad 2 % znamená, že série deseti ztrát — a ta přijde —
ukousne pětinu účtu.

## Strategie

| Název | Typ | Chování |
|-------|-----|---------|
| `sma_crossover` | trend following | Nízká úspěšnost, velké zisky z mála obchodů. Trpí v postranním trhu. |
| `rsi_reversion` | mean reversion | Vysoká úspěšnost, malé zisky proti občasné velké ztrátě. Má filtr trendu. |
| `donchian_breakout` | momentum | Jádro Turtle systému. Jen dva parametry, tedy odolnější vůči přeoptimalizování. |

Parametry se předávají přes `--param`:

```bash
python -m trading backtest --strategy sma_crossover --param fast=10 --param slow=40
```

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

## Data

Výchozí zdroj je Yahoo Finance přes `yfinance`, s CSV cache v `data/cache/`.
Ceny se upravují o splity a dividendy (`Adj Close` se promítne do celého baru).

Alternativy, když stahování není k dispozici:

```bash
python -m trading backtest --csv-dir moje_data --symbols AAPL   # čte moje_data/AAPL.csv
python -m trading backtest --synthetic --symbols X              # generovaná řada
```

CSV musí mít v prvním sloupci datum a dál sloupce `open, high, low, close, volume`.

Syntetická data slouží k testům a demu. Backtest na náhodné procházce
nevypovídá **nic** o kvalitě strategie, jen o tom, že kód běží.

## Testy

```bash
python -m pytest
```

168 testů. Těžiště není na pokrytí řádků, ale na vlastnostech, jejichž porušení
by tiše vyrobilo falešně ziskový systém: absence pohledu do budoucnosti,
plnění za správnou cenu, konzistence hotovosti a P/L, shoda živého běhu
s backtestem.

## Jak s tím pracovat rozumně

1. **Backtest na dostatečně dlouhé historii.** Aspoň 10 let, ať to zažije
   pokles i růst. Pod 30 obchodů je výsledek náhoda a nástroj vás na to upozorní.
2. **Test mimo optimalizované období.** Vylaďte parametry na letech 2010–2018
   a pak je bez úprav spusťte na 2019–2024. Když výsledek spadne, laděním jste
   jen popsali minulost.
3. **Srovnejte s benchmarkem.** `compare` ho počítá vždy. Strategie, která
   nepřekoná pasivní držení, není k ničemu.
4. **Paper trading aspoň několik měsíců.** Živá data přinesou mezery, výpadky
   a chování, které v historii nebylo.
5. **Reálné peníze až nakonec, a v částce, kterou unesete ztratit.**

## Co projekt zatím neumí

* **Nenapojuje se na brokera.** Není tu adaptér na Alpaca ani Interactive
  Brokers, takže odsud žádný příkaz na burzu neodejde. Přidání je přímočaré —
  `execution.py` a `live.py` jsou na to připravené — ale zatím to tu není.
* Jen long pozice, žádné shorty, páka ani deriváty.
* Jen denní a hodinové bary, ne intradenní tick data.
* Bez daňové evidence.
* Nezohledňuje dividendy jako samostatný peněžní tok (jsou v upravených cenách).

## Právní a daňové upozornění

Tohle je software, ne investiční doporučení. Autor není finanční poradce.
Obchodování s cennými papíry může vést ke ztrátě části nebo celé investované
částky.

V ČR je každý prodej cenného papíru zdanitelnou transakcí; časový test tří let
u automatizovaného systému prakticky nikdy neprojde a hodnotový limit na
osvobození je potřeba hlídat. Před nasazením s reálnými penězi si to ověřte
u daňového poradce.
