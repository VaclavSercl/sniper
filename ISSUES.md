# 🐺 SNIPER ARMADA — Roadmap & Issues Tracker
Fáze: Izolovaná Optimalizace (Level 5 Autonomy Phase)

## Záznamník Otevřených Úkolů:

### 📝 Issue #11: HYDRA – The Market Maker (Micro-Structure Optimization)
**Status:** Plánováno
**Cíl:** Optimalizace šířky gridu a "Tick-to-Trade" latence.

**Operační zadání pro AI Architekta:**
⚠️ **POZNÁMKA PRO AI:** Než začneš analyzovat, VYŽÁDEJ SI OD UŽIVATELE aktuální zdrojový kód `hydra/src/main.rs` (případně přidružené soubory logiky).
**Analýza:** Hydra momentálně používá statický (nebo hrubě odhadovaný) model rozestupu limitních objednávek na základě volatility. Musíme provést audit toho, jak rychle reaguje na změnu Trending skóre od Orákula.
**Vylepšení:**
- **Dynamic Skewing:** Naučit Hydru naklánět své objednávky (např. dávat větší objem na stranu Bid, pokud Orákulum detekuje rostoucí VPIN, ale ještě není Storm).
- **Cancel-Replace Latency:** Zkontrolovat, zda Hydra používá efektivní "Amend" příkazy (pokud je burza podporuje), nebo zda dělá pomalý cyklus Cancel -> Create.

---

### 📝 Issue #12: MOONSHOT – The Aggressive Taker (Slippage Matrix Integration)
**Status:** Plánováno
**Cíl:** Záchrana zisků před zborcením orderbooku.

**Operační zadání pro AI Architekta:**
⚠️ **POZNÁMKA PRO AI:** Než začneš analyzovat, VYŽÁDEJ SI OD UŽIVATELE aktuální zdrojový kód `moonshot/src/main.rs` a případně sdílenou strukturu čtení Orderbooku.
**Analýza:** Moonshot pálí do trhu market/agresivní limit ordery při detekci Toxic Storm. Nyní ale nevidí, jak hluboko se jeho objednávka propadne (Slippage).
**Vylepšení:**
- **Deep Book Parsing:** Napojit Moonshota na vektorizovaná L2 data, která sbírá Orákulum.
- **Slippage Matrix:** Implementovat funkci, která před výstřelem spočítá exaktní průměrnou cenu (Weighted Average Price) pro celý zamýšlený objem objednávky, nejen pro Top Bid/Ask.
- **Adaptive Volume:** Pokud je skluz příliš velký, Moonshot nesmí obchod zrušit úplně, ale musí snížit objem na rentabilní úroveň.

---

### 📝 Issue #13: GRID – The Ranging Harvester (Inventory Risk Management)
**Status:** Plánováno
**Cíl:** Zabránit akumulaci jednosměrného rizika v dlouhých Ranging trzích.

**Operační zadání pro AI Architekta:**
⚠️ **POZNÁMKA PRO AI:** Než začneš analyzovat, VYŽÁDEJ SI OD UŽIVATELE aktuální zdrojový kód `grid/src/main.rs`.
**Analýza:** Grid boti mají tendenci hromadit zásoby (Inventory) na jedné straně, pokud trh pomalu driftuje jedním směrem, aniž by spustil Toxic Storm.
**Vylepšení:**
- **Inventory Skew Penalty:** Pokud Grid nakoupil už 3 úrovně dolů a neprodal nic nahoru, jeho další "Buy" limity se musí posunout dál (nebo snížit objem).
- **Armada Sync:** Napojit Grid bota na celkové PnL flotily, aby věděl, kdy je čas uzavřít mřížku s mírnou ztrátou, než riskovat velký propad.

---

### 📝 Issue #14: NEXUS – The Arbitrageur (Cross-Venue Latency Audit)
**Status:** Plánováno
**Cíl:** Absolutní atomická exekuce na L1 burzách.

**Operační zadání pro AI Architekta:**
⚠️ **POZNÁMKA PRO AI:** Než začneš analyzovat, VYŽÁDEJ SI OD UŽIVATELE aktuální zdrojový kód `nexus/src/main.rs`.
**Analýza:** Nexus bojuje mezi Binance a Bitfinexem. Úspěch závisí na rozdílu v řádu milisekund.
**Vylepšení:**
- **Leg-Hedging (Ochrana proti zlomené noze):** Co se stane, když se exekuce na Binance povede, ale Bitfinex order je zamítnut? Nexus musí mít krizový "Hedge" režim, který okamžitě uzavře otevřenou pozici, klidně se ztrátou, aby zabránil delta riziku.
- **Fee-Adjusted Spread Tracking:** Ověřit, že Nexus čte poplatky dynamicky pro obě burzy současně přes nový fee_matrix.bin.

---

### 📝 Issue #15: TRIGON – The Triangular Killer (Tier-1 Conversion & Viability Check)
**Status:** Plánováno
**Cíl:** Oživení mrtvého kusu kódu nebo jeho finální exekuce.

**Operační zadání pro AI Architekta:**
⚠️ **POZNÁMKA PRO AI:** Než začneš analyzovat, VYŽÁDEJ SI OD UŽIVATELE aktuální zdrojový kód `trigon/src/main.rs`.
**Analýza:** Trigon je momentálně náš nejslabší článek s obrovským třením na poplatcích (3 exekuce = 3x fees).
**Vylepšení:**
- **Synthetic Pair Construction:** Naučit Trigona skládat si vlastní (syntetický) orderbook ze tří různých měnových párů.
- **Viability Killswitch:** Pokud Trigon za 48 hodin v Paper módu (nyní s reálnými nulovými poplatky na BFX) nevygeneruje zisk, bude tento modul oficiálně označen za zastaralý a jeho kapitálový slot bude přesunut k Moonshotovi.

---

## ✅ Dokončené Úkoly (Closed Issues):

### 🏁 Issue #18: Autonomous ML Injector & Hot-Swap
**Status:** DONE / CLOSED
**Řešení:** Úspěšně implementováno. Systém nyní využívá zero-copy mmap architekturu k provádění L1 tensor operací bez nutnosti restartu engine (Sovereign Shield Hot-Swap).

### 🏁 Issue #19: THE WAR ROOM (V2 Omni-Interface Dashboard)
**Status:** DONE / CLOSED
**Řešení:** Úspěšně implementováno. Přechod na 10Hz Server-Sent Events (SSE). Byly odstraněny staré zombie procesy na statických portech, nahrazeny dynamickým HTML5 Canvas s VWS depth-chartem a OBI tachometrem.

### 🏁 Issue #20: Operace OMNI-ROUTER (L0 Exchange Adapters for Binance & Bitfinex)
**Status:** DONE / CLOSED
**Řešení:** Úspěšně implementováno. L0 adaptéry jsou plně spuštěny přes Omni-Router, což efektivně řeší propustnost dual-exchange a arbitráže mezi CEX jako Binance a Bitfinex.
