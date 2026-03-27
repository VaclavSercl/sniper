# 🐺 SNIPER ARMADA v11.1 — Multi-Bot HFT Platform

**Modular high-frequency trading platform** for Bitfinex with cross-exchange intelligence, AI-driven grid management, and scalable multi-bot architecture.

## Architecture

Cargo workspace divided into isolated bot modules sharing a common type system:

```
sniper/
├── shared/              ← 🔧 Shared types crate (sniper_types)
│   └── src/types.rs     ← EngineState, RiskState, mmap constants
│
├── hydra/               ← 🐍 Bot #1: BTC-USD Delta Lead HFT
│   ├── src/
│   │   ├── main.rs      ← L0 Engine: WebSocket, execution, ghost injection
│   │   ├── dashboard.rs ← HTMX real-time dashboard (port 3000)
│   │   ├── brain.rs     ← SQLite persistence, lesson engine
│   │   └── config_cli.rs← Live parameter modifier (mmap)
│   ├── scripts/
│   │   ├── l1_shield.py         ← L1: OBI, sweep detection, ghost mode
│   │   ├── sniper_orchestrator.py ← L2: Gemini Oracle + Fee Sentinel
│   │   ├── tg_listener.py       ← Telegram command interface
│   │   └── macro_monitor.py     ← Binance shadow stream + F&G + RSS
│   └── dashboard.html   ← HTMX dashboard template
│
├── trigon/              ← 🔺 Bot #2: Triangular Arbitrage (PLANNED)
├── architect/           ← 🏛️ Central orchestrator (PLANNED)
│
├── Cargo.toml           ← Workspace root
├── .env                 ← API keys (shared)
└── deploy_armada.sh     ← Master startup with CPU pinning (PLANNED)
```

## Three-Layer Architecture (per bot)

| Layer | Runtime | Role | Latency |
|-------|---------|------|---------|
| **L0** | Rust (async) | Execution, orderbook, ghost injection, delta lead | < 1ms |
| **L1** | Python | OBI Shield, sweep detection, ghost mode | ~50ms |
| **L2** | Python + Gemini | Oracle, strategy, parameter tuning, fee monitoring | ~5min |
| **Macro** | Python | Binance sync, F&G, news sentiment | real-time |

## Key Features (Hydra v11.1)

### Delta Lead (v11.1) — Cross-Venue Arbitrage
- **Binance Shadow Stream**: Real-time mid-price from Binance Futures
- **Delta Calculation**: Binance vs Bitfinex delta in basis points
- **Reactive Grid Shift**: Repositions grid center toward expected convergence
- **Dashboard Overlay**: Buy/Sell grid lines + Binance mid on price chart

### Fee Sentinel (v11.3) — Autonomous Fee Guard
- **Hourly API Check**: Polls Bitfinex `/auth/r/summary` for current fees
- **Profitability Guard**: Skips trades where spread < 2× round-trip fees
- **Telegram Alert**: Instant notification if Bitfinex changes fee structure
- **Current State**: Maker 0% / Taker 0% (zero fees since Dec 2025)

### Sentinel Defense Engine (v11.0)
- **Global Fair Value**: 60% local + 30% Binance VWAP + 10% sentiment
- **Ghost Reposition**: Shadow grid shift when FV diverges > 0.05%
- **Anti-Flicker Guard**: Minimum order lifetime prevents exchange flags

### Ghost Orders (Shadow Liquidity)
- Only 1 public "tip" order visible per side
- Shadow levels fire as IOC when micro-price enters trigger zone
- Velocity-based anti-toxic rejection prevents adverse fills

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/status` | Live engine state + equity + PnL |
| `/delta` | ⚡ Delta Lead cross-venue arbitrage status |
| `/fees` | 💰 Fee Sentinel v11.3 status |
| `/macro` | Macro intelligence (bias, F&G, Binance sweep) |
| `/ai` | AI Shield + Ghost + Sovereign state |
| `/aggressive` | Intent: tight grid, fast fire |
| `/defensive` | Intent: wide grid, ghost ON |
| `/sovereign` | Intent: full AI autonomy (default) |
| `/close CONFIRM` | 🚨 Emergency close all positions |

## Quick Start

```bash
# Build entire workspace
cargo build --release --workspace

# Start Hydra bot
cd hydra && ./hydra-start.sh

# Or via systemd
sudo systemctl start sniper-armada.service
```

## mmap Layout (EngineState)

All fields are lock-free atomics. Key offsets:

| Offset | Type | Field | Description |
|--------|------|-------|-------------|
| 1856 | i64 | binance_mid_price | BNB VWAP × PRICE_SCALE |
| 1864 | i64 | global_fair_value | Weighted FV × PRICE_SCALE |
| 1888 | i64 | delta_lead_signal | Cross-venue direction signal |
| 1896 | i64 | delta_lead_raw_bps | Delta in basis points × 100 |
| 1904 | u64 | delta_repositions | Grid shifts from delta lead |
| 1928 | u64 | maker_fee_bps | Maker fee × 10000 |
| 1936 | u64 | taker_fee_bps | Taker fee × 10000 |
| 1952 | u64 | fee_kills | Trades skipped (fee > profit) |

## Safety

- **DLL Circuit Breaker**: Hardcoded daily loss limit halts trading
- **Fee Sentinel**: Auto-stops if fees exceed spread profit
- **Anti-Cross**: Bid always < best ask, ask always > best bid
- **AI Heartbeat**: Stale AI (>30s) → bias zeroed automatically
- **Ghost Velocity**: Fast-moving toxic flow rejected from injection

## Version History

| Version | Codename | Key Feature |
|---------|----------|-------------|
| v11.1 | Delta Lead | Cross-venue arbitrage (Binance→Bitfinex) |
| v11.3 | Fee Sentinel | Autonomous fee monitoring + profitability guard |
| v11.0 | Sentinel Singularity | Fair Value + Anti-Flicker + Reactive defense |
| v10.9 | Omniscient Predator | Macro monitor + Binance sync |
| v10.6 | Ghost Shadow | Hidden liquidity (IOC injection) |
| v10.0 | Apex Predator | Dynamic grid + analytics |

## License

Private / Proprietary
