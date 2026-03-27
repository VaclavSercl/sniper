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
├── architect/           ← 🏛️ Central orchestrator (Phase 6)
├── trigon/              ← 🔺 Bot #4: Triangular Arbitrage (Phase 5)
│
├── Cargo.toml           ← Workspace root
├── .env                 ← API keys (shared)
└── deploy_armada.sh     ← Master startup with CPU pinning
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

| Command | Bot | Description |
|---------|-----|-------------|
| `/status` | Hydra | Live engine state + equity + PnL |
| `/delta` | Hydra | Delta Lead cross-venue status |
| `/fees` | Hydra | Fee Sentinel status |
| `/moonshot` | Moonshot | Quick status (pairs, PnL, kill switch) |
| `/mpairs` | Moonshot | List active pairs with parameters |
| `/mkill` | Moonshot | Toggle Moonshot kill switch |
| `/grid` | Grid | Grid status (levels, spacing, PnL) |
| `/gkill` | Grid | Toggle Grid kill switch |

## Quick Start

```bash
# Build entire workspace
cargo build --release --workspace

# Start all bots
./deploy_armada.sh

# Or start individually
cd hydra && ./hydra-start.sh
cd moonshot && ./moonshot-start.sh
cd grid && ./grid-start.sh

# Or via systemd
sudo systemctl start sniper-armada.service
```

## Dashboard Ports

| Port | Bot | Content |
|------|-----|---------|
| :3000 | Hydra | BTC-USD orderbook, sparklines, ghost grid |
| :3001 | Moonshot | 20-pair live grid (symbol, price, drop%, PnL) |
| :3002 | Grid | Buy/Sell level grid with fill status |
| :3003 | Architect | Aggregated multi-bot view (PLANNED) |

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
| 4 | 🎯 Next | Deploy Moonshot + Grid na Beroun server |
| 5 | 🔲 | Trigon (Bot #4) — Triangular Arbitrage |
| 6 | 🔲 | Architect — multi-bot PnL dashboard |
| 7 | 🔲 | Shared MDF — single WebSocket process |

## Version History

| Version | Codename | Key Feature |
|---------|----------|-------------|
| v11.2 | Full Armada | Hydra + Moonshot + Grid (3-bot workspace) |
| v11.1 | Delta Lead + Moonshot | Cross-venue arb + Multi-symbol flash crash |
| v11.0 | Sentinel Singularity | Fair Value + Anti-Flicker |
| v10.9 | Omniscient Predator | Macro monitor + Binance sync |
| v10.0 | Apex Predator | Dynamic grid + analytics |

## License

Private / Proprietary
