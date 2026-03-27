# 🐺 BEROUN SNIPER v10.3 "Neural Cross"
**Status:** Live Fire | **Intelligence:** Self-Learning AI with Permanent Memory

[![Rust](https://img.shields.io/badge/Rust-2024_Edition-orange)]()
[![SQLite](https://img.shields.io/badge/Brain-SQLite_WAL-blue)]()
[![Gemini](https://img.shields.io/badge/Oracle-Gemini_3.1_Pro-green)]()

## What is this?

A sovereign HFT market-making bot for BTC/USD on Bitfinex. It places bid/ask orders on a dynamic grid, captures the spread, and uses a 3-layer AI architecture to autonomously optimize its own strategy.

**v10.3 "Neural Cross"** adds **nightly self-learning**: the AI reviews its own past mistakes, generates corrective lessons, and applies them automatically — no human intervention needed.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│              SNIPER v10.3 NEURAL CROSS               │
│                                                      │
│  L0: beroun-core         (Rust, µs HFT engine)      │
│  L1: l1_shield.py        (Python, 50ms OBI tactical) │
│  L2: sniper_orchestrator (Python, 5min Gemini Oracle)│
│  🧠: beroun-brain         (Rust, SQLite memory)      │
│  🌙: Neural Cross         (Nightly AI self-critique)  │
│  UI: beroun-dashboard    (Rust, HTMX+SSE)            │
│  C2: tg_listener.py      (Telegram interface)        │
└─────────────────────────────────────────────────────┘
```

### The Neural Cross Loop

```
DAY:   Oracle reads lessons → makes better decisions → logs to SQLite
NIGHT (03:00 CET):
  1. beroun-brain backtest  → statistical analysis (winners vs losers)
  2. beroun-brain worst-cycles → export worst decisions
  3. Gemini Self-Critique   → AI reviews its OWN past reasoning
  4. beroun-brain save-lesson → stores corrective rules
NEXT DAY: Oracle reads NEW lessons → even better decisions
  ↻ autonomous forever
```

## Project Structure

```
hft-sniper/
├── src/
│   ├── main.rs              # L0 HFT Engine (Hydra Grid, Zero-Copy WS)
│   ├── types.rs              # EngineState mmap struct (Atomic IPC)
│   ├── brain.rs              # Sniper Brain v10.3 (SQLite + Neural Cross)
│   ├── dashboard.rs          # Dashboard (SSE + HTMX, zero JS)
│   └── config_cli.rs         # Parameter overrider (beroun-config)
├── scripts/
│   ├── l1_shield.py          # L1 Kinetic Shield (OBI, sweep detection)
│   ├── sniper_orchestrator.py # L2 Neural Cross Oracle (Gemini + Brain + Lessons)
│   └── tg_listener.py        # Telegram Command Center (18 commands)
├── logs/
│   └── sniper.db             # Permanent SQLite Memory
├── beroun-start.sh           # Master Orchestrator
├── watchdog.sh               # Process guardian
├── Cargo.toml                # v10.3.0
└── .env                      # API keys (not in git)
```

## Rust Binaries

| Binary | Purpose |
|--------|---------|
| `beroun-core` | HFT engine (µs trades, Hydra Grid) |
| `beroun-dashboard` | HTMX+SSE dashboard (zero JavaScript) |
| `beroun-config` | CLI parameter overrider |
| `beroun-brain` | SQLite permanent memory + Neural Cross engine |

## Sniper Brain CLI

```bash
# Memory
beroun-brain init                  # Create/migrate DB (5 tables)
beroun-brain log-cycle <json>      # Store Oracle cycle
beroun-brain log-alert <json>      # Store tactical alert
beroun-brain query [--last 6h]     # Query history
beroun-brain analyze               # Self-analysis per regime
beroun-brain pattern TRENDING      # Learned optimal params
beroun-brain stats                 # Overall statistics
beroun-brain context [--cycles 6]  # Generate Gemini prompt

# Neural Cross (v10.3)
beroun-brain backtest [--days 1]   # Statistical winners-vs-losers analysis
beroun-brain worst-cycles [--n 5]  # Export worst cycles for AI critique
beroun-brain lessons [--regime R]  # Query active learned lessons
beroun-brain save-lesson <json>    # Store lesson from AI Coach
```

## SQLite Schema (5 tables)

| Table | Purpose |
|-------|---------|
| `cycles` | Every 5min Oracle cycle snapshot (regime, grid, PnL, reasoning) |
| `alerts` | Tactical alerts (inventory drift, latency spike, etc.) |
| `patterns` | Learned optimal params per regime |
| `experiments` | What-if counterfactual analysis |
| `lessons` | AI-generated corrective rules with confidence scoring |

### Lessons Confidence System

| Confidence | Icon | Behavior |
|------------|------|----------|
| ≥ 70% | 🔴 | **Mandatory** — Oracle must follow |
| 30-70% | 🟡 | **Suggested** — Oracle considers |
| < 30% | ⚪ | Ignored (will decay and deactivate) |

Lessons **auto-decay** (-5% per 3 days without validation) and are **deactivated** when confidence drops below 15%.

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/status` | Live state (equity, PnL, position) |
| `/ai` | AI status (L1 Shield + L2 Oracle) |
| `/brain` | Sniper Brain (memory stats + active lessons) |
| `/backtest` | **Neural Cross** (stats → worst cycles → Gemini Coach → lessons) |
| `/oracle` | Force Sniper AI cycle (Gemini analysis) |
| `/analyze` | Gemini analysis with permanent memory |
| `/analytics` | Trade analytics + Brain regime analysis |
| `/shadow` | Activate Shadow Mode (simulation) |
| `/golive` | Return to Live Fire |
| `/grid N` | Set grid step ($) |
| `/capital N` | Set authorized capital ($) |
| `/loss N` | Set daily loss limit ($) |
| `/pause` / `/resume` | Pause/resume trading |
| `/cautious` | Macro-event defense (15 min) |
| `/close CONFIRM` | EMERGENCY market exit |
| `/report` | Daily report |
| `/help` | Command overview |

## Quick Start

```bash
# Build all binaries
cargo build --release

# Initialize brain (creates 5 SQLite tables)
./target/release/beroun-brain init

# Start everything
sudo systemctl start beroun-sniper

# Manual backtest
./target/release/beroun-brain backtest --dry-run
```

## Version History

| Version | Codename | Key Feature |
|---------|----------|-------------|
| v10.3 | **Neural Cross** | Self-learning backtest + AI Coach |
| v10.2 | Sniper Brain | SQLite permanent memory |
| v10.1 | Apex Predator | Cross-layer AI orchestration |
| v10.0 | Sovereign | Gemini Oracle + L1 Shield |
| v9.x | Hydra | Grid trading + mmap IPC |

## Standards
- **Rust 2024 Edition**, Zero-copy, Atomic Fixed-Point (PRICE_SCALE=1e8)
- **PGO profiling**, `opt-level=3`, `lto=fat`, `panic=abort`
- **SQLite WAL mode** for concurrent reads during trading
- **Gemini 3.1 Pro** for strategic analysis and self-critique

## License
Proprietary — Sovereign HFT Systems © 2026
