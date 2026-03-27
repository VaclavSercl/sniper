# BEROUN SNIPER v10.4 — Architecture Overview

## Data Flow

```
Bitfinex WebSocket
     │
     ▼
┌─────────────────────────────────────────────┐
│  L0: beroun-core (Rust, µs latency)        │
│  ├── WebSocket Handler (tungstenite)        │
│  ├── Hydra Grid Engine (3-level per side)   │
│  ├── Fill-Rate Tracker                      │
│  ├── Trade Analytics (spread capture, PnL)  │
│  └── mmap EngineState (/dev/shm/beroun/)    │
│       ├── Atomic prices, positions           │
│       ├── L1 skew, confidence, freeze       │
│       └── L2 regime, shadow mode            │
└──────────────────┬──────────────────────────┘
                   │ mmap IPC (zero-copy)
     ┌─────────────┼─────────────────┐
     ▼             ▼                 ▼
┌─────────┐  ┌──────────────┐  ┌──────────────┐
│ L1 Shield│  │ L2 Oracle    │  │ Dashboard    │
│ (Python) │  │ (Python)     │  │ (Rust+HTMX)  │
│ 50ms OBI │  │ 5min Gemini  │  │ SSE+zero JS  │
│ Sweep    │  │ Brain+Lessons│  │ port 3000    │
│ Freeze   │  │ Neural Cross │  │              │
└─────────┘  └──────┬───────┘  └──────────────┘
                    │
                    ▼
          ┌─────────────────┐
          │  Sniper Brain    │
          │  (Rust+SQLite)   │
          │  6 tables WAL    │
          │  Lesson Validator│
          └─────────────────┘
```

## IPC: mmap EngineState

The `EngineState` struct (src/types.rs) is mapped to `/dev/shm/beroun/engine_state.bin`.
All fields are Atomic (u64/i64) with Fixed-Point arithmetic (PRICE_SCALE = 1e8).

```
Offset  Field                  Type
0       heartbeat_ms           AtomicU64
8       best_bid               AtomicU64
16      best_ask               AtomicU64
...
1584    l1_skew_adjustment     AtomicI64
1600    l1_confidence_score    AtomicU64
1608    l2_regime_id           AtomicU64
1616    is_shadow_mode         AtomicU64
```

Cross-layer communication is lock-free via Atomic operations.
L0 writes prices → L1 reads & writes skew → L2 reads all & writes regime.

## SQLite Brain (6 tables)

```sql
cycles              -- 5min Oracle snapshots
alerts              -- Tactical alerts
patterns            -- Learned regime optima
experiments         -- Counterfactual analysis
lessons             -- AI-generated rules
lesson_validations  -- Pre/post PnL validation
```

WAL mode for concurrent reads during live trading.

## Nightly Loop (03:00 CET)

```
1. validate-lessons  → degrade bad lessons
2. backtest          → statistical analysis
3. worst-cycles      → export failures
4. Gemini Coach      → AI self-critique
5. save-lesson       → store new rules
```

## Build

```bash
cargo build --release      # 3 binaries: core, brain, dashboard
```

Profiles: `opt-level=3`, `lto=fat`, `panic=abort`, PGO-ready.
