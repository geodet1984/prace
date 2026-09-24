# Kontext projektu pro Claude Code

Backtest, statistická validace a paper trading akciových strategií.
Čeština v komentářích, dokumentaci i výstupech je záměr — držte se jí.

## Spuštění

```bash
python3 -m venv .venv && source .venv/bin/activate    # macOS: PEP 668 vyžaduje venv
pip install -r requirements.txt
python -m pytest                                       # 358 testů, běží ~40 s
python -m ruff check .
```

Demo paper účet na jedno spuštění: `./scripts/start-demo.sh`

## Co v tomhle projektu nesmí spadnout

Tohle nejsou stylistické preference. Každý bod odpovídá způsobu, jak vyrobit
strategii, která vydělává jen v tabulce — a každý má test, který to hlídá.

1. **Žádný pohled do budoucnosti.** Strategie dostane přes `BarContext` jen
   aktuální bar. Indikátory počítejte výhradně dozadu (`rolling`, `ewm`,
   `shift(1)`). Hlídá `test_future_bars_do_not_change_past_decisions`
   a `test_indicators_do_not_depend_on_future_bars`.

2. **Příkazy se plní za otevírací cenu dalšího baru**, nikdy za close, na
   kterém signál vznikl. Za ten už nikdo neobchoduje.

3. **Mezera přes noc se plní za open, ne za stop.** Otevře-li trh pod
   stop-lossem, dostanete horší cenu. Opačný předpoklad systematicky
   nadhodnocuje výsledky.

4. **Poplatky a skluz jsou zapnuté i ve výchozím nastavení.** Backtest bez
   nákladů je marketingový materiál, ne test.

5. **Všechen nasčítaný stav musí přežít restart.** Když do enginu přidáte
   něco, co se hromadí v čase (počitadlo, EWMA, historie obchodů), **musí**
   to přibýt do `live.py` v `save()` i `load()` a zvednout `STATE_VERSION`.
   Jinak se to při každém spuštění tiše vynuluje a funkce přestane fungovat,
   aniž by to bylo poznat. Hlídá `test_result_does_not_depend_on_how_often_you_run_it`.

6. **Konstantní řada nemá Sharpe.** Porovnávejte směrodatnou odchylku proti
   `MIN_STD`, ne proti nule — plovoucí čárka dá u konstantní řady 1e-18
   a podíl z toho udělá Sharpe v řádu 10¹⁵.

7. **Počet vyzkoušených variant se počítá.** Každé kolo ladění parametrů je
   další pokus a patří do `deflated_sharpe_ratio(n_trials=...)`. Podhodnotit
   ho znamená obelhat sám sebe.

## Architektura

Rozhodnutí *kdy* obchodovat (`strategies.py`) je oddělené od *za kolik*
a *kdy to utnout* (`risk.py`). Strategie nikdy neřeší velikost pozice.

Backtest i živý běh sdílejí jednu smyčku — `BacktestEngine.process_bar`.
Druhá implementace pro živý běh by se dřív nebo později začala chovat jinak
než ta testovaná.

| Modul | Odpovědnost |
|-------|-------------|
| `data.py` | yfinance / CSV / generátor, cache, úprava o splity |
| `strategies.py` | Signály. `tsmom`, `sma_crossover`, `rsi_reversion`, `donchian_breakout` |
| `risk.py` | Velikost pozice od rizika, ATR stopy, kill-switch |
| `sizing.py` | Cílování volatility, Kellyho kritérium |
| `ensemble.py` | Diverzifikace, vážení, efektivní počet sázek |
| `stats.py` | PSR, deflated Sharpe, PBO, MinTRL, bootstrap |
| `validation.py` | Walk-forward, mřížka parametrů |
| `backtest.py`, `live.py` | Smyčka a paper trading se stavem na disku |
| `brokers/` | Rozhraní k brokerovi + pojistky |

## Testy

Píšou se proti **vlastnostem**, ne proti řádkům. Dobrý test v tomhle
projektu odpovídá konkrétní chybě, kterou je snadné udělat, a má
v docstringu napsáno jaké.

Statistické testy průměrujte přes víc seedů. PBO z jediné datové sady má
obrovský rozptyl (na šumu 0,19 až 0,73) a tvrzení z jednoho běhu by byl
přesně ten druh závěru, proti kterému je celý `stats.py`.

## Čemu v tomhle projektu nevěřit

* **Syntetická data** (`data.synthetic`) slouží k testům a demu. Backtest na
  náhodné procházce nevypovídá nic o kvalitě strategie.
* **Adaptér na Alpaca** nebyl ověřen proti živému API.
* **Výběr pozic je abecední.** Když je kandidátů víc než volných slotů,
  engine je obsadí podle pořadí symbolů, ne podle síly signálu. Viz
  UNIVERZUM.md.

## Kde jsme

Rozhodnutí, otevřené otázky a seznam chyb, které se cestou našly, jsou
v [STAV.md](STAV.md). Přečtěte si ho dřív, než začnete něco měnit — drží
*proč*, které by se z kódu rekonstruovalo těžko.

## Práce s repozitářem

Vývojová větev: `claude/stock-trading-app-3vvbh8`. Commit messages česky,
v imperativu, s vysvětlením *proč*, ne jen *co*.

Před commitem: `python -m pytest && python -m ruff check .`
