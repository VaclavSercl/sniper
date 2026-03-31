# Analyze L0 Tactical Bots (Moonshot, Grid, Trigon, Nexus)

## 1. Analýza chyb a porušení "Zero-Allocation"
- **Dynamická alokace Stringů na "Hot Pathu"**: Všichni 4 zbývající L0 boti (Moonshot, Trigon, Grid, Nexus) používají nebezpečný pattern `order_msg = String::with_capacity(...)` a serializují/posílají JSON zprávy přes WebSocket za běhu pomocí `.push_str()` a `format!()`. Tím dochází uvnitř sub-ms smyčky neustále k žádostem o paměť na haldě, což je absolutní porušení principu "Zero Allocation" pro L0 vrstvu z Master promptu.
- **Floating-Point Penalizace (f64)**: Namísto využití přesné fixed-point matematiky typu velikosti `i64` boti zbytečně převádí interní formát trhu (`u64 / PRICE_SCALE`) na plovoucí desetinnou čárku (`f64`). 
  - `Grid bot`: Operuje s `f64::powi()` přímo v loopu.
  - `Trigon & Nexus bot`: Počítají spready, poplatky a profit do `f64`. Desetinná čárka je v HFT nedeterministická, pomalejší a vede k zaokrouhlovacím chybám. L0 by po celou dobu měl manipulovat pouze se zlomky postavenými nad `PRICE_SCALE_I` z `shared/types.rs`.

## 2. Odstranění Zombie kódů a Pomalých patternů
- Boti dočasně klonují proměnné v `HashMap` (`idx_to_symbol`), navíc Nexus používá v bloku pro kontrolu `SeqLocku` uvnitř `scan_for_arb` naprosto zbytečně `Ordering::Acquire`, i když data pochází z jiného bloku. 

## 3. Návrh Architektury a optimalizace (L0 Execute)
Při následné implementaci se tyto 4 moduly přepíší podle standardu, který byl nastolen nad `hydra/main.rs` a `shared/types.rs` viz. Issues #34 a #35:

1. **`bytes::BytesMut` JSON Builder**: Místo posílání pomalých Stringů se vytvoří jednorázové byty, nebo se předalokuje `static_builder`.
2. **Fixed-Point Math & Poplatky**:
   - Vymýtit operátory `as f64` natvrdo z mmap loadnutí.
   - Příklad špatného použití Trigonu:
     ```rust
     // BAD (Current state)
     let fee_bps = fee_state.taker_fee_bps.load(Ordering::Relaxed) as f64 / 100.0;
     ```
     Bude nutné přepsat striktně nad integer limity.
3. Přepsat `moonshot/src/main.rs` (Ghost orders payloady), `grid/src/main.rs` (Float powi), `trigon/src/main.rs` (Float arrays) a `nexus/src/main.rs` (Hybrid Float + String) tak, aby L0 zůstala dokonale autonomní, stabilní a lock-free zónou.
