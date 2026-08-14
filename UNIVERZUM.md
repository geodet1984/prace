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

| US (demo) | UCITS alternativa | Poznámka |
|-----------|-------------------|----------|
| SPY | CSPX / VUSA | S&P 500 |
| IWM | ZPRS / IUSN | small cap |
| EFA | IWDA (svět) nebo VEUR + VJPN | ex-US samostatně neexistuje 1:1 |
| EEM | EIMI / VFEM | rozvíjející se trhy |
| TLT | IDTL | US státní 20+ let |
| IEF | IDTM | US státní 7–10 let |
| LQD | LQDE | korporátní investment grade |
| HYG | IHYU | high yield |
| TIP | ITPS | inflačně vázané |
| GLD | SGLN / IGLN | fyzické zlato, formálně ETC ne UCITS |
| SLV | ISLN | fyzické stříbro, ETC |
| DBC | ICOM | široké komodity |
| VNQ | IWDP | nemovitosti rozvinutých trhů |
| UUP | — | přímý ekvivalent chybí, vynechte |

**Tyhle tickery si ověřte, než na nich něco postavíte.** Označení se liší
podle burzy, na které je fond kotovaný (LSE, Xetra, Amsterdam), a pro
`yfinance` potřebují příponu — `CSPX.L`, `IDTL.L`, `EIMI.L` pro Londýn,
`.DE` pro Xetra, `.AS` pro Amsterdam. Nejjistější je vzít ISIN ze stránek
svého brokera a ticker dohledat podle něj.

Rychlá kontrola, co se vůbec stáhne:

```bash
python -m trading fetch --symbols CSPX.L,IDTL.L,EIMI.L --start 2015-01-01
```

Co projde, vypíše se s počtem barů. Co ne, skončí jako `ERROR` a ze seznamu
to vyhoďte.

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
