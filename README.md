# 🐺 Beroun Sniper v10.0 "Apex Predator"

Vysokofrekvenční (HFT) market-making bot pro Bitfinex BTC/USD.
Rust 2024 • Zero-copy • Sub-ms latence • mmap IPC • Hydra Multi-Level Grid

## Architecture

```
┌──────────────────────────────────────────────────┐
│               BEROUN SNIPER v10.0                │
├──────────────────────────────────────────────────┤
│  L0 REFLEX (Rust, <1ms)                          │
│  ├─ Dual WebSocket (Market Data + Exec)          │
│  ├─ Hydra Multi-Level Grid (1-5 levels)          │
│  ├─ Adaptive Grid (fill-rate + volatility)       │
│  ├─ Liquidity Hole Detection (L2 depth)          │
│  ├─ Inventory Throttling + Anti-Cross Guard      │
│  └─ Capital Guard + DLL Circuit Breaker          │
├──────────────────────────────────────────────────┤
│  IPC (mmap /dev/shm/beroun/)                     │
│  ├─ engine_state.bin (EngineState, lock-free)    │
│  └─ risk_state.bin  (RiskState, atomic params)   │
├──────────────────────────────────────────────────┤
│  C2 LAYER (Python + Telegram)                    │
│  ├─ tg_listener.py (Command & Control)           │
│  ├─ analytics.py   (Trade Analytics Engine)      │
│  └─ oracle_brain.sh (Gemini 3.1 Pro Oracle)      │
├──────────────────────────────────────────────────┤
│  DASHBOARD (http://localhost:3000)                │
│  └─ dashboard.html (Real-time metrics)           │
└──────────────────────────────────────────────────┘
```

## Quick Start

```bash
# 1. Konfigurace
cp .env.example .env
# Vyplň: BITFINEX_API_KEY, BITFINEX_API_SECRET, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

# 2. Build
cargo build --release

# 3. Spuštění
sudo systemctl start beroun-sniper   # Spustí beroun-start.sh (engine + dashboard + tg_listener)

# 4. Dashboard
open http://localhost:3000

# 5. Telegram — pošli /help tvému botovi
```

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/status` | Live equity, PnL, pozice |
| `/report` | Denní report (obchody, equity) |
| `/analytics` | Trade analytics (Sharpe, win-rate, heatmap) |
| `/close CONFIRM` | 🚨 Emergency market close + auto-pause |
| `/cautious` | Macro-event defense mode (15 min) |
| `/grid <N>` | Nastavit grid step (USD) |
| `/capital <N>` | Autorizovaný kapitál (USD) |
| `/loss <N>` | Denní loss limit (USD) |
| `/pause` / `/resume` | Nouzové zastavení / obnovení |
| `/analyze` | Gemini 3.1 Pro analýza trhu |
| `/oracle` | Vynutit Oracle cyklus |

## Project Structure

```
hft-sniper/
├── src/
│   ├── main.rs          # L0 HFT engine (Dual WS, Hydra Grid, Adaptive Grid)
│   ├── types.rs          # Shared types (EngineState, RiskState, mmap structs)
│   ├── config_cli.rs     # beroun-config CLI (mmap parameter modifier)
│   ├── dashboard.rs      # HTTP dashboard server
│   └── risk_control.rs   # Risk control binary
├── scripts/
│   ├── tg_listener.py    # Telegram C2 interface
│   ├── analytics.py      # Trade Analytics Engine
│   └── oracle_brain.sh   # Gemini Oracle scheduler
├── beroun-start.sh       # Orchestrator (startup script)
├── dashboard.html        # Web dashboard UI
├── ROADMAP.md            # Future development plan
└── ARCHITECTURE.md       # Detailed architecture docs
```

## Key Features (v10.0)

- **Hydra Multi-Level Grid**: 1-5 concurrent price levels with Fibonacci spacing
- **Adaptive Grid**: Fill-rate balance drives grid tightening/widening (0.7×-1.5×)
- **Liquidity Hole Detection**: L2 depth monitor with 60-sample MA, auto grid ×3 on holes
- **Emergency Close**: `/close CONFIRM` — REST API market close (works even if WS is down)
- **Cautious Mode**: `/cautious` — 15-min defensive mode before macro events
- **Total Equity**: `wallet_usd + (wallet_btc × mid_price)` — true NAV tracking
- **Trade Analytics**: Sharpe Ratio, win-rate, hourly PnL heatmap, max drawdown
- **Volume Tracking**: 30-day volume accumulation for fee tier optimization
- **Multi-Pair Foundation**: `TRADING_SYMBOL` abstraction (0 hardcoded pair references)
- **Auto Daily Report**: 08:00 CET automatic Telegram summary

## Safety Systems

1. **Capital Guard**: Hard USD cap per order cycle
2. **Daily Loss Limit**: Circuit breaker (-$20 default)
3. **Anti-Cross Guard**: Prevents bid > best_ask or ask < best_bid
4. **Inventory Throttling**: Skews grid based on net position
5. **Delta-Reducing Exemption**: Always allows position-reducing trades
6. **Checksum Validation**: 5-consecutive failures before reconnect
7. **AI Safety Fuse**: Zeros AI bias if heartbeat > 30s stale

## Version History

| Version | Codename | Key Features |
|---------|----------|-------------|
| v10.0 | Apex Predator | Analytics, Fee Optimizer, Multi-Pair |
| v9.5 | Sentinel | Adaptive Grid, Liquidity Hole, Cautious Mode |
| v9.2 | Accountant | Emergency Close, Total Equity, Daily Report |
| v9.0 | Silent Hydra | Multi-Level Grid, Capital Guard, DLL |
| v8.0 | Sovereign | Dual WS, AI Sidecar, Gemini Oracle |

## License

Private / All Rights Reserved
