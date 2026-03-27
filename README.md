# 🐺 Beroun Sniper v10.0 "Apex Predator"

Vysokofrekvenční (HFT) market-making bot pro Bitfinex BTC/USD.
Rust 2024 • Zero-copy • Sub-ms latence • mmap IPC • Hydra Multi-Level Grid

## Architecture

```
┌───────────────────────────────────────────────────────┐
│                BEROUN SNIPER v10.0                     │
├───────────────────────────────────────────────────────┤
│  L0 REFLEX (Rust, <1ms)                               │
│  ├─ Dual WebSocket (Market Data + Execution)          │
│  ├─ Hydra Multi-Level Grid (1-5 levels, Fibonacci)    │
│  ├─ Adaptive Grid (fill-rate + volatility driven)     │
│  ├─ Liquidity Hole Detection (L2 depth, 60-sample MA) │
│  ├─ Inventory Throttling + Anti-Cross Guard           │
│  └─ Capital Guard + DLL Circuit Breaker               │
├───────────────────────────────────────────────────────┤
│  L1 TACTICAL SHIELD (Python, 1s cycle)                │
│  ├─ l1_shield.py  (OBI skewing, micro-skew, sweep)   │
│  ├─ mmap reader   (engine_state.bin → real-time OBI)  │
│  └─ mmap writer   (risk_state.bin → bias_offset)      │
├───────────────────────────────────────────────────────┤
│  IPC (mmap /dev/shm/beroun/ or runtime/)              │
│  ├─ engine_state.bin (EngineState, lock-free atomic)  │
│  └─ risk_state.bin  (RiskState, atomic params)        │
├───────────────────────────────────────────────────────┤
│  L2 STRATEGIC ORACLE (Gemini 3.1 Pro, 26h cycle)      │
│  ├─ oracle_brain.sh (macro analysis + RSS + F&G)      │
│  └─ beroun-config   (mmap parameter modifier)         │
├───────────────────────────────────────────────────────┤
│  C2 LAYER (Python + Telegram)                         │
│  ├─ tg_listener.py (Command & Control)                │
│  └─ analytics.py   (Trade Analytics Engine)           │
├───────────────────────────────────────────────────────┤
│  DASHBOARD (http://localhost:3000)                     │
│  └─ dashboard.html (Real-time metrics)                │
└───────────────────────────────────────────────────────┘
```

## Quick Start

```bash
# 1. Konfigurace
cp .env.example .env
# Vyplň: BITFINEX_API_KEY, BITFINEX_API_SECRET, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

# 2. Build
cargo build --release

# 3. Spuštění
sudo systemctl start beroun-sniper   # Spustí beroun-start.sh (engine + dashboard + L1 + tg_listener)

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
│   ├── main.rs            # L0 HFT engine (Dual WS, Hydra Grid, Adaptive Grid)
│   ├── types.rs           # Shared types (EngineState, RiskState, mmap structs)
│   ├── config_cli.rs      # beroun-config CLI (mmap parameter modifier)
│   ├── dashboard.rs       # HTTP dashboard server (:3000)
│   ├── dump_offsets.rs    # Debug: mmap offset validator
│   └── risk_control.rs   # Risk control binary
├── scripts/
│   ├── l1_shield.py       # L1 Tactical Shield (OBI→skew, sweep detection)
│   ├── tg_listener.py     # Telegram C2 interface
│   ├── analytics.py       # Trade Analytics Engine
│   └── oracle_brain.sh    # L2 Gemini Oracle scheduler
├── docs/
│   ├── AI_INFRASTRUCTURE.md   # AI stack documentation
│   ├── HFT_AUDIT_2026.md     # Architecture audit & standards
│   ├── MONITORING.md          # Observability & metrics
│   ├── RESEARCH.md            # HFT research notes
│   └── STRATEGY.md            # Trading strategy documentation
├── beroun-start.sh        # Orchestrator (startup script)
├── watchdog.sh            # mmap heartbeat monitor
├── optimize.sh            # PGO optimization script
├── dashboard.html         # Web dashboard UI
├── beroun-sniper.service  # systemd service unit
├── ARCHITECTURE.md        # Detailed architecture docs
├── ROADMAP.md             # Future development plan
├── CONTRIBUTING.md        # Development standards
└── .env                   # API keys (gitignored)
```

## Key Features (v10.0)

### L0 — Rust HFT Engine
- **Hydra Multi-Level Grid**: 1-5 concurrent price levels with Fibonacci spacing
- **Adaptive Grid**: Fill-rate balance drives grid tightening/widening (0.7×-1.5×)
- **Liquidity Hole Detection**: L2 depth monitor with 60-sample MA, auto grid ×3 on holes
- **Micro-Price**: Volume-weighted mid-price for directional prediction
- **L2 OBI**: 10-level order book imbalance (-1.0 to +1.0)
- **Dynamic Sizing**: Signal convergence drives order size (0.5×-2.0×)
- **Multi-Pair Foundation**: `TRADING_SYMBOL` abstraction (0 hardcoded pairs)

### L1 — Tactical Shield (Python)
- **OBI Skewing**: Reads real-time OBI from mmap, writes bias_offset to risk_state
- **Micro-Skew**: Posouvá bidy dolů při sell pressure (ochrana před padajícím nožem)
- **Sweep Detection**: Detekce toxických large-order sweepů
- **1s Cycle**: Low-latency mmap IPC, no network overhead

### L2 — Strategic Oracle (Gemini)
- **26h Macro Cycle**: RSS feeds + Fear & Greed Index + market analysis
- **Automated Tuning**: Grid, max position, risk level adjustments
- **3× Safety**: beroun-config applies triple safety validation

### Operations
- **Emergency Close**: `/close CONFIRM` — REST API market close (works even if WS is down)
- **Cautious Mode**: `/cautious` — 15-min defensive mode before macro events
- **Total Equity**: `wallet_usd + (wallet_btc × mid_price)` — true NAV tracking
- **Trade Analytics**: Sharpe Ratio, win-rate, hourly PnL heatmap, max drawdown
- **Volume Tracking**: 30-day volume accumulation for fee tier optimization
- **Auto Daily Report**: 08:00 CET automatic Telegram summary

## Safety Systems

| # | System | Trigger | Action |
|---|--------|---------|--------|
| 1 | Capital Guard | Hard USD cap per order cycle | Prevents oversized orders |
| 2 | Daily Loss Limit | -$20 daily loss (configurable) | Circuit breaker, auto-pause |
| 3 | Anti-Cross Guard | bid > best_ask or ask < best_bid | Order rejected |
| 4 | Inventory Throttling | Net position skew | Grid bias to reduce exposure |
| 5 | Delta-Reducing Exemption | Position-reducing trade | Always allowed |
| 6 | Checksum Validation | 5 consecutive failures | WS reconnect |
| 7 | AI Heartbeat Fuse | L1 heartbeat > 30s stale | Bias zeroed, pure grid |
| 8 | L1 Sweep Guard | Toxic large-order sweep detected | Temporarily widens grid |

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
