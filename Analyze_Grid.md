# Analyze_Grid.md

<thinking>
**Architektura & Problém Inventory targetu (Ghost Levels) v Grid botu:**
Grid bot vytváří síť (GridLevels) poptávek a nabídek. Pokud mu dojde kapitál (USD nebo BTC), současný algoritmus dělá 2 fatální chyby:
1. **Sell-Side Shrink (Skokové odkládání):** Místo aby umístil plnohodnotné (plně naceněné) orders na první nejvíce rentabilní úrovně a zbytek úrovní skryl (odložil do paměti jako Ghost levels), "rozkrájí" fyzický wallet-balance plošně mezi VŠECHNY levely (`w_btc_raw / n_sell_levels`). To způsobuje "skokové" mikro-fylly, protože limit order je nesmyslně malý a netrefuje se do market-maker patternů.
2. **Buy-Side Blind Fire:** V Buy-smyčce kompletně chybí `Wallet Guard`. Pálí limity za všechny peníze a spoléhá, že burza hodí Margin chybu. To ničí API stabilitu a způsobuje restrikce rate-limitu.

Řešení: **Sequential Wallet Allocation (Ghost Level Architecture)**
Změníme obě for-smyčky na sekvenční odpočet. Projdeme úrovně a plníme je 100% z požadovaného `qty`, dokud nám nedojde alokovaný zůstatek peněženky L0 stavu (95% volného kapitálu). Jakmile zůstatek nestačí ani na minimální order-size (15000 microBTC = 0.00015 BTC), smyčka se přeruší (`break`). Zbytek hladin zůstane pouze ve vrstvě Orákula - vzniknou čistokrevné "Ghost Levely", aniž by alokovaly paměť nebo zahlcovaly API.
</thinking>

### 1. BUG HUNTING & SECURITY:
- **Zero Buy-Side Wallet Guard:** L0 jádro Gridu nechalo úplně otevřenou bránu na nákupní straně. Posílalo objednávky do zdi a spoléhalo na `INSUFFICIENT BALANCE` z Bitfinexu. API mohlo při velké síti zablokovat spojení.
- **Micro-order Fragmentation:** Dělení celkového BTC walletu počtem Sell-levelů produkuje miniaturní limity a degraduje spread-harvesting. 

### 2. ZOMBIE CODE PURGE (Zero-Tolerance):
- Aktuálně Grid bloky neobsahují explicitní `// DISABLE` zombie prvky, ale pročišťujeme celou proměnnou a blok `max_sell_per_level`, který představoval zombie logiku skokového štěpení.

### 3. LAYER-SPECIFIC OPTIMIZATION:
- Zavedení `remaining_usd` a `remaining_btc` do lokálního stacku ve fází WS zprávy. Obě smyčky teď obsahují dynamický `O(1)` výpočet frakčních přebytků a instantní `break` jakmile dojdou peníze. Tím se drasticky omezí počet odeslaných (a zbytečných) objednávek na burzu a tím i websocket ping/pong latence. Optimalizováno vše přes Zero-Cost abstrakci `FixedPrice`.

### 4. SURGICAL CODE REFACTOR:
Vykonávám fix uvnitř `grid/src/main.rs`. Odstraním celou starou `for price in &buys` a `for price in &sells` divizi a implementuji Sequential Allocation O(N) buffer logic se zachováním Zero-Alloc API BitfinexVenue.
