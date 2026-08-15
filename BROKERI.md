# Brokeři: náklady a možnost automatizace

Rešerše k **15. 8. 2026**, primárně z ceníků a smluvních podmínek.
Ceníky se v roce 2026 měnily často (ČS 18. 5., Portu 17. 6. a 18. 7.,
Patria 1. 7., Fio 1. 7. a 17. 8., Fondee 15. 8., RB 1. 1., DEGIRO 1. 1.),
takže **údaje mají krátkou trvanlivost** — před rozhodováním ověřte.

Není to investiční doporučení. Jsou to ceníkové a technické vlastnosti.

## Automatizace: jen dva zbývají

Rozhoduje smluvní povolení, ne existence API. Dva brokeři mají API
a přitom automatizaci nezakazují:

| Broker | API | Demo / paper | Automatizace dle podmínek |
|---|---|---|---|
| **Saxo Bank** | OpenAPI (REST + streaming, OAuth 2.0) | **SIM zdarma i bez živého účtu** | bez zákazu |
| **Interactive Brokers** | TWS API, Web API, FIX | **paper zdarma, bez financování** | bez zákazu |
| Trading 212 | REST, beta, live umí limit/stop/stop-limit | `demo.trading212.com` | **ZAKÁZÁNA** |
| DEGIRO | — | — | **ZAKÁZÁNA** včetně wrapperů |
| XTB | zrušeno 14. 3. 2025 | — | — |
| Portu, Fondee, Indigo | ne | — | — |
| Fio, ČS/George, KB, RB, ČSOB, Patria | ne | — | — |

**Trading 212**, API Terms čl. 4.2(a): *„You are expressly prohibited from
using our API for Algorithmic Trading purposes…"*, přičemž čl. 11 definuje
algoritmické obchodování jako to, kde algoritmus určuje parametry pokynů
„s omezeným nebo žádným lidským zásahem". Není to šedá zóna. Kde přesně
leží hranice (algoritmus navrhne, člověk odklikne) neupřesňují — dotaz na
jejich compliance, písemně.

**DEGIRO** zakazuje i „external solutions, such as API wrappers or custom
scripts" a označuje to za porušení podmínek. Neoficiální `degiro-connector`
je aktivní, ale jeho použití hrozí zablokováním účtu.

### Past v paper účtu IBKR

Paper účet plní **jen z top-of-book a stopy jsou vždycky simulované**.
To je přesně ten optimistický předpoklad, proti kterému stojí bod 3
v [CLAUDE.md](CLAUDE.md): mezera přes noc se plní za open, ne za stop.
**Papírový účet u IBKR tuhle chybu neodhalí, protože ji sám dělá.**

### Trading 212 API — technické detaily, kdyby se to někdy hodilo

Dokumentace je na `docs.trading212.com/api` (staré apiary je 404).
Endpointy **nejsou idempotentní**: *„Sending the same request multiple
times may result in duplicate orders."* Pozor na retry logiku. Pokyny jen
v hlavní měně účtu, objednávky podle hodnoty přes API nejdou (jen podle
`quantity`), real-time data nejsou. Sekce Pies je označená „Deprecated".

## Náklady u malých částek

Nákup evropského ETF za **2 000 Kč**, provize i měnová konverze:

| | Náklad |
|---|---|
| Revolut — investiční plán ETF | **0 %** |
| Trading 212 | 0,15 % |
| Česká spořitelna (akce do 31. 12. 2026) | 0,35 % |
| XTB | 0,50 % |
| Revolut — běžný pokyn | 1,25 % |
| DEGIRO | 1,46 % |
| Fio (Xetra) | 4,78 % |
| Česká spořitelna mimo akci | 4,84 % |
| IBKR | ~5,7 % |

U 20 000 Kč se rozdíly smrsknou na 0,1–0,6 % a přestává na tom záležet.
**U malých částek rozhoduje minimální poplatek na pokyn, ne sazba.**

Změřeno na strategii: nákladový preset `retail_eu` (min. 2 USD na příkaz)
srazil výsledek na **−5,76 % ročně** a odvedl 420 USD na provizích
z tisícidolarového účtu za osm let. Broker s nulovou provizí není u téhle
velikosti účtu úspora, ale podmínka.

## Co se v ceníku nepíše

* **Měnovou marži nezveřejňuje ani Fio, ani ČS, ani KB, ani RB.** U částek
  v tisícikorunách může snadno převýšit samotnou provizi. Největší mezera
  celé rešerše.
* **Revolut**: ochrana investic jen do **22 000 EUR** (litevský systém),
  **frakční podíly nelze převést jinam vůbec** — odchod znamená prodat.
  Vklady kartou zdarma jen do kumulativních 2 000 EUR, pak 0,7 %.
* **Česká spořitelna**: 0,35 % bez minima je **marketingová akce do
  31. 12. 2026**, ne ceníková změna; ceník od 1. 2. 2026 minima pořád
  obsahuje. Od února zdražila úschova na 0,08 % ročně. Odchod **800 Kč
  za ISIN**.
* **XTB**: akcie na omnibus účtu, plátce daně nezná daňovou rezidenci
  klienta a strhává **nejvyšší srážkovou daň — 30 % u US, 35 % u českých**.
  Smlouvy o zamezení dvojího zdanění se neuplatní.
* **Saxo**: custody 0,15 % ročně + dánská DPH ≈ 0,1875 %, odpouští se při
  aktivaci půjčování cenných papírů. Nápověda tvrdí, že minimum custody
  není, sekundární zdroje uvádějí 5 EUR měsíčně — neověřeno.
* **DEGIRO**: web si odporuje sám se sebou ve čtyřech bodech (handling ČR,
  fair-use, EUR/CZK konverze, kotace PSE). Ověřit u podpory.

## Robo-platformy

| | Portu | Fondee | Indigo |
|---|---|---|---|
| Provozovatel | WOOD Retail Investments | Direct Fondee | **Patria Finance** |
| Ročně | 1,00 % (do 0,5 mil.) | 1,089 % | 1,00 % |
| Min. vklad | 500 Kč | 500 Kč | 100 Kč |
| TER ETF | nezveřejněn | **0,15 %** | nezveřejněn |
| Vlastní výběr ETF | ano | ne | ne |
| API | ne | ne | ne |

Portu má slevu za fixaci (15 let → 40 %), ale nevztahuje se na DIP.
Portu **Brokerage** (jednotlivé tituly) má minimum 39 Kč na pokyn, tedy
3,9 % na tisícikoruně — na malé částky se nehodí a Portu na to upozorňuje
samo.

## Regulatorní rámec

Kvůli **PRIIPs** (nařízení EU 1286/2014) si evropský retail nekoupí
americké ETF — američtí správci nevydávají KID a bez něj je broker prodat
nesmí. Není to volba platformy, je to právní povinnost. Náhrady jsou
v [UNIVERZUM.md](UNIVERZUM.md).

Krypto po skončení přechodného období MiCA (1. 7. 2026) smí sloužit
českým klientům jen držitel povolení CASP. **Binance stáhla žádost
24. 6. 2026** a v EU nepřijímá nové spotové příkazy. Licenci mají mimo
jiné **Coinbase** (oficiální Python SDK, sandbox) a **Kraken** (levnější,
ale spotový sandbox nemá).

## Nedotažené otázky

1. Měnové marže u českých bank — nikdo je nezveřejňuje.
2. Rozpor u ČS: tisková zpráva ruší minima od 18. 5. 2026, ceník je má dál.
3. Minimum custody u Saxo: nápověda „žádné", sekundární zdroje 5 EUR/měs.
4. Frakce u konkrétních UCITS ETF na IBKR — pravidla to připouštějí, seznam
   je neveřejný. Jediný spolehlivý test je zkusit to na paper účtu.
5. Zda Alpaca přijme retailového klienta z ČR — seznam zemí neveřejný.
