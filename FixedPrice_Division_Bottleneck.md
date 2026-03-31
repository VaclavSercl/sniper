# Záznam Technického Dluhu: i128 Dělení v Hot-Pathu (Software Fallback)

## 1. Popis problému
Při návrhu a zavedení bezpečné struktury `FixedPrice(i64)` pro HFT výpočty jsme se rozhodli využít rozšíření operátorů na `i128` při operacích násobení (`Mul`) a dělení (`Div`). Důvodem je absolutní zabránění nepostřehnutelnému mezisoučtovému přetečení (overflow) při manipulaci s `PRICE_SCALE = 1e8`.

Je to **matematicky 100% neprůstřelné**, nicméně běžné 64bitové procesory (x86_64) nemají hardwarovou instrukci pro dělení 128bitových čísel. Kompilátor Rustu (LLVM) proto injektuje do zkompilované binárky volání softwarové mikrofunkce (typicky `__divti3`).
To vyvolává následující degradaci: softwarové dělení je **asi 3x až 5x pomalejší** než klasické instrukčně podpořené i64 dělení na samotném procesoru.

## 2. Aktuální status a akceptace
*   **Stav:** Akceptovaný dočasný technický dluh (Phase 2).
*   **Důvod:** Bezpečnost a eliminace nedeterministických floatů (f64) má u "Sovereign Boot Protocol" nyní absolutní přednost. Nasazení robustního a spolehlivého kódu pro L0/L1 přesahuje dočasnou ztrátu několika nanosekund na operaci.

## 3. Akční plán (Optimalizace Phase 3)
Až bude systém kompletně převedený na instanci `FixedPrice`, spuštěn a otestován pod zátěží (bez memory corrupů), tento specifický bottleneck v `shared/src/math.rs` bude refaktorován:
*   Plošné nasazení super-optimalizovaného modula (tzv. Fast Division), ideálně převodem škály do mocniny 2 (např. bitový posun o bázi `2^26`), jelikož pro bit shifty existují super-rychlé ASM instrukce přímo v x86 architektuře.
*   Zásah bude vyžadovat kompletní pročištění sémantiky, proto se izoluje výhradně do fáze, kdy budeme lovit poslední submikrosekundové prodlevy přes PGO profilování.
