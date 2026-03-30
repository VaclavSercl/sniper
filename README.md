# 🐺 SNIPER ARMADA v15.1 — Sovereign HFT

**Autonomous high-frequency trading platform** for Bitfinex with AI-driven decision-making, cross-exchange intelligence, and zero-latency IPC via mmap + UDS.

## Architecture: "Separated Hemispheres"

```
┌─────────────────────────────────────────────────────────────────┐
│                    DATA PLANE (Rust Cortex)                      │
│  Pure, isolated HFT engine. No Telegram, no HTTPS.              │
│                                                                   │
│  ┌─────────┐ ┌─────────┐ ┌──────────┐ ┌──────────┐             │
│  │ L1 Loop │ │ GPU     │ │ Macro    │ │ Sentinel │             │
│  │ 50ms    │ │ Phi-3.5 │ │ Intel    │ │ Alerts   │             │
│  │ OBI/Skew│ │ Inf.2-4s│ │ F&G/BNB  │ │ Freeze   │             │
│  └────┬────┘ └────┬────┘ └────┬─────┘ └────┬─────┘             │
│       └───────────┴───────────┴─────────────┘                    │
│                       UDS Bridge                                  │
│              /tmp/cortex.sock (JSON-line)                         │
└───────────────────────────┬─────────────────────────────────────┘
                            │
┌───────────────────────────┴─────────────────────────────────────┐
│                   CONTROL PLANE (Python Commander)                │
│  Telegram C2 + L2 Strategic Oracle + Dashboard Server             │
│                                                                   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐           │
│  │ Telegram │ │ L2 Oracle│ │ Dashboard│ │ Event    │           │
│  │ v15.0    │ │ Gemini   │ │ SSE:3004 │ │ Listener │           │
│  │ /gpu /pnl│ │ 5min+rpt │ │ Real-time│ │ Sentinel │           │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘           │
└──────────────────────────────────────────────────────────────────┘
```

## File Structure

```
sniper/
├── shared/              ← 🔧 Shared types crate (sniper_types)
│   ├── src/types.rs             ← EngineState, RiskState (repr(C, align(64)))
│   ├── src/moonshot_types.rs    ← Moonshot PairState[20]
│   ├── src/grid_types.rs        ← Grid LevelState[10]
│   ├── src/trigon_types.rs      ← Trigon TriangleState[10]
│   ├── src/pnl_types.rs         ← PnlBotState, PnlGlobalState
│   ├── src/math.rs              ← Fixed-point PRICE_SCALE (1e8) utilities
│   └── src/logging.rs           ← Tracing setup (JSON, daily rotation)
│
├── hydra/               ← 🐍 Bot #1: BTC-USD Delta Lead HFT (Core 0, :3000)
│   ├── src/main.rs              ← L0 Engine (1,434 LOC)
│   ├── src/brain.rs             ← SQLite analytics (1,099 LOC)
│   ├── src/dashboard.rs         ← SSE Dashboard server (816 LOC)
│   └── src/config_cli.rs        ← Live config via mmap
│
├── moonshot/            ← 🌙 Bot #2: Multi-Symbol Flash Crash (Core 1, :3001)
├── grid/                ← 📐 Bot #3: Dynamic Multi-Level Grid (Core 2, :3002)
├── trigon/              ← 🔺 Bot #4: Triangular Arbitrage (Core 3, :3003)
├── mdf/                 ← 🔄 Shared Market Data Feed
│
├── architect/           ← 🏛️ Central Orchestrator
│   ├── cortex/                  ← 🧠 Sovereign Cortex (Rust)
│   │   └── src/
│   │       ├── main.rs          ← Cortex launcher (186 LOC)
│   │       ├── l1.rs            ← L1 tactical loop (451 LOC)
│   │       ├── gpu.rs           ← Phi-3.5 inference + ring buffer (480 LOC)
│   │       ├── uds.rs           ← UDS command server (321 LOC)
│   │       ├── memory.rs        ← Multi-bot mmap reader (336 LOC)
│   │       ├── macro_intel.rs   ← Fear & Greed + Binance sync (262 LOC)
│   │       └── sentinel.rs      ← Push alerts via UDS (412 LOC)
│   ├── tg_commander.py          ← Telegram Commander v15.0
│   ├── l2_oracle.py             ← L2 Strategic Oracle — Gemini (reports hourly)
│   ├── cortex_client.py         ← Python UDS client (184 LOC)
│   ├── dashboard_server.py      ← Master Dashboard SSE server :3004 (141 LOC)
│   ├── pnl_daemon.py            ← PnL Daemon: FIFO, 30s cycles (466 LOC)
│   └── master_dashboard.html    ← Multi-bot web UI (290 LOC)
│
├── deploy_armada.sh     ← Master launch script
├── sniper-armada.service ← systemd (auto-start, FIFO scheduler)
├── Cargo.toml           ← Workspace root (Rust 2024 Edition)
└── .env                 ← API keys (BITFINEX + TELEGRAM)
```

## Four-Layer Architecture

| Layer | Runtime | Role | Latency |
|-------|---------|------|---------|
| **L0** | Rust (async, CPU-pinned) | Execution, orderbook, order management | < 1ms |
| **L1** | Rust (Cortex) | Tactical: OBI skewing, sweep detection, confidence | ~50ms |
| **GPU** | Rust → Phi-3.5 (LM Studio) | Inference: SKEW_BID/ASK, PAUSE decisions | ~2-4s |
| **L2** | Python → Gemini | Strategic Oracle: regime, grid tuning, L1 tuning | 5min (silent), reports hourly |

## Bots

### 🐍 Hydra (Bot #1) — BTC-USD Delta Lead
- **Core 0** · Port **:3000** · Full AI stack (L1 + GPU + Macro)
- Single-pair market making with adaptive ghost orders
- Delta Lead: cross-venue arbitrage prediction (Binance→Bitfinex)
- Fee Sentinel: autonomous fee monitoring via Bitfinex REST

### 🌙 Moonshot (Bot #2) — Multi-Symbol Flash Crash
- **Core 1** · Port **:3001** · 20 simultaneous pairs
- AI-selected pair rotation every 66 minutes
- Ghost BUY orders placed X% below market ("spike catching")

### 📐 Grid (Bot #3) — Dynamic Multi-Level Grid
- **Core 2** · Port **:3002** · N BUY + N SELL levels
- ATR-based dynamic adjustment, Consecutive Loss Halt

### 🔺 Trigon (Bot #4) — Triangular Arbitrage
- **Core 3** · Port **:3003** · N-triangle monitoring
- A→B→C→A when implied_rate > 1 + 3×fee

## 🧠 Sovereign Cortex (v14.0)

The Cortex is the AI brain — **100% network-isolated** from Telegram/internet.

| Module | Role | Update Rate |
|--------|------|-------------|
| `l1.rs` | OBI analysis, sweep detection, confidence scoring | 50ms |
| `gpu.rs` | Phi-3.5 inference (SKEW/PAUSE), ring buffer telemetry, evaluator | 2-4s |
| `macro_intel.rs` | Fear & Greed Index, Binance sweep detection | 60s |
| `sentinel.rs` | mmap age monitoring, push alerts to Commander | 10s |
| `uds.rs` | Command server: GET_SNAPSHOT, SET_GRID, SET_L1_TUNING | on-demand |
| `memory.rs` | Multi-bot mmap reader (Hydra/Moonshot/Grid/Trigon) | real-time |

### L2 Feedback Loop (Generál → Voják)
```
GPU Phi-3.5 → Ring Buffer → Evaluator → GET_GPU_STATS
                                              ↓
L2 Oracle (Gemini) ← GPU telemetry + Bot snapshot
                                              ↓
SET_L1_TUNING → skew_max, obi_threshold, inference_interval
                                              ↓
GPU Phi-3.5 reads new params → adapts behavior
```

## 📱 Telegram Commander v15.0

All automated Telegram reports are generated by L2 Oracle (Gemini AI):
- **⏰ Hodinový** — každou celou hodinu
- **📅 Denní** — v 00:00
- **📊 Týdenní** — pondělí v 00:00
- **📈 Měsíční** — 1. den měsíce v 00:00

L2 Oracle runs silently every 5 minutes (parameter tuning only, no Telegram spam).

| Command | Description |
|---------|-------------|
| `/status` | All bots status overview |
| `/hydra start\|stop\|restart\|pause` | Manage Hydra |
| `/moonshot start\|stop\|restart` | Manage Moonshot |
| `/grid start\|stop\|restart` | Manage Grid |
| `/trigon start\|stop\|restart` | Manage Trigon |
| `/pnl` | FIFO PnL report (volatility-neutral) |
| `/gpu` | Phi-3.5 GPU performance report |
| `/panic` | Emergency: stop ALL bots |
| `/analyze` | Gemini AI market analysis |
| `/help` | Show all commands |
| *Natural language* | _"Ahoj snipere, vypni hydru"_ |

## IPC Map

| Path | Type | Written By | Read By |
|------|------|-----------|---------|
| `/dev/shm/beroun/engine_state.bin` | mmap | hydra-core | cortex, dashboard, brain |
| `/dev/shm/beroun/risk_state.bin` | mmap | cortex (L1) | hydra-core |
| `/dev/shm/beroun/moonshot_engine.bin` | mmap | moonshot-core | cortex |
| `/dev/shm/beroun/grid_engine.bin` | mmap | grid-core | cortex |
| `/dev/shm/beroun/trigon_engine.bin` | mmap | trigon-core | cortex |
| `/dev/shm/beroun/pnl_state.bin` | mmap | pnl_daemon | dashboards |
| `/tmp/cortex.sock` | UDS | cortex | commander, l2_oracle |
| `/tmp/commander_events.sock` | UDS | cortex (sentinel) | commander |

## Safety Architecture

```
┌─── NON-OVERRIDABLE (Hardcoded in Rust) ──────────────────────┐
│  • Daily Loss Limit (DLL) circuit breaker                     │
│  • Max position size (capital guard)                          │
│  • Anti-Cross Guard (bid < best_ask, ask > best_bid)         │
│  • Fee Sentinel: spread < 2× fees → skip cycle              │
│  • AI Heartbeat > 30s → bias zeroed (Hydra)                  │
│  • L1 Tuning: clamp(skew 0.5-5.0, obi 0.0-0.8)            │
│  • Instance Lock: fs2 exclusive file lock                    │
│  • Telegram PANIC: /panic → stops ALL bots                   │
└──────────────────────────────────────────────────────────────┘
```

## Quick Start

```bash
# Build
cargo build --release --workspace

# Deploy (Hydra + Cortex + Commander + PnL)
./deploy_armada.sh

# Or via systemd
sudo systemctl enable --now sniper-armada

# Open dashboards
# Master:  http://localhost:3004
# Hydra:   http://localhost:3000
```

## Hardware Profile (Beroun Server)

| Resource | Spec | Allocation |
|----------|------|------------|
| CPU | i5-6400 (4 cores, no HT) | Core 0: Hydra, Core 1: Moonshot, Core 2: Grid, Core 3: Cortex + AI |
| RAM | 16 GB | ~4 GB used, 12 GB available |
| GPU | GTX 1060 6GB | Phi-3.5 Mini (LM Studio, localhost:1234) |
| SHM | 7.6 GB tmpfs | mmap files for zero-copy IPC |

## Version History

| Version | Codename | Key Feature |
|---------|----------|-------------|
| v15.1 | System Hardening | Instance locks, dead code removal, duplicate process guards |
| v15.0 | Sovereign HFT | Consolidated AI reporting (hourly/daily/weekly/monthly), lmstudio.service dependency, unified watchdog |
| v14.0 | Separated Hemispheres | Cortex/Commander split, GPU telemetry, L2 feedback loop, Master Dashboard |
| v13.0 | Sovereign Intelligence | Zero-Debt Audit + unified standard |
| v12.0 | Armada PnL | PnL FIFO Engine + Telegram Commander v2.0 |
| v11.2 | Full Armada | 4-bot workspace (Hydra + Moonshot + Grid + Trigon + MDF) |
| v10.0 | Apex Predator | Dynamic grid + analytics |

## License

Private / Proprietary
