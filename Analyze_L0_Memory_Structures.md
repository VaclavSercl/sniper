# Analyze_L0_Memory_Structures.md

## 1. Bug Hunting & Security
- **Heap Allocation Risk v L0**: Globální cesty souborů ukazují na paměťovou alokaci. Použití `std::sync::LazyLock<String>` pro stringové konstanty (`RISK_STATE_PATH` a `ENGINE_STATE_PATH`) vynucuje zbytečnou alokaci `String` na haldě při prvním zavolání. L0 musí operovat v režimu `no_alloc` s `const &str`.
- **Cache-line Alignment Check**: Padding `_pad_heartbeat: [u8; 56]` a `_pad_hot_cold: [u8; 8]` je matematicky správně zarovnán na cache line boundary, čímž u sdílené paměti (mmap) bezpečně brání proti *False Sharing* u `latency_ns` a hot zone prvků.
- **Riziko ztráty přesnosti u Floating-Pointu v HFT**: V hot-path a L0 typech je explicitně zakázána jakákoliv reprezentace Floatů, avšak `PRICE_SCALE` a `LEVEL_SPACING` jsou floaty a otevírají logiku dalším chybám s f64.

## 2. Zero-Tolerance na Zombie kód
V souboru narušuje filozofii Zero-Allocation a floating-point paralýza. Následující řádky absolutně odstraňujeme:
- Lístky starých architektur. Float aritmetika způsobuje nedeterministické latence na FPU:
  ```rust
  // Řádek 1 a 4-5 - alokace na haldě přes LazyLock a String
  use std::sync::LazyLock;
  pub static RISK_STATE_PATH: LazyLock<String> = LazyLock::new(|| "/dev/shm/beroun/risk_state.bin".to_string());
  pub static ENGINE_STATE_PATH: LazyLock<String> = LazyLock::new(|| "/dev/shm/beroun/engine_state.bin".to_string());

  // Řádek 7 - zakázaný f64
  pub const PRICE_SCALE: f64 = 100_000_000.0;

  // Řádek 28-29 - pole f64 pro grid multiplier místo i64/u64
  pub const LEVEL_SPACING: [f64; MAX_GRID_LEVELS] = [1.0, 2.5, 4.5, 7.0, 10.0];
  ```

## 3. Optimalizace podle Vrstvy (L0 / Rust)
Jelikož se jedná o nejdůležitější paměťové mapování pro L0 vrstvu, musíme provést:
- **Zero-Allocation**: Změnit globální cesty z `LazyLock<String>` na `const &str`. Tím zcela eliminujeme jakýkoli heap struct access z L0 do haldy.
- **Fixed-Point Arithmetic Enforcement**: Všechny floatové typy jako `PRICE_SCALE` a dynamické spacing multiplikátory přepsat striktně do i64/u64 `PRICE_SCALE_I` reprezentace (např. vynásobeno 1e8 nebo 1e4 pro bp logic). To zaručuje, že žádné moduly už nebudou svádět k iteracím s `f64`.

## 4. Refaktorovaný kód (`shared/src/types.rs`)
```rust
use std::sync::atomic::{AtomicI64, AtomicU32, AtomicU64};

// OPTIMALIZACE: Zero-allocation const referenční řezy (žádný LazyLock/String heap allocation!)
pub const RISK_STATE_PATH: &str = "/dev/shm/beroun/risk_state.bin";
pub const ENGINE_STATE_PATH: &str = "/dev/shm/beroun/engine_state.bin";

// OPTIMALIZACE: POUZE fixed-point matematika, f64 verze nemilosrdně smazána.
pub const PRICE_SCALE_I: i64 = 100_000_000;
pub const BOOK_LEVELS: usize = 25;
pub const MAX_GRID_LEVELS: usize = 5;

// v10.0: Trading pair configuration
pub const TRADING_SYMBOL: &str = "tBTCUSD";
pub const TRADING_BASE: &str = "BTC";
pub const TRADING_QUOTE: &str = "USD";

// v14.0: Per-bot GID ranges for trade attribution
pub const BOT_GID_HYDRA: u32 = 1000;
pub const BOT_GID_MOONSHOT: u32 = 2000;
pub const BOT_GID_GRID: u32 = 3000;
pub const BOT_GID_TRIGON: u32 = 4000;

// OPTIMALIZACE: Převedeno na i64/integer bázi (desetiny jsou reprezentovány x 10)
// 1.0 -> 10, 2.5 -> 25, 4.5 -> 45 atd.
pub const LEVEL_SPACING_I: [i64; MAX_GRID_LEVELS] = [10, 25, 45, 70, 100];

#[repr(C)]
pub struct OrderBookLevel {
    pub price: AtomicU64,
    pub amount: AtomicI64,
    pub count: AtomicU64,
}

#[repr(C, align(64))]
pub struct EngineState {
    pub latency_ns: AtomicU64,
    pub _pad_heartbeat: [u8; 56],

    pub best_bid: AtomicU64,
    pub best_ask: AtomicU64,
    pub bids: [OrderBookLevel; BOOK_LEVELS],
    pub asks: [OrderBookLevel; BOOK_LEVELS],

    pub t2t_micros: AtomicU64,
    pub micro_price: AtomicU64,
    pub current_skew: AtomicI64,

    pub active_buy_ids: [AtomicU64; MAX_GRID_LEVELS],
    pub active_sell_ids: [AtomicU64; MAX_GRID_LEVELS],

    pub l2_imbalance: AtomicI64,
    pub current_order_usd: AtomicU64,

    pub _pad_hot_cold: [u8; 8],

    pub net_position: AtomicI64,
    pub realized_pnl: AtomicI64,
    pub wallet_btc: AtomicU64,
    pub wallet_usd: AtomicU64,
    pub checksum: AtomicU32,
    pub _padding: [u8; 4],
    pub last_buy_price: AtomicI64,
    pub last_sell_price: AtomicI64,

    pub average_entry_price: AtomicI64,

    pub current_ai_bias: AtomicI64,
    pub ai_heartbeat_ms: AtomicU64,
    pub ai_alpha_usd: AtomicI64,

    pub buy_fill_count: AtomicU64,
    pub sell_fill_count: AtomicU64,

    pub monthly_volume_usd: AtomicU64,

    pub session_buy_volume: AtomicU64,
    pub session_sell_volume: AtomicU64,
    pub session_buy_usd: AtomicU64,
    pub session_sell_usd: AtomicU64,
    pub session_fill_count: AtomicU64,
    pub session_pnl_realized: AtomicI64,
    pub toxic_flow_hits: AtomicU64,
    pub sweep_freeze_until: AtomicU64,
    pub l1_skew_adjustment: AtomicI64,
    pub analytics_checkpoint_ms: AtomicU64,

    // ... Zbytek structu je shodný ...
}
```
