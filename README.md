# 🐺 SNIPER ARMADA v11.2 — Multi-Bot HFT Platform

**Modular high-frequency trading platform** for Bitfinex with cross-exchange intelligence, AI-driven grid management, and scalable multi-bot architecture.

## Architecture

Cargo workspace divided into isolated bot modules sharing a common type system.
**Every bot follows the same structure:**

```
sniper/
├── shared/              ← 🔧 Shared types crate (sniper_types)
│   └── src/
│       ├── types.rs             ← Hydra EngineState, RiskState
│       ├── moonshot_types.rs    ← Moonshot PairState[20], symbol hash
│       ├── grid_types.rs        ← Grid LevelState[10], arithmetic/geometric
│       ├── trigon_types.rs      ← Trigon TriangleState[10], leg calculator
│       ├── math.rs              ← Fixed-point PRICE_SCALE utilities
│       └── logging.rs           ← Tracing setup
│
│   ┌──────────────────────────────────────────────────────────┐
│   │  TEMPLATE: Every bot has identical structure              │
│   │  bot/                                                    │
│   │  ├── src/{main, dashboard, brain, config_cli}.rs         │
│   │  ├── scripts/{l1_shield, sniper_orchestrator,            │
│   │  │           tg_listener, macro_monitor}.py              │
│   │  ├── dashboard.html                                      │
│   │  └── bot-start.sh                                        │
│   └──────────────────────────────────────────────────────────┘
│
├── hydra/               ← 🐍 Bot #1: BTC-USD Delta Lead HFT (Core 0)
├── moonshot/            ← 🌙 Bot #2: Multi-Symbol Flash Crash (Core 1)
├── grid/                ← 📐 Bot #3: Dynamic Multi-Level Grid (Core 2)
├── trigon/              ← 🔺 Bot #4: Triangular Arbitrage (Core 3)
├── mdf/                 ← 🔄 Shared Market Data Feed (single WS)
├── architect/           ← 🏛️ Central orchestrator + Telegram Commander
│   ├── sniper_architect.py   ← Dashboard + REST API (:3004)
│   ├── tg_commander.py       ← Telegram C2 (slash + natural language)
│   └── master_dashboard.html ← Multi-bot web UI
│
├── Cargo.toml           ← Workspace root
├── .env                 ← API keys (shared)
└── sniper-armada.service ← systemd (auto-start on reboot)
```

## Three-Layer Architecture (per bot)

| Layer | Runtime | Role | Latency |
|-------|---------|------|---------|
| **L0** | Rust (async) | Execution, orderbook, order management | < 1ms |
| **L1** | Python | Tactical shield, volatility filters | ~50ms |
| **L2** | Python + Gemini | Strategic oracle, parameter tuning | ~5min |
| **Macro** | Python | Cross-venue data feed | real-time |

## Bots

### 🐍 Hydra (Bot #1) — BTC-USD Delta Lead
- **Core 0** · Port **:3000**
- Single-pair market making with ghost orders
- Delta Lead: cross-venue arbitrage (Binance→Bitfinex)
- Fee Sentinel: autonomous fee monitoring
- mmap: `engine_state.bin`, `risk_state.bin`

### 🌙 Moonshot (Bot #2) — Multi-Symbol Flash Crash
- **Core 1** · Port **:3001**
- 20 simultaneous pairs, AI-selected every 66 minutes
- Ghost BUY orders placed X% below market ("spike catching")
- Active Trade Lock: AI cannot rotate pairs with open positions
- mmap: `moonshot_engine.bin`, `moonshot_risk.bin`

### 📐 Grid (Bot #3) — Dynamic Multi-Level Grid
- **Core 2** · Port **:3002**
- N BUY + N SELL levels around center price (1-10 per side)
- **Arithmetic mode**: Fixed USD spacing (e.g., $1,200 per level)
- **Geometric mode**: Fixed % spacing (e.g., 1.5% per level)
- **ATR-based dynamic spacing**: AI recalculates via ATR/2 every 30 min
- **Consecutive loss halt**: 3 consecutive losses → 24h pause
- When BUY fills → place SELL at grid_spacing higher
- When SELL fills → place BUY at grid_spacing lower
- mmap: `grid_engine.bin`, `grid_risk.bin`
- Ported from HFT-Grid with upgraded Rust L0 engine

## Telegram Commands

### 🏛️ Commander v2.0 — Unified C2

Start: `python3 architect/tg_commander.py`

**Slash commands (all bots):**

| Command | Description |
|---------|-------------|
| `/hydra start` | Start Hydra |
| `/hydra stop` | Stop Hydra |
| `/hydra restart` | Restart Hydra |
| `/hydra pause` | Pause trading (bot keeps running) |
| `/hydra unpause` | Resume trading |
| `/moonshot start` | Start Moonshot |
| `/grid stop` | Stop Grid |
| `/trigon pause` | Pause Trigon |
| `/status` | All bots status |
| `/panic` | Emergency stop ALL bots |
| `/analyze` | Gemini AI analysis |
| `/help` | Show all commands |

Works for all bots: `hydra`, `moonshot`, `grid`, `trigon`

**Natural language (AI-powered):**

- _"Ahoj snipere, vypni hydru"_ → stops Hydra
- _"Spusť moonshot"_ → starts Moonshot
- _"Jak se daří?"_ → shows status
- _"Restartuj grid"_ → restarts Grid

### 🐍 Hydra-specific (legacy commands via tg_listener.py)

| Command | Description |
|---------|-------------|
| `/status` | Live engine state + equity + PnL |
| `/delta` | Delta Lead cross-venue status |
| `/fees` | Fee Sentinel status |
| `/analyze` | Gemini market analysis |
| `/oracle` | Force AI cycle |
| `/grid 8.5` | Set grid step |
| `/pause` / `/resume` | Emergency stop/start |
| `/close CONFIRM` | Emergency market close |

## Quick Start

```bash
# Build entire workspace
cargo build --release --workspace

# Start Hydra + Commander
cd /home/wwwenda/sniper
nohup taskset -c 0 ./target/release/hydra-core > logs/hydra-core.log 2>&1 &
nohup taskset -c 3 ./target/release/hydra-dashboard > logs/hydra-dashboard.log 2>&1 &
nohup python3 architect/tg_commander.py > logs/tg_commander.log 2>&1 &

# Or via systemd
sudo systemctl start sniper-armada.service

# Start other bots via Telegram:
# /moonshot start
# /grid start
# /trigon start
```

## Dashboard Ports

| Port | Bot | Content |
|------|-----|---------|
| :3000 | 🐍 Hydra | BTC-USD orderbook, sparklines, ghost grid |
| :3001 | 🌙 Moonshot | 20-pair live grid (symbol, price, drop%, PnL) |
| :3002 | 📐 Grid | Buy/Sell level grid with fill status |
| :3003 | 🔺 Trigon | Triangular arbitrage monitor |
| :3004 | 🏛️ Architect | Multi-bot command center |

## Safety

- **DLL Circuit Breaker**: Hardcoded daily loss limit halts trading
- **Fee Sentinel**: Auto-stops if fees exceed spread profit
- **Anti-Cross**: Bid always < best ask, ask always > best bid
- **AI Heartbeat**: Stale AI (>30s) → bias zeroed automatically
- **BTC Volatility Kill**: Moonshot/Grid auto-pause if BTC >4%/h
- **Active Trade Lock**: AI cannot rotate pairs with open positions
- **Consecutive Loss Halt**: Grid pauses after 3 consecutive losses

## Roadmap

| Phase | Status | Description |
|-------|--------|-------------|
| 1-3 | ✅ Done | Workspace + Hydra + Moonshot + Grid |
| 4 | ✅ Done | Deploy to Beroun server |
| 5 | ✅ Done | Trigon — Triangular Arbitrage |
| 6 | ✅ Done | Architect — Multi-bot dashboard + Telegram C2 |
| 7 | ✅ Done | MDF — Shared Market Data Feed |

## Version History

| Version | Codename | Key Feature |
|---------|----------|-------------|
| v11.2 | Full Armada | 4 bots + MDF + Architect + Telegram C2 |
| v11.1 | Delta Lead + Moonshot | Cross-venue arb + Multi-symbol flash crash |
| v11.0 | Sentinel Singularity | Fair Value + Anti-Flicker |
| v10.9 | Omniscient Predator | Macro monitor + Binance sync |
| v10.0 | Apex Predator | Dynamic grid + analytics |

## License

Private / Proprietary
