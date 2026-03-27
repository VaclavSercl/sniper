# 🐺 Beroun Sniper v10.1 "Apex Predator — Overlord"

Vysokofrekvenční (HFT) market-making bot pro Bitfinex BTC/USD.
Rust 2024 • True Zero-Copy (FastWebSockets) • Sub-µs latence • mmap IPC • Cross-Layer AI Intelligence

## Architecture

```
┌───────────────────────────────────────────────────────┐
│           BEROUN SNIPER v10.1 "OVERLORD"               │
├───────────────────────────────────────────────────────┤
│  L0 REFLEX (Rust, <1µs)                               │
│  ├─ FastWebSockets (True Zero-Copy, SIMD JSON)        │
│  ├─ Hydra Multi-Level Grid (1-5 levels, Fibonacci)    │
│  ├─ Adaptive Grid (fill-rate + volatility driven)     │
│  ├─ Liquidity Hole Detection (L2 depth, 60-sample MA) │
│  ├─ Inventory Throttling + Anti-Cross Guard           │
│  ├─ Shadow Mode Engine (simulate without risk)        │
│  └─ Capital Guard + DLL Circuit Breaker               │
├───────────────────────────────────────────────────────┤
│  L1 KINETIC SHIELD (Python, 50ms cycle)               │
│  ├─ l1_shield.py  (OBI skewing, sweep, confidence)   │
│  ├─ Adaptive Learning (auto-calibrate threshold)      │
│  ├─ Iceberg & Flickering Detection                    │
│  ├─ Confidence Scoring (0.0-1.0 → mmap)              │
│  └─ L1→L2 JSON Bridge (/dev/shm/beroun/l1_state.json)│
├───────────────────────────────────────────────────────┤
│  L2 SOVEREIGN ORACLE (Gemini 3.1 Pro, 5min cycle)     │
│  ├─ beroun_ai_orchestrator.py (Cross-Layer brain)     │
│  ├─ Chain-of-Analysis (CoA) Gemini prompts            │
│  ├─ Market Regime Classification (Trend/Range/Chaos)  │
│  ├─ Shadow Mode Recovery Monitor                      │
│  └─ Premium Telegram Reports + Inline Buttons         │
├───────────────────────────────────────────────────────┤
│  IPC (mmap /dev/shm/beroun/)                          │
│  ├─ engine_state.bin (EngineState, lock-free atomic)  │
│  ├─ risk_state.bin   (RiskState, atomic params)       │
│  └─ l1_state.json    (L1→L2 bridge, JSON telemetry)  │
├───────────────────────────────────────────────────────┤
│  C2 LAYER (Python + Telegram v10.1)                   │
│  ├─ tg_listener.py  (Command & Control, /ai /shadow) │
│  ├─ analytics.py    (Trade Analytics Engine)          │
│  └─ oracle_brain.sh (Legacy cron-based Oracle)        │
├───────────────────────────────────────────────────────┤
│  DASHBOARD (http://localhost:3000)                     │
│  └─ dashboard.html  (Real-time metrics)               │
└───────────────────────────────────────────────────────┘
```

## Quick Start

```bash
# 1. Konfigurace
cp .env.example .env
# Vyplň: BITFINEX_API_KEY, BITFINEX_API_SECRET, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

# 2. Build
cargo build --release

# 3. Spuštění (L0 + L1 + L2 Oracle + Dashboard + Telegram + Watchdog)
sudo systemctl start beroun-sniper

# 4. Dashboard
open http://localhost:3000

# 5. Telegram — pošli /help tvému botovi
```

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/status` | Live equity, PnL, pozice |
| `/ai` | 🧠 AI Intelligence Status (L1 Shield + L2 Oracle telemetry) |
| `/shadow` | 🌑 Aktivovat Shadow Mode (simulace bez rizika) |
| `/golive` | 🚀 Návrat do Live režimu |
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
│   ├── main.rs            # L0 HFT engine (FastWebSockets, Hydra Grid)
│   ├── types.rs           # Shared types (EngineState + AI fields, RiskState)
│   ├── config_cli.rs      # beroun-config CLI (mmap parameter modifier)
│   ├── dashboard.rs       # HTTP dashboard server (:3000)
│   ├── dump_offsets.rs    # Debug: mmap offset validator
│   └── risk_control.rs   # Risk control binary
├── scripts/
│   ├── l1_shield.py               # L1 Kinetic Shield (adaptive, confidence)
│   ├── beroun_ai_orchestrator.py  # L2 Sovereign Oracle (Cross-Layer AI)
│   ├── tg_listener.py             # Telegram C2 interface (v10.1 Overlord)
│   ├── analytics.py               # Trade Analytics Engine
│   └── oracle_brain.sh            # Legacy L2 Oracle (cron-based)
├── docs/
│   ├── AI_INFRASTRUCTURE.md   # AI stack documentation
│   ├── HFT_AUDIT_2026.md     # Architecture audit & standards
│   ├── MONITORING.md          # Observability & metrics
│   ├── RESEARCH.md            # HFT research notes
│   └── STRATEGY.md            # Trading strategy documentation
├── beroun-start.sh        # Orchestrator (L0 + L1 + L2 + Dashboard + TG)
├── watchdog.sh            # mmap heartbeat monitor
├── optimize.sh            # PGO optimization script
├── dashboard.html         # Web dashboard UI
├── beroun-sniper.service  # systemd service unit
├── ARCHITECTURE.md        # Detailed architecture docs
├── ROADMAP.md             # Future development plan
├── CONTRIBUTING.md        # Development standards
└── .env                   # API keys (gitignored)
```

## Key Features (v10.1 Overlord)

### L0 — Rust HFT Engine (True Zero-Copy)
- **FastWebSockets**: Zero-copy WebSocket parsing via `fastwebsockets` 0.8 + `simd_json`
- **In-Place SIMD Parsing**: No `Vec<u8>` allocation per market frame
- **Hydra Multi-Level Grid**: 1-5 concurrent price levels with Fibonacci spacing
- **Adaptive Grid**: Fill-rate balance drives grid tightening/widening (0.7×-1.5×)
- **Liquidity Hole Detection**: L2 depth monitor with 60-sample MA, auto grid ×3 on holes
- **Shadow Mode**: Engine can simulate trades without sending real orders to Bitfinex

### L1 — Kinetic Shield (Tactical AI)
- **Adaptive Learning**: Auto-calibrates sweep detection threshold based on success/false positive rate
- **Confidence Scoring**: Writes `l1_confidence_score` (0-100%) to mmap for L2 consumption
- **OBI Micro-Skewing**: Reads real-time Order Book Imbalance, shifts grid bias
- **Iceberg Detection**: Identifies hidden institutional orders at repeated price levels
- **Flickering Detection**: Detects bid/ask manipulation (rapid oscillations without fills)
- **L1→L2 Bridge**: Exports telemetry to `/dev/shm/beroun/l1_state.json` every 60s

### L2 — Sovereign Oracle (Strategic AI)
- **Cross-Layer Intelligence**: Reads L1 state + Engine metrics + external data
- **Chain-of-Analysis (CoA)**: Structured Gemini prompt with 5-step analytical framework
- **Market Regime Classification**: Trending / Ranging / Chaos detection
- **Auto-Tune**: Grid, max position, and risk level adjustments every 5 minutes
- **Shadow Recovery**: Monitors shadow PnL and proposes going live when safe
- **Premium Reports**: Rich Telegram summaries with regime icons and L1 telemetry

### Operations
- **Shadow Mode**: `/shadow` — bot continues learning without risk, `/golive` to return
- **Emergency Close**: `/close CONFIRM` — REST API market close (works even if WS is down)
- **Cautious Mode**: `/cautious` — 15-min defensive mode before macro events
- **AI Status**: `/ai` — full L1/L2 telemetry view on Telegram
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
| 9 | Shadow Mode | `/shadow` via Telegram | Simulates without risk |
| 10 | L1 Adaptive Threshold | FP rate > 50% | Auto-desensitize sweep detection |

## Cross-Layer Intelligence (v10.1)

```
L1 (Kinetic Shield) ──→ l1_state.json ──→ L2 (Sovereign Oracle)
     ↓ mmap write                              ↓ Gemini CoA
  confidence, obi,                        regime, grid_step,
  sweep_threshold                         l1_advice, insight
     ↑ mmap read                              ↓ beroun-config
L0 (Rust Engine)  ←────── risk_state.bin ←──── L2 applies params
```

**Feedback Loop:**
1. L1 detects market anomalies (sweeps, icebergs, flickering) and writes confidence + signatures
2. L2 reads L1 telemetry, consults Gemini for strategic analysis
3. L2 writes parameters back to L0 via `beroun-config`
4. L2 can trigger L1 learning cycles (increase/decrease sensitivity)
5. If L1's false positive rate is too high, it auto-calibrates its threshold

## Version History

| Version | Codename | Key Features |
|---------|----------|-------------|
| v10.1 | Overlord | Cross-Layer AI, Shadow Mode, FastWebSockets Zero-Copy |
| v10.0 | Apex Predator | Analytics, Fee Optimizer, Multi-Pair Foundation |
| v9.5 | Sentinel | Adaptive Grid, Liquidity Hole, Cautious Mode |
| v9.2 | Accountant | Emergency Close, Total Equity, Daily Report |
| v9.0 | Silent Hydra | Multi-Level Grid, Capital Guard, DLL |
| v8.0 | Sovereign | Dual WS, AI Sidecar, Gemini Oracle |

## License

Private / All Rights Reserved
