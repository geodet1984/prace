# Kde jsme a co bylo rozhodnuto

Předávací poznámka pro pokračování v lokální relaci Claude Code. Kód je
v repozitáři, tenhle soubor drží *proč* — rozhodnutí, která by se z kódu
těžko rekonstruovala, a věci, které ještě nejsou vyřešené.

## Zadání a jak se posunulo

Původní otázka zněla, jestli jde postavit aplikaci, která hlídá trh a je
**vždy v zisku**. Nejde. Cíl se proto přeformuloval na *kladnou očekávanou
hodnotu při propadu, který se dá ustát* — a na nástroje, které umí strategii
**zamítnout**.

Kapitál: začínalo se na 500 USD, ustálilo se na **1 000 USD** pro paper fázi.

## Rozhodnutí, která platí

| Co | Jak | Proč |
|---|---|---|
| Strategie | `tsmom` (12měsíční momentum) | Nejlépe doložený jednoduchý signál, jediný parametr, ~30 obratů ročně |
| Univerzum | 14 ETF napříč třídami aktiv | Breadth, ne víc indikátorů. Detaily v UNIVERZUM.md |
| Hlášení | **týdně**, pátek po zavření | Hodinové by u Sharpe 0,8 ukázalo ztrátu 806× ročně, týdenní 24× |
| Frekvence obchodů | denní bary, ~30 obratů/rok | Nákladový strop je ~80 obratů/rok, a nezávisí na velikosti účtu |
| Broker | Trading 212 nebo Alpaca | IBKR má minimum 1 $/příkaz, což na malém účtu žere ~20 % ročně |
| Zprávy | jen jako **blackout**, ne jako signál | Blackout nic nepředpovídá, sentiment ano — a to nejde ověřit |

## Čísla, ze kterých ta rozhodnutí vyšla

* **Nákladový strop ~80 obratů/rok** platí pro každou velikost účtu — pozice
  i náklady škálují spolu. Intradenní obchodování (10×/hod) by znamenalo
  8 190 obratů = **205 % kapitálu ročně** na samotném skluzu.
* **Daňový strop ~1 200 USD kapitálu.** V ČR je příjem z prodeje cenných
  papírů osvobozený jen do úhrnu **hrubých prodejů** 100 000 Kč za rok.
  Tříletý časový test u držby ~35 dní neprojde nikdy.
* **Za měsíc se nedozvíte nic.** Očekávaný zisk 8 USD proti rozptylu ±68 USD.
  Smysl prvního měsíce je provozní ověření, ne výnos.
* **Sharpe 0,8 potřebuje 2,4 roku** jen na prokázání, že je nad nulou.

## Co je ověřené a co ne

Ověřené testy (398 testů, `python -m pytest`):

* žádný pohled do budoucnosti — zkrácení dat nezmění dřívější obchody
* frekvence spouštění nemění výsledek — denní, týdenní i měsíční dá totéž
* mezera přes noc se plní za open, ne za stop
* na čistém šumu vyjde PBO kolem 0,5 a deflated Sharpe zamítne
* na ostrý účet neprojde příkaz bez explicitního povolení
* dashboard do stavu účtu nezapíše ani při čtení a zapisující HTTP metody
  odmítá — přehled není druhá cesta, jak hnout účtem

**Neověřené:**

* stahování z Yahoo Finance a adaptér na Alpaca — prostředí, kde projekt
  vznikal, nemělo přístup na burzovní servery
* UCITS tickery v UNIVERZUM.md — vypsané podle názvů, ne ověřené proti burze
* termíny CPI v `data/udalosti.csv` jsou **neúplné** (viz níže)

## Chyby, které se cestou našly

Každá má regresní test. Stojí za přečtení, protože ukazují, jaký typ chyby
v tomhle projektu hrozí.

1. **Sharpe 5,7·10¹⁵** u konstantní equity křivky — std z plovoucí čárky
   vyšla 1,7·10⁻¹⁸ místo nuly.
2. **Duplicitní časové razítko** na konci equity křivky rozbíjelo skládání
   out-of-sample úseků ve walk-forwardu.
3. **Efektivní počet sázek** přes vlastní báze kovarianční matice není
   u blízkých vlastních čísel jednoznačný.
4. **Akumulovaný stav se neukládal** — pauza po stop-lossu se při každém
   spuštění vynulovala, takže denní běh dával jiné obchody než týdenní.
5. **Poplatky vstupní nohy se po restartu ztrácely** — obchody vykazovaly
   zhruba poloviční náklady.
6. **Malý účet tiše nefungoval** — se 100 USD a výchozím minimem pozice
   50 USD engine 1061× zamítl signál a report ukázal nulu bez vysvětlení.
7. **Zamítnutí hlásilo špatnou příčinu** — hláška „pozice pod minimem“
   padala i tehdy, když pozici osekala vyčerpaná hotovost. Ze 6 221
   zamítnutí na paper účtu jich takhle bylo 4 200 a diagnostika kvůli
   tomu dvakrát hledala chybu v nastavení minima místo v nasazení
   kapitálu (účet běžel na 99,8 %). Sizing teď sleduje, který ze tří
   stropů skutečně rozhodl.
8. **`risk_per_trade` se dá nastavovat, aniž by cokoli dělal** — velikost
   pozice je minimum ze tří stropů a nad hranicí
   `max_position_pct × (vzdálenost stopu / cena)` vždycky ořízne expozice.
   V `tydenni-report.sh` je hranice 0,72 %, nastaveno je 1,5 %, takže
   hodnoty 1,5 % i 5 % dávají bit po bitu shodný výsledek. Není to vada
   výpočtu — pořadí pojistek je záměr — ale mlčet o tom znamená nechat
   člověka ladit parametr, který je v jeho konfiguraci mrtvý.

## Co zbývá

**Blízké:**

* [ ] Doplnit chybějící termíny CPI do `data/udalosti.csv` — únor, březen,
      květen, září, říjen 2026 a celý 2027. Použijte
      `./scripts/stahni-kalendar.sh`, nebo opište z bls.gov. Nedopočítávat:
      data za listopad 2026 vycházejí 18. prosince, tedy mimo obvyklý druhý
      týden, takže pravidelnost neexistuje.
* [ ] Ověřit, že se všech 14 tickerů skutečně stáhne (`./scripts/start-demo.sh`)
* [ ] Nechat běžet měsíc na papíře, pak se podívat na `~/paper.log`

**Otevřené otázky, na které zatím nepadla odpověď:**

* [ ] **Přeměřit `sma_crossover`, `rsi_reversion`, `donchian_breakout`
      a jejich směs na evropském univerzu.** Odloženo, ne zamítnuto.
      Změřený je zatím jen `tsmom` a ten prohrál na všech třech univerzech
      (US, EU, krypto) — viz UNIVERZUM.md. Tohle je poslední krok, který
      může změnit odpověď na otázku, jestli je co automatizovat.

      **Rozpočet pokusů je 672** (624 z diagnostiky + 48 z měření EU) a do
      `deflated_sharpe_ratio` patří celý, ne od nuly. Nález „jedna
      strategie vypadá dobře" bez obhájeného deflated Sharpe není nález,
      ale očekávatelná náhoda — na 672 pokusech obzvlášť.
* [ ] HTML report do prohlížeče místo terminálu — nabízeno, nerozhodnuto
* [ ] Přidat BTC-USD a ETH-USD k univerzu (viz KRYPTO.md; s `--costs binance`
      a `--periods-per-year 365`)

**Známá omezení, která nikdo neopravil:**

* Výběr pozic je **abecední**, ne podle síly signálu. Při 14 titulech
  a 10 slotech to zatím nevadí, protože `tsmom` vrací sílu vždy 1,0.
* Blackout u pomalé strategie skoro nefiruje — na 2 000 barech odmítl jeden
  vstup u `tsmom`, čtyři u `sma_crossover`. U rychlejších strategií bude
  význam větší.

## Jak pokračovat lokálně

```bash
git clone https://github.com/geodet1984/prace.git
cd prace && git checkout claude/stock-trading-app-3vvbh8
claude          # Claude Code si sám načte CLAUDE.md i tenhle soubor
```

Sedm pravidel, která v projektu nesmí spadnout, je v `CLAUDE.md`. Nejsou to
stylistické preference — každé odpovídá způsobu, jak vyrobit strategii, která
vydělává jen v tabulce.
