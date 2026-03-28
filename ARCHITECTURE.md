# 🏗️ SNIPER ARMADA v13.0 — Architecture Document

## System Overview

```
                  ┌─────────────────────────────────────────┐
                  │          📱 TELEGRAM COMMANDER v2.0      │
                  │  /hydra /moonshot /grid /trigon /pnl     │
                  │  Natural Language via Gemini AI          │
                  └────────────┬─────────────────────────────┘
                               │ telebot polling
┌──────────────────────────────┴──────────────────────────────────────────┐
│                     SNIPER ARMADA WORKSPACE (Cargo)                      │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │  shared/ (sniper_types crate)                                       │ │
│  │  EngineState │ MoonshotState │ GridState │ TrigonState │ PnlState  │ │
│  │  math.rs (fixed-point)  │  logging.rs (tracing)                    │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                               │                                          │
│       ┌───────────────┬───────┼───────────┬───────────────┐              │
│       ▼               ▼       ▼           ▼               ▼              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐ │
│  │  hydra/   │  │moonshot/ │  │  grid/   │  │ trigon/  │  │  mdf/    │ │
│  │  Bot #1   │  │  Bot #2  │  │  Bot #3  │  │  Bot #4  │  │  Feed    │ │
│  │  BTC-USD  │  │  Multi-  │  │ Dynamic  │  │ Triangl. │  │  Market  │ │
│  │  Delta    │  │  Symbol  │  │  Grid    │  │  Arb     │  │  Data    │ │
│  │  Core 0   │  │  Core 1  │  │  Core 2  │  │  Core 3  │  │         │ │
│  │  :3000    │  │  :3001   │  │  :3002   │  │  :3003   │  │         │ │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬────┘ │
│       │              │             │              │              │       │
│       ▼              ▼             ▼              ▼              ▼       │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                /dev/shm/beroun/ (Lock-free mmap IPC)               │ │
│  │  engine_state  │ moonshot_engine │ grid_engine │ trigon_engine      │ │
│  │  risk_state    │ moonshot_risk   │ grid_risk   │ trigon_risk        │ │
│  │  pnl_state.bin │ mdf.bin         (Atomic, Zero-copy, <1μs)        │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                               │                                          │
│       ┌───────────────────────┼───────────────────────┐                  │
│       ▼                       ▼                       ▼                  │
│  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────┐          │
│  │ 🏛️ Architect  │  │ 💰 PnL Engine    │  │ 🧠 Hydra AI      │          │
│  │ Dashboard     │  │ FIFO Tracking    │  │ L1: Shield       │          │
│  │ :3004 (REST)  │  │ SQLite + mmap    │  │ L2: Orchestrator │          │
│  │ SSE Events    │  │ 30s cycles       │  │ L3: Macro Mon.   │          │
│  └──────────────┘  └──────────────────┘  └──────────────────┘          │
└──────────────────────────────────────────────────────────────────────────┘
```

## Component Matrix

| Component | Language | LOC | Role | Port | CPU |
|-----------|----------|-----|------|------|-----|
| **hydra-core** | Rust | 1,434 | BTC-USD Delta Lead HFT | :3000 | Core 0 |
| **hydra-brain** | Rust | 1,099 | SQLite analytics + Neural Cross | — | Core 0 |
| **hydra-dashboard** | Rust | 816 | SSE Dashboard server | :3000 | Core 3 |
| **hydra-config** | Rust | 247 | Live config via mmap | — | — |
| **moonshot-core** | Rust | 344 | Multi-Symbol Flash Crash | :3001 | Core 1 |
| **grid-core** | Rust | 334 | Dynamic Multi-Level Grid | :3002 | Core 2 |
| **trigon-core** | Rust | 363 | Triangular Arbitrage | :3003 | Core 3 |
| **shared** | Rust | 929 | Type definitions + math + logging | — | — |
| **l1_shield** | Python | 621 | Tactical AI (OBI, sweep, ghost) | — | Core 3 |
| **sniper_orchestrator** | Python | 1,146 | Sovereign Oracle (Gemini + Brain) | — | Core 3 |
| **sniper_architect** | Python | 235 | Master Dashboard + REST | :3004 | Core 3 |
| **tg_commander** | Python | 590 | Telegram C2 (Slash + NL) | — | Core 3 |
| **pnl_daemon** | Python | 467 | FIFO PnL Engine + mmap writer | — | Core 3 |

**Total:** 6,477 Rust LOC + ~3,100 Python LOC = **~9,600 LOC**

## Bot Template (Standard Structure)

```
bot/
├── Cargo.toml               ← depends on sniper-shared
├── src/
│   ├── main.rs              ← L0 Engine (Rust, hot loop)
│   ├── dashboard.rs         ← SSE Dashboard server
│   ├── brain.rs             ← SQLite analytics/history
│   └── config_cli.rs        ← Live config via mmap
└── dashboard.html           ← Dashboard frontend (HTMX + SSE)
```

Only Hydra has the full AI stack (`scripts/`) because it's the primary live trading bot.

## Memory Layout (EngineState)

Critical mmap offsets for cross-language IPC (Rust ↔ Python):

| Field | Offset | Size | Type |
|-------|--------|------|------|
| `latency_ns` | 0 | 8 | AtomicU64 |
| `_pad_heartbeat` | 8 | 56 | [u8; 56] |
| `best_bid` | 64 | 8 | AtomicU64 |
| `best_ask` | 72 | 8 | AtomicU64 |
| `bids[25]` | 80 | 600 | [OrderBookLevel; 25] |
| `asks[25]` | 680 | 600 | [OrderBookLevel; 25] |
| `t2t_micros` | 1280 | 8 | AtomicU64 |
| `micro_price` | 1288 | 8 | AtomicU64 |
| `net_position` | 1408 | 8 | AtomicI64 |
| `realized_pnl` | 1416 | 8 | AtomicI64 |
| `ai_heartbeat_ms` | 1480 | 8 | AtomicU64 |
| `toxic_flow_hits` | 1568 | 8 | AtomicU64 |
| `l1_confidence_score` | 1600 | 8 | AtomicU64 |
| `l2_regime_id` | 1608 | 8 | AtomicU64 |
| `maker_fee_bps` | 1928 | 8 | AtomicU64 |
| `taker_fee_bps` | 1936 | 8 | AtomicU64 |

**Total struct size:** 1,960 bytes (cache-line aligned, `repr(C, align(64))`)

## PnL Engine (v2.0 — Volatility-Neutral)

```
Bitfinex REST → 30s poll → FIFOEngine
                               │
                    ┌──────────┼──────────┐
                    ▼          ▼          ▼
              FIFO Match   SQLite    mmap write
              USD fixace   fills     pnl_state.bin
              at exec ms   table     ↓
                    │                dashboards
                    ▼
              Time Windows: 1h / 24h / 7d / 30d
```

**Key Principles:**
- FIFO matching (First In, First Out)
- USD fixation at execution millisecond
- Fees tracked separately (transparency)
- Wallet PnL = info only (not trading PnL)

## Safety Architecture

```
┌─── NON-OVERRIDABLE (Hardcoded) ──────────────────────────────┐
│  • Daily Loss Limit (DLL) circuit breaker                     │
│  • Max position size (capital guard)                          │
│  • Anti-Cross Guard (bid < best_ask, ask > best_bid)         │
│  • Fee Sentinel: spread < 2× fees → skip (Hydra)             │
│  • AI Heartbeat > 30s → zero bias (Hydra)                     │
│  • AI Heartbeat > 120min → safe mode (Moonshot)              │
│  • BTC Volatility Kill > 4%/h (Moonshot, Grid)              │
│  • Active Trade Lock (Moonshot)                               │
│  • Consecutive Loss Halt: 3 losses → 24h pause (Grid)       │
│  • Instance Lock: fs2 exclusive file lock (no dual-trade)    │
│  • Telegram PANIC: /panic → stops ALL bots                   │
└──────────────────────────────────────────────────────────────┘
```

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/status` | All bots status overview |
| `/hydra start\|stop\|restart\|pause` | Manage Hydra |
| `/moonshot start\|stop\|restart` | Manage Moonshot |
| `/grid start\|stop\|restart` | Manage Grid |
| `/trigon start\|stop\|restart` | Manage Trigon |
| `/pnl` | FIFO PnL report (1h/24h/7d/30d) |
| `/panic` | Emergency: stop ALL bots |
| `/analyze` | Gemini market analysis |
| `/help` | Show all commands |
| *Natural language* | Gemini AI intent parsing |

## Hardware Profile (Beroun Server)

| Resource | Spec | Allocation |
|----------|------|------------|
| CPU | i5-6400 (4 cores, no HT) | Core 0: Hydra, Core 1: Moonshot, Core 2: Grid, Core 3: OS + AI |
| RAM | 16 GB | ~4 GB used (4 bots), 12 GB available |
| GPU | GTX 1060 6GB | NVIDIA MPS for shared AI inference |
| SHM | 7.6 GB tmpfs | 10 mmap files for IPC |

## IPC Map

| Path | Written By | Read By |
|------|-----------|---------| 
| `/dev/shm/beroun/engine_state.bin` | hydra-core | dashboard, brain, l1_shield |
| `/dev/shm/beroun/risk_state.bin` | l1_shield, orchestrator | hydra-core |
| `/dev/shm/beroun/moonshot_engine.bin` | moonshot-core | dashboard, brain |
| `/dev/shm/beroun/moonshot_risk.bin` | AI sidecar | moonshot-core |
| `/dev/shm/beroun/grid_engine.bin` | grid-core | dashboard, brain |
| `/dev/shm/beroun/grid_risk.bin` | AI sidecar | grid-core |
| `/dev/shm/beroun/trigon_engine.bin` | trigon-core | dashboard, brain |
| `/dev/shm/beroun/trigon_risk.bin` | AI sidecar | trigon-core |
| `/dev/shm/beroun/pnl_state.bin` | pnl_daemon | all dashboards, architect |
| `/dev/shm/beroun/mdf.bin` | mdf | pnl_daemon, trigon |

## Version History

| Version | Codename | Key Feature |
|---------|----------|-------------|
| v13.0 | Sovereign Intelligence | Zero-Debt Audit + L1 offset fix + 7GB cleanup |
| v12.0 | Armada PnL | PnL FIFO Engine + Telegram Commander v2.0 |
| v11.2 | Full Armada | 4-bot workspace (+ Trigon + MDF) |
| v11.1 | Delta Lead + Moonshot | Cross-venue arb + flash crash |
| v11.0 | Sentinel Singularity | Fair Value + Anti-Flicker |
| v10.9 | Omniscient Predator | Macro monitor + Binance sync |
| v10.0 | Apex Predator | Dynamic grid + analytics |
