# 🐺 SNIPER ARMADA v11.1 — Multi-Bot HFT Platform

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
├── trigon/              ← 🔺 Bot #3: Triangular Arbitrage (PLANNED)
├── architect/           ← 🏛️ Central orchestrator (PLANNED)
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

### 🔺 Trigon (Bot #3) — Triangular Arbitrage (PLANNED)
- **Core 1** (shared with Moonshot) · Port **:3002**

## Telegram Commands

| Command | Bot | Description |
|---------|-----|-------------|
| `/status` | Hydra | Live engine state + equity + PnL |
| `/delta` | Hydra | Delta Lead cross-venue status |
| `/fees` | Hydra | Fee Sentinel status |
| `/moonshot` | Moonshot | Quick status (pairs, PnL, kill switch) |
| `/mpairs` | Moonshot | List active pairs with parameters |
| `/mkill` | Moonshot | Toggle Moonshot kill switch |

## Quick Start

```bash
# Build entire workspace
cargo build --release --workspace

# Start all bots
./deploy_armada.sh

# Or start individually
cd hydra && ./hydra-start.sh
cd moonshot && ./moonshot-start.sh

# Or via systemd
sudo systemctl start sniper-armada.service
```

## mmap Layout

### Hydra (`engine_state.bin`)
| Offset | Type | Field | Description |
|--------|------|-------|-------------|
| 1856 | i64 | binance_mid_price | BNB VWAP × PRICE_SCALE |
| 1864 | i64 | global_fair_value | Weighted FV × PRICE_SCALE |
| 1888 | i64 | delta_lead_signal | Cross-venue direction |

### Moonshot (`moonshot_engine.bin`)
| Offset | Type | Field | Description |
|--------|------|-------|-------------|
| 0..2559 | PairEngine[20] | pairs | 128 bytes each × 20 |
| 2560 | u64 | wallet_btc | BTC balance × PRICE_SCALE |
| 2568 | u64 | wallet_usd | USD balance × PRICE_SCALE |
| 2576 | u64 | total_fills | Global fill counter |
| 2584 | i64 | daily_pnl | Daily PnL × PRICE_SCALE |

## Dashboard Ports

| Port | Bot | Content |
|------|-----|---------|
| :3000 | Hydra | BTC-USD orderbook, sparklines, ghost grid |
| :3001 | Moonshot | 20-pair live grid (symbol, price, drop%, PnL) |
| :3002 | Trigon | Triangle view (PLANNED) |
| :3003 | Architect | Aggregated multi-bot view (PLANNED) |

## Safety

- **DLL Circuit Breaker**: Hardcoded daily loss limit halts trading
- **Fee Sentinel**: Auto-stops if fees exceed spread profit
- **Anti-Cross**: Bid always < best ask, ask always > best bid
- **AI Heartbeat**: Stale AI (>30s) → bias zeroed automatically
- **BTC Volatility Kill**: Moonshot auto-pauses if BTC >4%/h
- **Active Trade Lock**: AI cannot rotate pairs with open positions

## Version History

| Version | Codename | Key Feature |
|---------|----------|-------------|
| v11.1 | Delta Lead + Moonshot | Cross-venue arb + Multi-symbol flash crash |
| v11.0 | Sentinel Singularity | Fair Value + Anti-Flicker |
| v10.9 | Omniscient Predator | Macro monitor + Binance sync |
| v10.6 | Ghost Shadow | Hidden liquidity (IOC injection) |
| v10.0 | Apex Predator | Dynamic grid + analytics |

## License

Private / Proprietary
