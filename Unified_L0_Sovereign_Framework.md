# Unified L0 Sovereign Framework

## 1. Současný Technický Dluh (Roztříštěná Flotila)
Z analýzy všech 5 exekučních botů (Hydra, Moonshot, Grid, Trigon, Nexus) prokazatelně vyplývá masivní duplikace síťového a paměťového kódu. Každý bot si "na koleně" řeší:
- Tokio WebSocket (Bitfinex), TLS připojování a exponenciální timeouty
- Otevírání, parsování a serializaci UDS/JSON událostí (často přes `String::push_str`)
- Pinging srdečního tepu latence v mmap Engine IPC souborech
- Vynucování System-Mutexu přes `lock::ensure_single_instance`

Tento stav narušuje **Zero-Allocation** a **DRY (Don't Repeat Yourself)** pravidla HFT komunity. Pokud v současném stavu implementujeme sub-milisekundovou optimalizaci (např. fixed-point matematiku z Issue #34, #35 a #40), budeme to muset zduplikovat v 5 různých Rust projektech L0 vrstvy, čímž vzniká značné třecí místo pro chyby.

## 2. Navrhovaná Architektura (SovereignEngine Trait)
Základním stavebním kamenem `sniper-armada` verze 2026 bude vytvoření sjednocené vrstvy `SovereignEngine` ukotvené ve složce `shared/src/framework.rs`.

**L0 Moduly budou sjednoceny do tohoto nového Trait Standardu:**
```rust
pub trait SovereignEngine {
    // Definice obsluhovaných MMAP pointerů (každý bot si nadefinuje vlastní EngineState/RiskState)
    type EngineState;
    type RiskState;

    fn on_start(&mut self) -> Result<()>;
    fn on_auth(&mut self);
    // Všechna kritická logika (Zero Alloc fixed-point argumenty) v Hot-path smyčce
    fn on_ticker(&mut self, best_bid: u64, best_ask: u64, executor: &mut ExecutionWriter);
    // Vratná logika pro asynchronní zprávy (non-blocking)
    fn on_event(&mut self, json_payload: &[u8]);
}
```

## 3. Plánovaný Refactoring
Tento issue je vstupní "zastřešující" (Epic) lístek pro následující akční plán:
1. **Implementace SovereignEngine API**: Vybudovat v `shared` asynchronní kostru (`Runner`), která pojme TLS WebSocket a MMAP synchronizaci pro kteréhokoliv bota.
2. **Portování Hydry**: Přepis `hydra/src/main.rs`, který se tím "seřízne" z obludných 1200 řádků na několik stovek zacílených pouze na strategii (Neural Cross/Tick-To-Trade).
3. **Rollování na zbytek flotily**: Stejným filtrem projdou Trigon, Moonshot, Grid i Nexus.

Sjednocením vytvoříme vysoce optimalizovanou, testovatelnou základnu, která dokáže všechny L0 boty pohánět naráz se sníženou režií na OS thready a garbage collector.
