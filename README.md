# 🐺 SNIPER ARMADA v12.0 — Multi-Bot HFT Platform

**Autonomous high-frequency trading platform** for Bitfinex with cross-exchange intelligence, AI-driven grid management, FIFO PnL tracking, and unified Telegram C2.

## Architecture

```
sniper/
├── shared/              ← 🔧 Shared types crate (sniper_types)
│   ├── src/
│   │   ├── types.rs             ← Hydra EngineState, RiskState
│   │   ├── moonshot_types.rs    ← Moonshot PairState[20]
│   │   ├── grid_types.rs        ← Grid LevelState[10]
│   │   ├── trigon_types.rs      ← Trigon TriangleState[10]
│   │   ├── pnl_types.rs         ← PnlBotState, PnlGlobalState
│   │   ├── math.rs              ← Fixed-point PRICE_SCALE utilities
│   │   └── logging.rs           ← Tracing setup
│   └── pnl_engine.py           ← FIFO PnL Engine + SQLite + mmap writer
│
├── hydra/               ← 🐍 Bot #1: BTC-USD Delta Lead HFT (Core 0, :3000)
├── moonshot/            ← 🌙 Bot #2: Multi-Symbol Flash Crash (Core 1, :3001)
├── grid/                ← 📐 Bot #3: Dynamic Multi-Level Grid (Core 2, :3002)
├── trigon/              ← 🔺 Bot #4: Triangular Arbitrage (Core 3, :3003)
├── mdf/                 ← 🔄 Shared Market Data Feed
│
├── architect/           ← 🏛️ Central Orchestrator
│   ├── sniper_architect.py    ← Master Dashboard + REST API (:3004)
│   ├── tg_commander.py        ← Telegram Commander v2.0 (slash + AI)
│   ├── pnl_daemon.py          ← PnL Daemon (FIFO, 30s cycles)
│   ├── start_commander.sh     ← Commander launcher
│   └── master_dashboard.html  ← Multi-bot web UI
│
├── deploy_armada.sh     ← Master launch (Hydra + Architect + Commander + PnL)
├── sniper-armada.service ← systemd (auto-start on reboot)
├── Cargo.toml           ← Workspace root
└── .env                 ← API keys (BITFINEX + TELEGRAM)
```

## Three-Layer Architecture (per bot)

| Layer | Runtime | Role | Latency |
|-------|---------|------|---------|
| **L0** | Rust (async) | Execution, orderbook, order management | < 1ms |
| **L1** | Python | Tactical shield, volatility filters | ~50ms |
| **L2** | Python + Gemini | Strategic oracle, parameter tuning | ~5min |

## Bots

### 🐍 Hydra (Bot #1) — BTC-USD Delta Lead
- **Core 0** · Port **:3000**
- Single-pair market making with ghost orders
- Delta Lead: cross-venue arbitrage (Binance→Bitfinex)
- Fee Sentinel: autonomous fee monitoring
- Full AI stack (L1 Shield, L2 Orchestrator, Macro Monitor)

### 🌙 Moonshot (Bot #2) — Multi-Symbol Flash Crash
- **Core 1** · Port **:3001**
- 20 simultaneous pairs, AI-selected every 66 minutes
- Ghost BUY orders placed X% below market ("spike catching")
- Active Trade Lock: AI cannot rotate pairs with open positions

### 📐 Grid (Bot #3) — Dynamic Multi-Level Grid
- **Core 2** · Port **:3002**
- N BUY + N SELL levels, arithmetic or geometric spacing
- ATR-based dynamic spacing: AI recalculates via ATR/2
- Consecutive loss halt: 3 losses → 24h pause

### 🔺 Trigon (Bot #4) — Triangular Arbitrage
- **Core 3** · Port **:3003**
- Monitors N triangles (A→B→C→A) for cross-pair inefficiencies
- When implied_rate > 1 + 3×fee → execute all 3 legs atomically

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

## Safety

- **DLL Circuit Breaker**: Hardcoded daily loss limit halts trading
- **Fee Sentinel**: Auto-stops if fees exceed spread profit
- **Anti-Cross**: Bid always < best ask, ask always > best bid
- **AI Heartbeat**: Stale AI (>30s) → bias zeroed automatically
- **BTC Volatility Kill**: Moonshot/Grid auto-pause if BTC >4%/h
- **Active Trade Lock**: AI cannot rotate pairs with open positions
- **Consecutive Loss Halt**: Grid pauses after 3 consecutive losses
- **Telegram PANIC**: `/panic` → stops ALL bots instantly

## Quick Start

```bash
# Build
cargo build --release --workspace

# Deploy (Hydra + Architect + Commander + PnL)
./deploy_armada.sh

# Or via systemd (survives reboot)
sudo systemctl start sniper-armada

# Start other bots via Telegram:
# /moonshot start
# /grid start
# /trigon start
```

## Version History

| Version | Codename | Key Feature |
|---------|----------|-------------|
| v12.0 | Sovereign Intelligence | PnL FIFO Engine + Telegram Commander v2.0 |
| v11.2 | Full Armada | 4-bot workspace (Hydra + Moonshot + Grid + Trigon + MDF) |
| v11.1 | Delta Lead + Moonshot | Cross-venue arb + flash crash |
| v11.0 | Sentinel Singularity | Fair Value + Anti-Flicker |
| v10.0 | Apex Predator | Dynamic grid + analytics |

## License

Private / Proprietary
