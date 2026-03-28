# 🐺 SNIPER ARMADA v13.0 — Sovereign Intelligence

**Autonomous high-frequency trading platform** for Bitfinex with cross-exchange intelligence, AI-driven grid management, volatility-neutral FIFO PnL, and unified Telegram C2.

## Architecture

```
sniper/
├── shared/              ← 🔧 Shared types crate (sniper_types)
│   ├── src/
│   │   ├── types.rs             ← Hydra EngineState, RiskState (repr(C, align(64)))
│   │   ├── moonshot_types.rs    ← Moonshot PairState[20]
│   │   ├── grid_types.rs        ← Grid LevelState[10]
│   │   ├── trigon_types.rs      ← Trigon TriangleState[10]
│   │   ├── pnl_types.rs         ← PnlBotState, PnlGlobalState
│   │   ├── math.rs              ← Fixed-point PRICE_SCALE (1e8) utilities
│   │   └── logging.rs           ← Tracing setup (JSON, daily rotation)
│   └── pnl_engine.py           ← FIFO PnL Engine + SQLite + mmap writer
│
├── hydra/               ← 🐍 Bot #1: BTC-USD Delta Lead HFT (Core 0, :3000)
│   ├── src/main.rs              ← L0 Engine (1,434 LOC, Rust hot loop)
│   ├── src/brain.rs             ← SQLite analytics (1,099 LOC)
│   ├── src/dashboard.rs         ← SSE Dashboard server (816 LOC)
│   ├── src/config_cli.rs        ← Live config via mmap (247 LOC)
│   ├── scripts/l1_shield.py     ← L1 Tactical AI (OBI guard, sweep, ghost)
│   ├── scripts/sniper_orchestrator.py  ← L2 Sovereign Oracle (Gemini)
│   └── scripts/macro_monitor.py ← Cross-venue data (Binance sync)
│
├── moonshot/            ← 🌙 Bot #2: Multi-Symbol Flash Crash (Core 1, :3001)
├── grid/                ← 📐 Bot #3: Dynamic Multi-Level Grid (Core 2, :3002)
├── trigon/              ← 🔺 Bot #4: Triangular Arbitrage (Core 3, :3003)
├── mdf/                 ← 🔄 Shared Market Data Feed
│
├── architect/           ← 🏛️ Central Orchestrator
│   ├── sniper_architect.py      ← Master Dashboard + REST API (:3004)
│   ├── tg_commander.py          ← Telegram Commander v2.0 (slash + NL)
│   ├── pnl_daemon.py            ← PnL Daemon (FIFO, 30s cycles)
│   └── master_dashboard.html    ← Multi-bot web UI
│
├── deploy_armada.sh     ← Master launch script
├── sniper-armada.service ← systemd (auto-start, FIFO scheduler)
├── Cargo.toml           ← Workspace root (Rust 2024 Edition)
└── .env                 ← API keys (BITFINEX + TELEGRAM)
```

## Three-Layer Architecture (per bot)

| Layer | Runtime | Role | Latency |
|-------|---------|------|---------|
| **L0** | Rust (async, CPU-pinned) | Execution, orderbook, order management | < 1ms |
| **L1** | Python | Tactical shield, OBI skewing, sweep detection | ~50ms |
| **L2** | Python + Gemini | Sovereign Oracle, parameter tuning, nightly self-critique | ~5min |

## Bots

### 🐍 Hydra (Bot #1) — BTC-USD Delta Lead
- **Core 0** · Port **:3000** · Full AI stack (L1 + L2 + Macro)
- Single-pair market making with adaptive ghost orders
- Delta Lead: cross-venue arbitrage prediction (Binance→Bitfinex)
- Fee Sentinel: autonomous fee monitoring via Bitfinex REST
- Anti-Cross Guard, AI Safety Fuse, Capital Circuit Breaker

### 🌙 Moonshot (Bot #2) — Multi-Symbol Flash Crash
- **Core 1** · Port **:3001** · 20 simultaneous pairs
- AI-selected pair rotation every 66 minutes
- Ghost BUY orders placed X% below market ("spike catching")
- Active Trade Lock: AI cannot rotate pairs with open positions

### 📐 Grid (Bot #3) — Dynamic Multi-Level Grid
- **Core 2** · Port **:3002** · N BUY + N SELL levels
- Arithmetic or geometric spacing, ATR-based dynamic adjustment
- Consecutive Loss Halt: 3 losses → 24h automatic pause

### 🔺 Trigon (Bot #4) — Triangular Arbitrage
- **Core 3** · Port **:3003** · N-triangle monitoring
- A→B→C→A when implied_rate > 1 + 3×fee
- Atomic 3-leg execution

## 💰 PnL Engine (v2.0 — Volatility-Neutral)

**FIFO-based Trade-Level Realized PnL Aggregation.**
Immune to BTC price volatility — measures only strategy performance.

- **FIFO matching** (First In, First Out)
- **USD fixation** at execution millisecond
- **Time windows**: 1h / 24h / 7d / 30d
- **SQLite**: `~/.local/share/sniper/pnl.db`
- **mmap**: `/dev/shm/beroun/pnl_state.bin`
- **Telegram**: `/pnl` command

## 📱 Telegram Commander v2.0

| Command | Description |
|---------|-------------|
| `/hydra start\|stop\|restart\|pause` | Manage Hydra |
| `/moonshot start\|stop\|restart` | Manage Moonshot |
| `/grid start\|stop\|restart` | Manage Grid |
| `/trigon start\|stop\|restart` | Manage Trigon |
| `/status` | All bots status overview |
| `/pnl` | FIFO PnL report (volatility-neutral) |
| `/panic` | Emergency: stop ALL bots |
| `/analyze` | Gemini AI market analysis |
| `/help` | Show all commands |
| *Natural language* | _"Ahoj snipere, vypni hydru"_ |

## Safety Architecture

```
┌─── NON-OVERRIDABLE (Hardcoded) ─────────────────────────────┐
│  • Daily Loss Limit (DLL) circuit breaker                    │
│  • Max position size (capital guard)                         │
│  • Anti-Cross Guard (bid < best_ask, ask > best_bid)        │
│  • Fee Sentinel: spread < 2× fees → skip cycle             │
│  • AI Heartbeat > 30s → bias zeroed (Hydra)                 │
│  • AI Heartbeat > 120min → safe mode (Moonshot)             │
│  • BTC Volatility Kill > 4%/h (Moonshot, Grid)             │
│  • Active Trade Lock: no pair rotation with open position    │
│  • Consecutive Loss Halt: 3 losses → 24h pause (Grid)      │
│  • Telegram PANIC: /panic → stops ALL bots                  │
│  • Instance Lock: fs2 exclusive file lock (no dual-trade)   │
└─────────────────────────────────────────────────────────────┘
```

## IPC Map

| Path | Written By | Read By |
|------|-----------|---------|
| `/dev/shm/beroun/engine_state.bin` | hydra-core | dashboard, brain, l1_shield, orchestrator |
| `/dev/shm/beroun/risk_state.bin` | l1_shield, orchestrator | hydra-core |
| `/dev/shm/beroun/moonshot_engine.bin` | moonshot-core | dashboard, brain |
| `/dev/shm/beroun/moonshot_risk.bin` | AI sidecar | moonshot-core |
| `/dev/shm/beroun/grid_engine.bin` | grid-core | dashboard, brain |
| `/dev/shm/beroun/grid_risk.bin` | AI sidecar | grid-core |
| `/dev/shm/beroun/trigon_engine.bin` | trigon-core | dashboard, brain |
| `/dev/shm/beroun/trigon_risk.bin` | AI sidecar | trigon-core |
| `/dev/shm/beroun/pnl_state.bin` | pnl_daemon | all dashboards, architect |
| `/dev/shm/beroun/mdf.bin` | mdf | pnl_daemon, trigon |

## Quick Start

```bash
# Build
cargo build --release --workspace

# Deploy (Hydra + Architect + Commander + PnL)
./deploy_armada.sh

# Or via systemd (survives reboot)
sudo cp sniper-armada.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sniper-armada

# Start other bots via Telegram:
# /moonshot start
# /grid start
# /trigon start
```

## Hardware Profile (Beroun Server)

| Resource | Spec | Allocation |
|----------|------|------------|
| CPU | i5-6400 (4 cores, no HT) | Core 0: Hydra, Core 1: Moonshot, Core 2: Grid, Core 3: OS + AI |
| RAM | 16 GB | ~4 GB used (4 bots), 12 GB available |
| GPU | GTX 1060 6GB | NVIDIA MPS for shared AI inference |
| SHM | 7.6 GB tmpfs | 10 mmap files for zero-copy IPC |

## Version History

| Version | Codename | Key Feature |
|---------|----------|-------------|
| v13.0 | Sovereign Intelligence | Zero-Debt Audit + unified v13.0 standard |
| v12.0 | Armada PnL | PnL FIFO Engine + Telegram Commander v2.0 |
| v11.2 | Full Armada | 4-bot workspace (Hydra + Moonshot + Grid + Trigon + MDF) |
| v11.0 | Sentinel Singularity | Fair Value + Anti-Flicker |
| v10.0 | Apex Predator | Dynamic grid + analytics |

## License

Private / Proprietary
