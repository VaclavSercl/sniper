# 🐺 SNIPER ARMADA v21.1 — Architecture Document

> **Sovereign HFT Trading System** — Pure Rust Hive with in-process AI inference.
> Sub-millisecond execution. Zero-allocation hot paths. Fully autonomous operation.
> **SIM v2.0** — Sovereign Intelligence Matrix with Candle L1 + ZeroClaw L2 + Gemini 3.1 Pro.

---

## System Overview

```
┌─────────────────────────── SERVER (i5-6400 / 16GB / GTX 1060) ──────────────────────────┐
│                                                                                          │
│  ┌───── L0: EXECUTION LAYER (Rust, CPU0) ─────────────────────────────────────────────┐  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐                 │  │
│  │  │  HYDRA   │ │ MOONSHOT │ │   GRID   │ │  TRIGON  │ │  NEXUS   │                 │  │
│  │  │ MM + OBI │ │Flash Dip │ │Grid Maker│ │ Tri-Arb  │ │Cross-Exch│                 │  │
│  │  │  <1ms    │ │  <1ms    │ │  <1ms    │ │  <1ms    │ │  <5ms    │                 │  │
│  │  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘                 │  │
│  │       │ mmap        │ mmap       │ mmap       │ mmap       │ PID file              │  │
│  └───────┼─────────────┼────────────┼────────────┼────────────┼───────────────────────┘  │
│          ▼             ▼            ▼            ▼            ▼                           │
│  ┌─── /dev/shm/beroun/ (mmap IPC — 64B cache-line aligned, SeqLock) ──────────────────┐  │
│  │  engine_state.bin │ moonshot_*.bin │ grid_*.bin │ trigon_*.bin │ l2_command.bin      │  │
│  └────────┬──────────────────────────────────────────────────────────────┬─────────────┘  │
│           ▼                                                             ▼                │
│  ┌───── L1: TACTICAL SHIELD (Rust, CPU1) ─────┐  ┌───── L2: STRATEGIC ORACLE (Python) ┐  │
│  │  Sovereign Cortex v14.0                     │  │  Commander + L2 Oracle             │  │
│  │  ├─ L1 Shield (50ms, GPU Phi-3.5)          │  │  ├─ Gemini 2.5 Pro (5min cycle)    │  │
│  │  ├─ Sentinel (4-layer guardian)             │──│  ├─ Sovereign Boot Protocol        │  │
│  │  ├─ Macro Intel (Binance WS + F&G + RSS)   │  │  ├─ Telegram I/O                  │  │
│  │  └─ UDS Server (/tmp/cortex.sock)           │  │  └─ Master Dashboard (port 3004)  │  │
│  └─────────────────────────────────────────────┘  └────────────────────────────────────┘  │
│                                                                                          │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Layer 0: Execution (Rust)

### Architectural Laws
| Rule | Enforcement |
|------|------------|
| **Zero-Allocation** | No `Box`/`Vec`/`String`/`HashMap` in hot path. `ArrayVec`, `ArrayString`, `FlatMap` only. |
| **Lock-Free** | No `Mutex`/`RwLock`. Atomics with `Ordering::Acquire/Release/SeqCst`. |
| **Fixed-Point** | No `f64` in IPC. `i64` with `PRICE_SCALE = 1e8`. |
| **Cache-Aligned** | `#[repr(C, align(64))]` on all mmap structs. No false sharing. |
| **SeqLock Protocol** | Version counter (even=consistent) guards multi-field reads. |

### Bot Fleet
*(Regulated via Sovereing Boot Protocol (SBP) and 5 Pillars of Risk)*

| Bot | Strategy | Latency | RiskClass | Binary |
|-----|----------|---------|-----------|--------|
| **Hydra** | Market Making + OBI skew + Ghost orders | <1ms | `MARKET_MAKER` | `hydra-core` |
| **Moonshot** | Flash crash dip buying (multi-pair) | <1ms | `POSITIONAL` | `moonshot-core` |
| **Grid** | Dynamic grid market making (L2 warped) | <1ms | `POSITIONAL` | `grid-core` |
| **Trigon** | Triangular arbitrage (14 triangles) | <1ms | `STAT_ARB` | `trigon-core` |
| **Nexus** | Cross-exchange arb (Bitfinex↔Binance) | <5ms | `ARBITRAGE` | `nexus-core` |

---

## Layer 1: Tactical Shield (Rust — Sovereign Cortex)

The Cortex is a dedicated Rust daemon running 5 concurrent subsystems:

### L1 Shield (50ms cycle)
- Reads order book from `engine_state.bin` via mmap
- Computes OBI (Order Book Imbalance) and toxic flow detection
- Sends OBI tensor to in-process **Candle L1 Brain** (Phi-3.5 Q4_K_M GGUF, Logit Sniping, <20ms)
- Writes `l1_skew_adjustment`, `toxic_flow_hits`, `l1_confidence_score` back to mmap
- **Zero-alloc**: All computation uses stack buffers and incremental hashing

### Sentinel (6-Layer Guardian, 5s cycle)
- **Layer 1:** Mmap heartbeat watchdog (stale > 30s → alert)
- **Layer 2:** AI timeout tracking (L1/L2 responsiveness)
- **Layer 3:** Business logic (PnL crash, toxic rate, position drift, spread explosion)
- **Layer 4:** Network liveness (async TCP connect to api.bitfinex.com:443)
- **Layer 5:** System resources (CPU, RAM, GPU temp, VRAM, disk, network)
- **Layer 6:** Regime Sentinel — flash crash (>0.5% in 30s), sweep storm (3+ spread explosions)
  - Writes `1` to `/dev/shm/beroun/toxic_storm.bin` (shared with ML Shield Hive Mind)

### Macro Intelligence
- **Binance WS:** Real-time BTC/USDT mid-price for cross-venue delta
- **Fear & Greed Index:** CoinMarketCap API (15min cycle)
- **News Sentiment:** RSS → keyword scoring (60min cycle)

### UDS Server (`/tmp/cortex.sock`)
- JSON-line protocol over Unix Domain Socket
- Commands: `PING`, `GET_SNAPSHOT`, `SET_GRID`, `SET_MAXPOS`, `SHADOW_TOGGLE`, `PAUSE`, `UNPAUSE`
- Bridge between L1 (Rust) and L2 (Python)

---

## Layer 2: Strategic Oracle (Python)

### L2 Oracle (Gemini 2.5 Pro, 5min cycle)
- Reads full fleet snapshot via UDS `GET_SNAPSHOT`
- Analyzes PnL, inventory, market regime, toxicity
- Writes strategic parameters to `l2_command.bin` mmap:
  - `bid_fade_bps`, `ask_fade_bps` (Avellaneda-Stoikov model)
  - `latency_padding_bps`, `latency_killswitch`
  - Grid Warp Matrix (up to 30 dynamic levels)
  - Global Risk Matrix (hedge triggers, freeze thresholds)
- **SeqLock write protocol:** Increment version (odd), write fields, increment version (even)

### Sovereign Boot Protocol (SBP)
1. **Phase 1:** Infrastructure (Cortex, PnL, Dashboard, Commander)
2. **Phase 2:** Read pre-crash state from `state/armada_state.json`
3. **Phase 3:** Start all bots in **PAPER** mode
4. **Phase 4:** 66-minute validation window (monitor PnL, fills, toxicity)
5. **Phase 5:** AI-driven analysis (Gemini evaluates performance)
6. **Phase 6:** Promote passing bots to **LIVE**

---

## Memory Topology

### Shared Memory Layout (`/dev/shm/beroun/`)

| File | Struct | Size | Owner | Readers |
|------|--------|------|-------|---------|
| `engine_state.bin` | `EngineState` | ~1.7KB | Hydra L0 | Cortex, Dashboard, Oracle |
| `risk_state.bin` | `RiskState` | ~384B | risk-control | Hydra L0 |
| `moonshot_engine.bin` | `MoonshotEngineState` | ~2.5KB | Moonshot L0 | Cortex |
| `moonshot_risk.bin` | `MoonshotRiskState` | ~2.5KB | Oracle L2 | Moonshot L0 |
| `grid_engine.bin` | `GridEngineState` | ~1KB | Grid L0 | Cortex |
| `grid_risk.bin` | `GridRiskState` | ~256B | Oracle L2 | Grid L0 |
| `trigon_engine.bin` | `TrigonEngineState` | ~12KB | Trigon L0 | Cortex |
| `trigon_risk.bin` | `TrigonRiskState` | ~3KB | Oracle L2 | Trigon L0 |
| `cross_exchange.bin`| `CrossExchangeState` | ~4KB | Price Bridge | Nexus, Cortex |
| `l2_command.bin` | `L2SharedState` | 896B | Oracle L2 | All L0 bots |

### Cache Line Protocol

```
┌────── 64 Bytes (L1d Cache Line) ──────┐
│ version_counter: AtomicU64 (SeqLock)  │  ← Ensures consistency
│ field_1: AtomicI64                    │
│ field_2: AtomicU64                    │
│ ...                                   │
│ _padding: [u8; N]                     │  ← Fills to 64B boundary
└───────────────────────────────────────┘
```

**Read Protocol:** `v1 = version.load(Acquire)` → read fields → `v2 = version.load(Acquire)` → valid if `v1 == v2 && v1 % 2 == 0`

**Write Protocol:** `version.store(v+1, Release)` → write fields → `fence(SeqCst)` → `version.store(v+2, Release)`

---

## CPU Pinning Strategy

| Core | Affinity | Processes |
|------|----------|-----------|
| **CPU0** | L0 Hot | Hydra, Grid, Trigon (core_affinity crate) |
| **CPU1** | L1 Tactical | Cortex L1 shield thread, GPU inference |
| **CPU2** | L2 + Infra | Python Commander, Dashboard, PnL, Recorder |
| **CPU3** | OS Reserved | Kernel, systemd, SSH, cron |

---

## Deployment

### Build & Deploy
```bash
./deploy_armada.sh --build    # Compile + regenerate offsets + boot
./deploy_armada.sh            # Boot only (use cached binaries)
```

### Systemd Service
```bash
sudo ./infra/install_service.sh     # Install + enable
sudo systemctl start sniper-armada  # Start
sudo systemctl status sniper-armada # Health check
journalctl -u sniper-armada -f      # Live logs
```

### Python Offset Generation
On every `--build`, `dump_l2_offsets` regenerates `architect/l2_rust_offsets.py` with exact byte offsets for all EngineState fields. Python code imports these instead of hardcoding.

---

## Repository Structure

```
sniper/
├── hydra/           # L0 Market Maker (Rust)
├── moonshot/        # L0 Flash Crash (Rust)
├── grid/            # L0 Grid Maker (Rust)
├── trigon/          # L0 Triangular Arb (Rust)
├── nexus/           # L0 Cross-Exchange (Rust)
├── shared/          # sniper-shared crate (types, framework, IPC)
├── architect/       # L1 Cortex (Rust) + L2 Oracle (Python)
│   ├── cortex/      #   Sovereign Cortex binary
│   └── l2_oracle.py #   Gemini Oracle
├── infra/           # systemd, install, stop scripts
├── state/           # Persistent pre-crash state (JSON)
├── deploy_armada.sh # Sovereign Boot Script
└── watchdog.sh      # Cron-based mmap heartbeat monitor
```

---

## SIM v2.0 — Sovereign Intelligence Matrix

### Overview

```
┌──────────────────────────────────────────────────────┐
│        SOVEREIGN INTELLIGENCE MATRIX v2.0             │
├──────────────────────────────────────────────────────┤
│                                                      │
│  L2 STRATEGIC BRAIN (Python, 5min cycles)            │
│  ├── Performance Tribunal (3+1 RAG closed-loop)      │
│  ├── Adaptive Governor (5-bot bidirectional tuning)  │
│  └── Portfolio Coordinator (dynamic VaR)             │
│                                                      │
│  L1 TACTICAL SHIELD (Rust/Python, 50ms–5s)           │
│  ├── ML Shield v2.0 (online learning, Hive Mind)     │
│  ├── Sentinel Layer 6 (flash crash, sweep storm)     │
│  └── toxic_storm.bin (1-byte mmap, dual-writer)      │
│         ↑ writes                    ↓ reads           │
│    ML Shield + Sentinel  →  ALL 5 RUST BOTS          │
│                                                      │
│  CONTINUOUS LEARNING (Cron)                           │
│  ├── Nightly Retrain (00:00, Shadow Validation)      │
│  └── Weekly GPU Audit (Sun 06:00, Gemini)            │
│                                                      │
└──────────────────────────────────────────────────────┘
```

### Performance Tribunal (Closed-Loop AI)

The L2 Oracle (Gemini) was previously "open-loop" — it made decisions but never
learned if they were good or bad. The Tribunal fixes this:

1. **Decision History:** Every L2 decision is saved to `decision_history` (SQLite)
   with full context: regime, PnL snapshot, parameters, bot states.
2. **T-1 Evaluation:** At the start of each cycle, the Tribunal evaluates the
   *previous* decision: what was the PnL delta? Did toxicity improve or worsen?
3. **3+1 RAG Injection:** Gemini receives: 3 most recent decisions + outcomes,
   plus 1 "golden standard" (best decision ever for current regime).

### Adaptive Parameter Governor

Replaces the old single-bot `_audit_strategies`:

- **ALL 5 bots** monitored (Hydra, Moonshot, Grid, Trigon, Nexus)
- **Bidirectional tuning:** grid_step widens on loss, tightens on profit
- **Fail-fast:** toxic > 50% OR 7d PnL < -$2 → immediate PAPER mode
- Asymmetric: "stairs up, elevator down"

### Portfolio Coordinator

- **Dynamic VaR:** `Max_Exposure = Equity × 5% / VolMultiplier`
- **VaR Utilization:** SAFE (<60%), CAUTION (60-85%), CRITICAL (>85%)
- **Cross-bot coordination:** Prevents all bots going LONG simultaneously
- **Hard limit:** 0.05 BTC absolute maximum

### Hive Mind (Cross-Bot Toxic Storm)

The Hive Mind provides fleet-wide coordination via a shared 1-byte mmap flag:

| Component | Role | Latency |
|-----------|------|---------|
| `ml_shield.py` | **Writer** — VPIN > 0.7 OR spread_z > 3 OR OBI_mom > 0.4 | 50ms |
| `sentinel.rs` Layer 6 | **Writer** — flash crash > 0.5% OR 3+ spread explosions | 5s |
| All 5 Rust bots | **Reader** — skip order placement when flag = 1 | < 1μs |

```
/dev/shm/beroun/toxic_storm.bin  (1 byte)
  0x00 = CLEAR  → normal trading
  0x01 = STORM  → all bots defensive (no new orders)
```

### Continuous Learning Pipeline

| Schedule | Script | Purpose |
|----------|--------|---------|
| `0 0 * * *` | `nightly_retrain.py` | Retrain ML Shield on 24h data |
| `0 6 * * 0` | `weekly_gpu_audit.py` | Gemini reviews GPU tuning |

**Shadow Validation Gate:** v_new model must beat v_old on **both** hit rate
AND mark-out PnL on 6h out-of-sample data. If worse → silently discarded.
Prevents catastrophic forgetting.
