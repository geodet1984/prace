# Brokeři: náklady a možnost automatizace

Rešerše k **15. 8. 2026**. Ceníky se v roce 2026 měnily často, takže
**údaje mají krátkou trvanlivost** — před rozhodováním ověřte u zdroje.

Není to investiční doporučení. Jsou to ceníkové a technické vlastnosti.

## Jak číst spolehlivost údajů

Tenhle dokument vznikl z rešerše, jejíž první verze obsahovala
**vymyšlená čísla** — agent je po sobě odvolal a část zjištění se ukázala
jako nepodložená. Proto je u každého oddílu uvedeno, odkud pochází:

* ★★★ **ověřeno vlastním stažením a přečtením** primárního dokumentu
* ★★ převzato z rešerše, která k tomu **stáhla a rozparsovala** ceník
* ★ **jediný zdroj, neověřeno** — ber jako vodítko, ne jako fakt
* Oddíl „Co bylo odvoláno" na konci drží, co se ukázalo jako nepodložené,
  aby to někdo za půl roku nevytáhl znovu jako fakt.

---

## Automatizace: rozhoduje smlouva, ne technika

### Trading 212 algoritmické obchodování zakazuje ★★★

Ověřeno vlastním stažením `trading212.com/legal-documentation/API-Terms_EN.pdf`
a extrakcí textu. **Čl. 4.2(a)** doslova:

> „You are expressly prohibited from using our API for Algorithmic Trading
> purposes or for providing any commercial services, such as agent,
> brokerage, and/or asset management services, regardless of whether such
> services are legally authorised"

**Čl. 11** definuje pojem tak, že sedí přesně na to, co by tenhle projekt
dělal:

> „Algorithmic Trading means any kind of trading in Instruments where
> a computer algorithm automatically determines individual parameters of
> Orders, such as whether to initiate the Order, the timing of execution,
> price or quantity of the Order, or how to manage the Order after its
> submission, with limited to no human intervention"

API tedy technicky funguje a v ostrém režimu umí market, limit, stop
i stop-limit — ale použít ho k tomu, k čemu bychom ho chtěli, smluvně
nelze. Kde leží hranice u varianty „algoritmus navrhne, člověk odklikne",
podmínky neupřesňují; to je dotaz na jejich compliance, písemně.

### DEGIRO automatizaci zakazuje taky ★★

Helpdesk DEGIRO: *„we don't offer an API"*, *„you cannot automate trades
or use trading bots"*, a *„external solutions, such as API wrappers or
custom scripts"* označuje za porušení podmínek. Neoficiální
`degiro-connector` existuje, ale hrozí zablokováním účtu.

### XTB API zrušilo ★★

Nápověda XTB: *„API access is no longer available. The service was
discontinued on March 14, 2025."* Náhrada pro retail není.

### Přehled

| Broker | API pro pokyny | Demo / paper | Automatizace dle podmínek |
|---|---|---|---|
| **Saxo Bank** | ano, OpenAPI ★★ | **SIM zdarma i bez živého účtu** ★★ | bez nalezeného zákazu |
| **Interactive Brokers** | ano ★★ | paper účet ★★ | bez nalezeného zákazu |
| Trading 212 | ano, beta ★★ | ano ★★ | **ZAKÁZÁNA** ★★★ |
| DEGIRO | ne ★★ | — | **ZAKÁZÁNA** ★★ |
| XTB | zrušeno 14. 3. 2025 ★★ | — | — |
| Portu, Fondee, Indigo | veřejné API nenalezeno ★ | — | — |
| Fio, ČS/George, KB, RB, Patria | veřejné API nenalezeno ★ | — | — |

„Bez nalezeného zákazu" není totéž co „povoleno" — u Saxo a IBKR jsme
zákaz nenašli, ale podmínky jsme celé nečetli. **Před stavbou adaptéru
se zeptejte písemně.**

### Trading 212 API — technické detaily ★★

Kdyby se to někdy hodilo pro neautomatizované použití: dokumentace je na
`docs.trading212.com/api`. Endpointy **nejsou idempotentní** — *„Sending
the same request multiple times may result in duplicate orders"*, tedy
pozor na opakování při chybě. Pokyny jen v hlavní měně účtu, objednávky
podle hodnoty nejdou (jen podle `quantity`). Sekce Pies je označená
„Deprecated". Rate limit: market 50/min, limit a stop 1 za 2 s.

---

## Robo-platformy ★★

Ceníky stažené a rozparsované jako PDF.

| | Portu | Fondee | Indigo |
|---|---|---|---|
| Provozovatel | WOOD Retail Investments | Direct Fondee | **Patria Finance** (ČSOB) |
| Ročně | 1,00 % do 0,5 mil. Kč | 1,089 % | 0,83 % + 21 % DPH = **1,00 %** |
| Min. vklad | 500 Kč | 500 Kč | 100 Kč |
| DIP | 0,50 % | 0,605 % | nepotvrzeno |
| TER ETF | nezveřejněn | **0,15 %** | nezveřejněn |
| Vlastní výběr titulů | ano (Brokerage) | ne | ne |
| Účinnost ceníku | 17. 6. 2026 | 15. 8. 2026 | 1. 6. 2025 |

Portu má pásma podle objemu (1,00 / 0,80 / 0,60 / 0,40 %) a slevu za
fixaci: 5 let → 20 %, 15 let → 40 %. **Nevztahuje se na DIP ani dětské
účty** a předčasný výběr znamená zpětné doúčtování.

**Portu Brokerage** (jednotlivé tituly) má poplatek 0,4 % ale **minimum
39 Kč / 2 EUR / 2 USD** — na tisícikoruně 3,9 %. Plus úschova 0,05 %
ročně a FX 0,4 %. Na malé částky se nehodí a Portu na to upozorňuje samo.
Převod mimo WRI se **18. 7. 2026 změnil** z 1 000 Kč za ISIN na 0,4 %,
min. 2 000 / max. 10 000 Kč.

Fondee jako jediná publikuje TER (0,15 %). Poplatky se **hradí v EUR**,
což je u korunového investora další konverze, jejíž cena vyčíslená není.

**Patria** (jiný produkt než Indigo): FX **1,5 %**, pravidelné investice
do ETF 0,8 %, DIP s 50% slevou.

---

## Náklady u malých částek ★

**Jediný zdroj, neověřeno.** Nákup evropského ETF za 2 000 Kč, provize
i měnová konverze:

| | Náklad |
|---|---|
| Revolut — investiční plán ETF | 0 % |
| Trading 212 | 0,15 % |
| Česká spořitelna (akce) | 0,35 % |
| XTB | 0,50 % |
| Revolut — běžný pokyn | 1,25 % |
| DEGIRO | 1,46 % |
| IBKR | ~5,7 % |

U 20 000 Kč se rozdíly smrsknou na desetiny procenta.

**Co je na tom podložené lépe** (★★): u IBKR platí `max(3 EUR;
0,05 % z objemu)`, tedy zlom na 6 000 EUR. XTB má 0 % do 100 000 EUR
měsíčního obratu, nad to 0,2 % min. 10 EUR, a FX 0,5 %. Trading 212 má
FX 0,15 % a vklady kartou zdarma jen do kumulativních 2 000 EUR, pak
0,7 % (převodem zdarma).

**Změřeno na naší strategii** (★★★, vlastní běh): nákladový preset
`retail_eu` s minimem 2 USD na příkaz srazil výsledek na **−5,76 %
ročně** a odvedl 420 USD na provizích z tisícidolarového účtu za osm let.
U téhle velikosti účtu není nulová provize úspora, ale podmínka.

---

## Co ceníky zamlčují

* **Měnovou marži nezveřejňuje žádná z českých bank.** U tisícikorunových
  nákupů může převýšit provizi. Největší mezera celé rešerše. ★
* **Revolut**: ochrana investic jen do 22 000 EUR (litevský systém),
  frakční podíly nelze převést jinam. ★
* **Česká spořitelna**: 0,35 % bez minima je marketingová akce do
  31. 12. 2026, ne ceníková změna — ceník od 1. 2. 2026 minima pořád
  obsahuje. Odchod 800 Kč za ISIN. ★
* **IBKR má dvě varianty konverze** ★★: automatická kolem 0,03 % spreadu
  bez provize, manuální IDEALPRO 0,20 bps s minimem 2 USD. Zlom kolem
  6 700 USD — pod tím je automatická levnější.
* **Frakce u konkrétních UCITS ETF na IBKR jsou nejednoznačné** ★★:
  tisková zpráva z 2022 mluví o evropských akciích *i ETF*, dnešní stránka
  u Evropy zmiňuje jen akcie, seznam je neveřejný. Jediný spolehlivý test
  je zkusit to na paper účtu.

## Regulatorní rámec ★★

Kvůli **PRIIPs** (nařízení EU 1286/2014) si evropský retail nekoupí
americké ETF — američtí správci nevydávají KID a bez něj je broker prodat
nesmí. Náhrady jsou v [UNIVERZUM.md](UNIVERZUM.md).

Krypto po skončení přechodného období MiCA (1. 7. 2026) smí sloužit
českým klientům jen držitel povolení CASP. Binance stáhla žádost
24. 6. 2026. Licenci mají mimo jiné Coinbase a Kraken.

---

## Co bylo odvoláno

Tohle stálo v první verzi dokumentu a **nemá zdroj** — agent, který to
dodal, si to vymyslel a sám to pak odvolal. Vypsané je proto, aby to
někdo nevytáhl znovu jako fakt:

* **Tvrzení, že paper účet IBKR plní jen z top-of-book a stopy vždy
  simuluje.** Znělo věrohodně a přesně sedělo na bod 3 z `CLAUDE.md`,
  což je právě důvod, proč prošlo. Jestli to tak je, nevíme — a **stojí
  za to si to ověřit**, protože kdyby to platilo, paper účet by tu chybu
  neodhalil.
* Celý ceník **Saxo** (0,08 % min. 1 USD, custody 0,15 % + DPH, FX
  0,25 %, rate limity, IČ organizační složky).
* Celý ceník **DEGIRO** (handling 1 EUR, connectivity 2,50 EUR, transfer
  out 20 EUR za pozici) i tvrzení o „čtyřech vnitřních rozporech" na jejich
  webu.
* Tabulky **Fio, KB, Raiffeisenbank** včetně sazeb a dat účinnosti.
* **XTB**: srážková daň 30 % / 35 % na omnibus účtu bez možnosti uplatnit
  smlouvu o zamezení dvojího zdanění.
* **IBKR**: Tiered minimum 1,25 EUR, strop 1 % z obchodu, minimum
  20 000 EUR na IDEALPRO, ceny tržních dat.
* Všechny „DNS a HTTP sondy" dokazující, že robo-platformy nemají API.
  Že ho nemají, je pravděpodobné, ale tvrdý důkaz chybí.

## Nedotažené otázky

1. Měnové marže u českých bank.
2. Ceníky Saxo, DEGIRO, Fio, ČS, KB a RB — po odvolání zůstala díra.
3. Zda paper účet IBKR simuluje plnění optimisticky (viz výše).
4. Frakce u konkrétních UCITS ETF na IBKR.
5. Zda Alpaca přijme retailového klienta z ČR — seznam zemí neveřejný.
