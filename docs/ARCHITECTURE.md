# Rust HFT Bitcoin Obchodní Bot
**Automatická aktualizace po každých 5 minutách**

**VÝZKUM VYSOKOFREKVENČNÍHO (HFT) OBCHODOVÁNÍ V MILISEKUNDÁCH PRO MĚNOVÝ PÁR USD/BTC NA BURZE BITFINEX, IMPLEMENTOVÁNO IN-MEMORY POMOCÍ JAZYKA RUST**

## ÚVOD DO MIKROSTRUKTURY TRHU A VYSOKOFREKVENČNÍHO OBCHODOVÁNÍ KRYPTOMĚN
Vysokofrekvenční obchodování (High-Frequency Trading, HFT) představuje v současném finančním ekosystému dominantní paradigma, které je definováno využitím sofistikovaných algoritmických systémů schopných analyzovat tržní data a odesílat obchodní příkazy v časových horizontech zlomků sekund. Zatímco na tradičních akciových trzích (TradFi) se latence, měřená jako doba od přijetí tržního signálu po odeslání příkazu (tzv. tick-to-trade), pohybuje v sub-mikrosekundových hodnotách díky přímému přístupu na trh (DMA) a masivnímu nasazení hardwarových akcelerátorů typu FPGA, kryptoměnové burzy vykazují zcela specifické a odlišné charakteristiky. Trhy s digitálními aktivy operují v neustálém režimu dvaceti čtyř hodin denně, sedm dní v týdnu, a čelí extrémní volatilitě, fragmentované likviditě napříč desítkami heterogenních burz a neustále se vyvíjejícím technologickým pravidlům.

Burza Bitfinex, jež byla založena v roce 2012, patří historicky k nejlikvidnějším platformám pro obchodování kryptoměn a představuje preferovanou destinaci pro institucionální investory a kvantitativní fondy. Měnový pár BTC/USD (potažmo BTC/USDt) zde vykazuje jedny z nejvyšších objemů obchodování na světě, přičemž denní objemy dosahují stovek milionů dolarů a otevřený úrok v derivátových kontraktech překračuje stovky milionů. Pro úspěšné nasazení HFT strategie na tomto konkrétním trhu je naprosto kritické minimalizovat veškeré prodlevy v softwarovém i hardwarovém řetězci.

V posledních letech se jazyk Rust etabloval jako primární a preferovaná volba pro vývoj moderních in-memory HFT systémů, a to i na úkor tradičního C++. Rust nabízí exekuční výkon plně srovnatelný s jazyky C a C++, avšak díky svému striktnímu modelu vlastnictví (ownership) a výpůjček (borrowing) garantuje paměťovou bezpečnost a eliminuje riziko souběhu dat (data races) bez jakékoliv nutnosti použití garbage collectoru. Absence nedeterministických pauz, které jsou typické pro systémy s automatickou správou paměti (jako je Java nebo Go), činí jazyk Rust naprosto ideálním nástrojem pro prostředí, kde je predikovatelnost latence (zejména tail latency na úrovni 99. a 99,9. percentilu) mnohem důležitější metrikou než pouhá průměrná propustnost. Analýza architektury in-memory HFT systému vyvinutého v jazyce Rust a přizpůsobeného specifikům burzy Bitfinex vyžaduje hluboký ponor do optimalizace síťové vrstvy, lock-free datových struktur, zero-copy zpracování zpráv a absolutního vyloučení zbytečných paměťových alokací na kritické cestě.

## GEOGRAFICKÁ TOPOLOGIE, LATENCE A KOLOKACE INFRASTRUKTURY
Latence je v kontextu vysokofrekvenčního obchodování definována jako zpoždění mezi aktualizací tržní ceny a automatizovaným odesláním obchodního příkazu. Úspěšnost HFT strategií, mezi něž patří zejména market making (tvorba trhu) nebo statistická arbitráž, závisí primárně na schopnosti reagovat na mikrozměny v knize objednávek dříve než konkurence. Jakékoliv minimální zpoždění má přímý a fatální vliv na míru vyplnění objednávek (fill rates) a celkovou profitabilitu obchodního modelu.

### DEKOMPOZICE LATENCE V KRYPTOMĚNOVÉM PROSTŘEDÍ
Analýza reálných produkčních dat HFT systémů napojených na moderní kryptoměnové burzy ukazuje, že zpoždění v kryptoměnách je o několik řádů vyšší než v tradičních financích. Zpracování příkazu na samotné burze a síťový přenos internetem tvoří absolutní většinu celkového zpoždění. Empirická měření výkonu optimalizovaných systémů komunikujících s burzami odhalují komplexní profil zpoždění, který lze analyzovat prostřednictvím následujících metrik:

| Fáze zpracování v HFT pipeline | Průměrná latence (Mean) | Medián | 95. percentil (p95) | 99. percentil (p99) |
| :--- | :--- | :--- | :--- | :--- |
| Market Data Feed (Příjem tržních dat) | 15.8 ms | 12.4 ms | 34.6 ms | Neuvádí se |
| Zpracování burzou (Exchange Processing) | 4.7 ms | - | 8.9 ms | - |
| Síťový přenos - Send (Cesta k burze) | 18.5 ms | - | 31.2 ms | - |
| Síťový přenos - Receive (Cesta od burzy) | 18.3 ms | - | 30.1 ms | - |
| Softwarová serializace | 0.8 ms (800 µs) | - | 1.2 ms (1200 µs) | - |
| **Celková exekuce objednávky (Tick-to-trade)** | **42.3 ms** | **39.1 ms** | **67.8 ms** | **89.2 ms** |

Prezentovaná data jednoznačně indikují, že ačkoliv je průměrná celková latence v řádu desítek milisekund, její variance (jitter) je enormní. Tento jev ukazuje na výrazný vliv asynchronní architektury samotné burzy a nestabilitu veřejného internetového směrování. Z tohoto důvodu musí být interní "latency budget" (rozpočet latence) samotného HFT enginu v jazyce Rust dimenzován do oblasti jednotek mikrosekund. Špičkové Rust systémy operují s interním časem pod pět mikrosekund pro kompletní tick-to-trade cyklus (vyjma sítě).

**Ideální rozložení času v plně optimalizovaném Rust enginu (Target Latency Budget):**
| Fáze softwarového zpracování | Časový rozpočet | Popis operace v jazyce Rust |
| :--- | :--- | :--- |
| Kernel bypass | 0.5 µs | Přenos z NIC do user-space přes DPDK / Solarflare |
| Dekódování knihy | 1.0 µs | Parsování binárních nebo optimalizovaných tržních dat |
| Signálová matematika | 1.5 µs | Běh logiky obchodní strategie a evaluace |
| Risk management | 1.0 µs | Validace limitů, prevence sebe-obchodování (STP) |
| Serializace | 0.5 µs | Sestavení zprávy a odeslání objednávky (FIX/binární) |
| **Celkem (Tick-to-Trade)** | **4.5 µs** | **Kompletní interní zpracování** |

### KOLOKACE A VLIV CLOUDOVÉ INFRASTRUKTURY
Tradiční HFT firmy investují obrovské prostředky do fyzické kolokace serverů přímo ve stejných datových centrech. V kryptoměnovém světě se firmy spoléhají především na optimalizaci v rámci globálních veřejných cloudů (např. AWS region us-east-1 v Severní Virginii). Přímé připojení na platformy umožňující fixní křížové propojení (cross-connects) dokáže drasticky snížit 99. percentil síťové latence.

## ARCHITEKTURA A OMEZENÍ BITFINEX API V2
### ÚZKÁ HRDLA REST API A PREFEROVÁNÍ WEBSOCKET PROTOKOLU
REST API je pro HFT neadekvátní kvůli režii HTTP a přísným limitům (10–90 požadavků/min). Překročení vede k chybě `{"error": "ERR_RATE_LIMIT"}` a blokaci na 60s. Pro Rust systém je nezbytné WebSocket API v2:
- **Veřejný endpoint:** `wss://api-pub.bitfinex.com/ws/2` (tržní data)
- **Autentizovaný endpoint:** `wss://api.bitfinex.com/ws/2` (příkazy, stav účtu)

**Parametry WebSocket připojení:**
| Parametr | Hodnota / Limit |
| :--- | :--- |
| Autentizovaná spojení | max. 5 za 15 sekund |
| Veřejná spojení | max. 20 za minutu |
| Kanály na jedno spojení | max. 25 kanálů |
| Penalizace za překročení | Blokace 15 až 60 s |

Systém musí implementovat **exponential backoff** a reagovat na informační kódy údržby:
- **Kód 20060:** Zahájení údržby (pozastavit aktivitu)
- **Kód 20061:** Ukončení údržby (reinicializace)

## SYNCHRONIZACE A REKONSTRUKCE IN-MEMORY KNIHY OBJEDNÁVEK
Bitfinex poskytuje dvě úrovně detailu:
1.  **Price-aggregated (P0-P3):** Agregovaná data o objemu na cenových hladinách.
2.  **Raw Order Book (R0):** Kritické pro HFT, formát "Market-by-Order" (L3 data) s unikátními ID objednávek. Umožňuje sledování pozice ve frontě (queue position).

Data jsou doručována jako JSON pole: kladné množství = **bids** (nákup), záporné množství = **asks** (prodej).

### OCHRANA INTEGRITY DAT: SEKVENČNÍ ČÍSLA A CRC32 KONTROLNÍ SOUČTY
1.  **Sekvenční čísla (Flag 65536):** Inkrementální ID pro detekci ztracených zpráv.
2.  **Kontrolní součty (Flag 131072):** CRC32 checksum pokrývající nejlepších 25 bids a 25 asks.

HFT engine must dynamicky konstruovat řetězec ve formátu `cena:množství:cena:-množství...` a porovnávat lokální CRC32 s hodnotou z burzy. Neshoda (`CHECKSUM_FAILED`) vyžaduje zrušení in-flight objednávek a stažení nového snapshotu.

## ZPRACOVÁNÍ EXEKUCÍ A SPRÁVA OBCHODNÍCH PŘÍKAZŮ
Základní příkazy: `on` (New), `ou` (Update), `oc` (Cancel).
Klíčový pro HFT je **Order Multi-OP (`ox_multi`)**, umožňující atomický **cancel-replace** v jediném TCP rámci.

**Struktura ox_multi:**
| Typ operace | Klíč v JSON | Parametry | Využití |
| :--- | :--- | :--- | :--- |
| Nová objednávka | "on" | type, symbol, price, amount, flags | Nová kotace |
| Zrušení objednávky | "oc" | id nebo cid | Odstranění z fronty |
| Hromadné zrušení | "oc_multi" | Pole ID | Rychlé stažení likvidity |
| Úprava objednávky | "ou" | id, price, amount | Realokace pozice |

Identifikace probíhá přes **cid (Client Order ID)** generované botem. Speciální modifikátory (flags) zahrnují **Self-Trade Prevention** a **Post-Only** (pro maker fee rebate).

## ARCHITEKTURA SÍŤOVÉ VRSTVY A "THREAD-PER-CORE\" MODEL V JAZYCE RUST
Tradiční asynchronní modely (Tokio) s "work-stealing\" jsou nevhodné kvůli cache misses a false sharingu. Standardem je **Thread-per-Core** (vlákno na jádro) a využití **io_uring**.

### MECHANIKA IO_URING A BĚHOVÁ PROSTŘEDÍ MONOIO A GLOMMIO
`io_uring` využívá sdílené kruhové buffery (Submission/Completion Queue), čímž eliminuje syscalls (zejména v módu `SQPOLL`).
- **Monoio / Glommio:** Běhová prostředí nad `io_uring` s afinitou k jádrům.
- **TLS:** Knihovny jako `websockets-monoio` s `rustls` (zero-copy friendly). Experimentálně se využívá **Kernel TLS (kTLS)**.

### INTER-THREAD KOMUNIKACE (SPSC A LMAX DISRUPTOR)
Komunikace mezi vlákny (Network -> OrderBook -> Strategy -> Logger) probíhá přes **lock-free fronty**:
- **SPSC (Single-Producer, Single-Consumer):** Např. knihovna `Nexus` (latence < 100 ns na p99).
- **LMAX Disruptor:** Pro broadcast událostí do více modulů (risk management, strategie).

## IN-MEMORY DATOVÉ MODELY A BEZ-ZÁMKOVÁ (LOCK-FREE) SYNCHRONIZACE
Standardní zámky (Mutex) selhávají kvůli kompetici. Optimální je lock-free návrh s atomickými operacemi.
- **Propustnost:** Až 19 milionů operací/s na "hot spot" cenové hladině.
- **ABA problém / Use-After-Free:** Řešeno přes **Epoch-Based Memory Reclamation** (knihovna `crossbeam-epoch`).

### PAMĚŤOVÉ USPOŘÁDÁNÍ A OPTIMALIZACE PŘÍSTUPU (AOS VS SOA)
Procesory načítají data v 64-byte cache lines.
- **AoS (Array of Structs):** Neefektivní, načítá nepotřebná data (ID, timestamp při výpočtu objemu).
- **SoA (Struct of Arrays):** Separátní pole pro ceny, objemy atd.
    - **Efektivita:** 100% využití cache line.
    - **SIMD:** Umožňuje vektorizaci (AVX2/AVX-512) – zpracování 4-8 operací v jednom cyklu.

## ZERO-COST ABSTRAKCE, SPRÁVA PAMĚTI A JSON PARSOVÁNÍ
### VLASTNÍ ALOKÁTORY PAMĚTI (CUSTOM ALLOCATORS) A MEMORY POOLS
Dynamická alokace na \"hot path\" je nepřípustná. Využívají se **Memory Pools / Slabs**:
- **nexus-slab:** Předalokované, page-aligned bloky.
- **mlock:** Zamknutí paměti proti swapování.
- **GlobalAlloc:** Vlastní implementace pro monitoring skrytých alokací.

### ZERO-COPY DESERIALIZACE A SIMD ZRYCHLENÍ
Standardní `serde_json` je pomalé (klonování dat).
- **Zero-Copy:** Mapování `&str` přímo na I/O buffer (lifetimes `'a`).
- **simd-json / sonic-rs:** Vektorizované parsování.

**Benchmark (citm_catalog):**
| Parser | Rychlost | Technologie |
| :--- | :--- | :--- |
| serde_json | ~ 1.888 ms | Sekvenční stavový stroj |
| simd-json | ~ 1.763 ms | C++ port, vektorizace |
| **sonic-rs** | **~ 0.821 ms** | **Paralelizovaná analytika (50% redukce)** |

## PERZISTENCE, ŽURNÁLOVÁNÍ A DETERMINISTICKÁ OBNOVA
Pro Disaster Recovery se využívají **Memory-Mapped Files (`memmap2`)**.
- **Append-only log:** Zápis do RAM pole, OS asynchronně flushuje \"dirty pages\" na disk.
- **Kódování:** Binární **SBE (Simple Binary Encoding)**.
- **Výkon:** Overhead < 1 µs, p99.9 < 1.5 µs.
- **Deterministic Replay Engine:** Blesková rekonstrukce stavu z logu po restartu.

## KONVERGENCE K DECENTRALIZOVANÝM APLIKACÍM A ENERGETICKÝM GRIDŮM
Fúze HFT a energetiky (**Microgrids**). Rust nody mohou přímo řídit spotřebu těžebních ASIC zařízení na základě arbitrážních příležitostí na Bitfinexu v reálném čase.

## SYNTÉZA ZÁVĚRŮ K IN-MEMORY ARCHITEKTUŘE
Boj o latenci probíhá v průsečíku hardwaru, Linuxu a Rustu.
- **Cíl:** Interní latency budget < 5 µs.
- **Klíčové prvky:** Thread-per-Core, io_uring, Lock-Free SoA struktury, SIMD, Zero-allocation, Zero-copy parsování, CRC32 integrita a Mmap perzistence.

---

### CITOVANÁ DÍLA
1. High-Frequency Trading Infrastructure: Technical Guideline - B2BROKER (2026)
2. Design and Implementation of a Low-Latency HFT System - Jung-Hua Liu (2026)
3. Bitfinex Official Site
5. High-Frequency Trading Systems in Rust - Google Books
7. How to Build a Lock-Free Data Structure in Rust - OneUptime (2026)
9. Announcing Nexus: Low-latency primitives - Rust-lang Forum
11. High-Frequency Trading in Crypto: Latency, Infrastructure, and Reality - Medium
12. What is HFT and how can we implement it in Rust - Dev.to
15. Geographic Latency in Crypto - Eli Williams
16. Bitfinex API Documentation (Requirements and Limitations)
19. Bitfinex OrderBook API Guide - GitHub Gist
20. Bitfinex API: Order Books & Checksums - Bitfinex Blog
29. Order Multi-OP - Bitfinex Reference
33. Bitfinex FIX API - Axon Trade
35. Introducing Glommio - Datadog
41. Rust + io_uring + ktls - Amos Wenger (P99 CONF 2024)
43. OrderBook-rs - GitHub (Joaquin Bejar)
48. AoS vs SoA - Stack Overflow / Wikipedia
53. Zero-Copy Data Parsing in Rust - Leapcell
62. sonic-rs - GitHub (Cloudwego)
64. Memory-Mapped Files in Rust - OneUptime
66. Chronicle-Queue - GitHub (OpenHFT)
70. Dynamic Adaptive Cross-Chain Trading - PMC (2020)
