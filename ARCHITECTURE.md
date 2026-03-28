# 🏗️ SNIPER ARMADA v12.0 — Architecture Document

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

| Component | Language | Role | Port | CPU |
|-----------|----------|------|------|-----|
| **hydra-core** | Rust | BTC-USD Delta Lead HFT | :3000 | Core 0 |
| **moonshot-core** | Rust | Multi-Symbol Flash Crash | :3001 | Core 1 |
| **grid-core** | Rust | Dynamic Multi-Level Grid | :3002 | Core 2 |
| **trigon-core** | Rust | Triangular Arbitrage | :3003 | Core 3 |
| **mdf** | Rust | Market Data Feed | — | — |
| **sniper_architect** | Python | Master Dashboard + REST | :3004 | Core 3 |
| **tg_commander** | Python | Telegram C2 (Slash + NL) | — | Core 3 |
| **pnl_daemon** | Python | FIFO PnL Engine | — | Core 3 |
| **l1_shield** | Python | Tactical AI (OBI Guard) | — | Core 3 |
| **sniper_orchestrator** | Python | Strategic Oracle (Gemini) | — | Core 3 |
| **macro_monitor** | Python | Cross-venue Data Feed | — | Core 3 |

## Bot Template (Standard Structure)

```
bot/
├── Cargo.toml               ← depends on sniper-shared
├── src/
│   ├── main.rs              ← L0 Engine (Rust, hot loop)
│   ├── dashboard.rs         ← SSE Dashboard server
│   ├── brain.rs             ← SQLite analytics/history
│   └── config_cli.rs        ← Live config via mmap
├── scripts/                  ← Python AI layers (when applicable)
│   ├── l1_shield.py         ← L1 Tactical AI
│   ├── sniper_orchestrator.py ← L2 Strategic Oracle
│   └── macro_monitor.py     ← Market data augmentation
├── dashboard.html           ← Dashboard frontend (HTMX + SSE)
└── bot-start.sh             ← Launch (taskset + env)
```

## PnL Engine (v2.0 — Volatility-Neutral)

```
Bitfinex WS → "tu" event → FIFOEngine
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
- Fees tracked separately
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
| SHM | 7.6 GB tmpfs | 8 mmap files for IPC |

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
| v12.0 | Sovereign Intelligence | PnL FIFO Engine + Telegram Commander v2.0 |
| v11.2 | Full Armada | 4-bot workspace (+ Trigon + MDF) |
| v11.1 | Delta Lead + Moonshot | Cross-venue arb + flash crash |
| v11.0 | Sentinel Singularity | Fair Value + Anti-Flicker |
| v10.9 | Omniscient Predator | Macro monitor + Binance sync |
| v10.0 | Apex Predator | Dynamic grid + analytics |
