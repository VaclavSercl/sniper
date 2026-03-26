# 🐺 Beroun Sniper v8.0

Vysokofrekvenční (HFT) market-making bot pro Bitfinex BTC/USD.
Rust 2024 • Zero-copy • Sub-ms latence • CPU-pinned • mmap IPC • AI Sovereign Intelligence

## Quick Start

```bash
# 1. Konfigurace
cp .env.example .env
# Vyplň: BITFINEX_API_KEY, BITFINEX_API_SECRET, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

# 2. Build
cargo build --release

# 3. Spuštění (kompletní AI stack)
systemctl --user enable --now beroun-sniper       # L0: HFT Engine
systemctl --user enable --now beroun-dashboard     # Web Dashboard
sudo systemctl enable --now beroun-ai             # L1: GPU AI Manager
sudo systemctl enable --now beroun-telegram       # Telegram Interface
sudo systemctl enable --now beroun-oracle.timer   # L2: Gemini Oracle (26h)

# 4. Dashboard
# Web:  http://localhost:3000
# TUI:  cargo run --release --bin beroun-monitor

# 5. Telegram
# Pošli /help tvému Beroun_Oracle_Bot na Telegramu
```

## Architektura v8.0 — Třívrstvý AI Imunitní Systém

```
┌──────────────────────────────────────────────────────────────────┐
│                    L2: STRATEGIE (26h + jitter)                  │
│  oracle_brain.sh → gemini-cli → beroun-config → mmap            │
│  RSS + Fear/Greed + PnL → Gemini 3.1 Pro → grid/inv update      │
├──────────────────────────────────────────────────────────────────┤
│                    L1: TAKTIKA (5s / 10s thermal)                │
│  beroun-sovereign-ai → LM Studio (localhost:1234) → mmap        │
│  OBI + Spread + Position → Phi-3.5 (GPU) → bias offset         │
│  Greedy sampling: temp=0, top_p=0.1, max_tokens=5              │
├──────────────────────────────────────────────────────────────────┤
│                    L0: REFLEX (< 50 µs)                          │
│  beroun-core → simd_json → Order Book → Sniper Logic → Bitfinex│
│  Micro-Price + OBI + Inv Skew + Dynamic Grid + AI Safety Fuse   │
└──────────────────────────────────────────────────────────────────┘

┌─ IPC (mmap, lock-free) ─────────────────────────────────────────┐
│  engine_state.bin ◄─► beroun-core, monitor, dashboard, ai       │
│  risk_state.bin   ◄─► beroun-config, sovereign-ai, oracle       │
└─────────────────────────────────────────────────────────────────┘
```

## Klíčové vlastnosti

| Vrstva | Technologie | Detail |
|--------|-------------|--------|
| Parser | `simd_json` | SIMD-akcelerovaný, ~10× vs serde_json |
| IPC | `memmap2` | Atomické ops, cache-line aligned |
| Network | TCP_NODELAY | Dual WebSocket (data + exec) |
| Runtime | Tokio current_thread | CPU pinned na core 1 |
| Intelligence | Micro-Price, OBI, Inv Skew | Volume-weighted pricing + L2 imbalance |
| AI L1 | LM Studio v0.4.7 + GTX 1060 | Phi-3.5-mini, greedy sampling, 5s cycle |
| AI L2 | Gemini 3.1 Pro (gemini-cli) | 26h Oracle cycle, RSS + sentiment |
| Safety | AI Heartbeat + Sanity Checks | 30s fuse, ±50% rate limiter, $1-$200 clamp |
| PnL | WAP + Alpha Tracking | Realized/Unrealized + AI execution alpha |
| Control | Telegram Bot | /status /analyze /grid /pause /oracle |
| Thermal | nvidia-smi + adaptive cycle | 82°C throttle, 75°C resume |

## Binárky

| Binárka | Účel |
|---------|------|
| `beroun-core` | HFT engine (main loop, orders, WS, PnL tracker) |
| `beroun-dashboard` | Web dashboard server (:3000) |
| `beroun-monitor` | TUI dashboard (ANSI, mmap reader, Alpha display) |
| `beroun-sovereign-ai` | AI L1 sidecar (LM Studio, GPU, thermal guard) |
| `beroun-config` | Live mmap parameter CLI (show/set-grid/set-bias/export-json) |
| `risk-control` | Legacy risk parameter management |

## Příkazy

```bash
# Služby
systemctl --user status beroun-sniper           # HFT engine
systemctl --user status beroun-dashboard        # Dashboard
sudo systemctl status beroun-ai                 # AI Manager (GPU)
sudo systemctl status beroun-telegram           # Telegram Interface
sudo systemctl list-timers beroun-oracle.timer  # Oracle timer

# Monitoring
cargo run --release --bin beroun-monitor    # TUI monitor
./target/release/beroun-config show        # Live risk params
./target/release/beroun-config export-json # Full JSON snapshot

# Telegram
/status    — Live stav bota
/analyze   — Gemini 3.1 Pro analýza
/grid 8.5  — Nastavit grid (se sanity checks)
/pause     — Nouzové zastavení
/oracle    — Force Oracle cyklus
```

## Dokumentace

| Soubor | Obsah |
|--------|-------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Kompletní architektura, memory layout, intelligence |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Coding standards (Rust HFT) |
| [docs/AI_INFRASTRUCTURE.md](docs/AI_INFRASTRUCTURE.md) | LM Studio v0.4.7, GPU, model, API |
| [docs/STRATEGY.md](docs/STRATEGY.md) | Obchodní strategie (OFI, Grid) |
| [docs/HFT_AUDIT_2026.md](docs/HFT_AUDIT_2026.md) | Technické standardy a audit |
| [docs/MONITORING.md](docs/MONITORING.md) | Monitoring stack a metriky |
| [docs/RESEARCH.md](docs/RESEARCH.md) | Akademický výzkum HFT v Rustu |

## Roadmap (v8.0+)

Viz [GitHub Issues](https://github.com/VaclavSercl/HFT-Sniper/issues).

### ✅ Completed
- **#1** PnL Tracker (Realized vs. Unrealized) — WAP, mmap
- **#2** Machine Learning Risk (AI Manager) — LM Studio, GPU sidecar
- **#7** Telegram Bi-directional Interface — 7 commands, Gemini Q&A
- **#8** AI Heartbeat + Alpha Tracking — 30s fuse, execution alpha

### 🔓 Open
- **#3** True Zero-Copy (FastWebSockets Migration)
- **#4** Global Oracle Fine-tuning (Gemini 3.1 Pro)
- **#5** L2 GPU Inference Optimization (<100ms)
- **#9** Thermal Guard + Network Jitter + Anti-Toxic Flow

## Licence

Proprietární — VaclavSercl
