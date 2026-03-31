# Analyze L1 Cortex Core (Right Hemisphere)

## 1. Analýza chyb a architektury (l1.rs & macro_intel.rs)
- **Zbytečné alokace kolekcí (l1.rs)**: Modul `l1.rs` běží v 50ms (20Hz) exekuční smyčce a na čtení mmap Order Booku alokuje na heapu 2x `Vec::with_capacity(BOOK_DEPTH)` (pro bidy i asky) v každém cyklu v metodě `read_orderbook_levels()`. Vzhledem k tomu, že `BOOK_DEPTH` je fixní (10), je vysoce neefektivní alokovat haldu, namísto použití stack-allocated polí např. `[BookLevel; 10]`, čímž by se ulehčilo paměťovému subsystému OS.
- **Float OBP processing v Rustu (l1.rs)**: Modul L1 štěpí nativní datové typy sub-ms vrstvy (u64 / i64) a konvertuje je do standardních `f64`. Stejný neduh jako u L0 botů, odebírá výhodu fixed-point deterministické aritmetiky s `PRICE_SCALE_I`.
- **Synchronní Zombie Bloky (macro_intel.rs)**: `Sovereign Cortex` vstupuje do `tokio::main` asynchronního runtimu, ale moduly `run_binance_ws`, `run_fear_greed` a `run_news_sentiment` běží nad synchronním HTTP klientem `ureq` a používají tvrdé uspání `std::thread::sleep()`. Pro to se na hrubo spawnuje `std::thread::Builder`. Jde o mixování IO-bound async architektury se synchronním vlakem, plýtvající systémovými resources.

## 2. Architektonické návrhy (Cortex Standard)
Pro dodržení "Sovereign Boot Protocolu" a 2026 HFT specifikace musí být L1 vrstva vyčištěna:

1. **Převod Macro do Tokio Async**: Přepsat `macro_intel.rs`, aby plně benefitoval z Tokio Runtimu. Modul nesmí zakládat nativní OS thready a nahradit `ureq` asynchronním `reqwest`.
   ```rust
   // ZLE
   std::thread::sleep(Duration::from_secs(300));
   let body = ureq::get("...").call().unwrap();
   
   // SPRAVNE
   tokio::time::sleep(Duration::from_secs(300)).await;
   let response = reqwest::get("...").await?.text().await?;
   ```
2. **L1 Stack Arrays (Zero-Allocation)**: Zastavit cyklické alokace Vektorů v L1. V 50ms smyčce `l1.rs` by se mělo naprosto zakázat slovo `Vec` a namísto toho posílat do `compute_obi` jen buffery `[BookLevel; BOOK_DEPTH]` nebo memory slices.
3. **Fixed-Point matematický model L1**: Proměnné `skew_usd` a `depth_ratio` předpočítat skrz i64 matematické blokády bez odboček do hardwarové plovoucí desetinné čárky.

## 3. Akční kroky pro implementaci
- Otevřít a refaktorovat `architect/cortex/src/l1.rs` (Zbavit `Vec` a `f64`).
- Otevřít a refaktorovat `architect/cortex/src/macro_intel.rs` (Zbavit `ureq`, `std::thread`, nahradit tokio async úkoly a `reqwest` client).
