# 🐺 BEROUN SNIPER v10.4 "Lesson Validator"
**Status:** Live Fire | **Intelligence:** Self-Learning + Self-Validating AI

[![Rust](https://img.shields.io/badge/Rust-2024_Edition-orange)]()
[![SQLite](https://img.shields.io/badge/Brain-SQLite_WAL-blue)]()
[![Gemini](https://img.shields.io/badge/Oracle-Gemini_3.1_Pro-green)]()

## What is this?

A sovereign HFT market-making bot for BTC/USD on Bitfinex. It places bid/ask orders on a dynamic grid, captures the spread, and uses a 3-layer AI architecture to autonomously optimize its own strategy.

**v10.4 "Lesson Validator"** adds the **closed feedback loop**: the AI not only learns from mistakes, but **validates its own lessons** against real performance data, automatically degrading rules that make things worse.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│              SNIPER v10.4 LESSON VALIDATOR           │
│                                                      │
│  L0: beroun-core         (Rust, µs HFT engine)      │
│  L1: l1_shield.py        (Python, 50ms OBI tactical) │
│  L2: sniper_orchestrator (Python, 5min Gemini Oracle)│
│  🧠: beroun-brain         (Rust, SQLite memory)      │
│  🌙: Neural Cross         (Nightly AI self-critique)  │
│  🔬: Lesson Validator     (Pre/Post PnL analysis)    │
│  UI: beroun-dashboard    (Rust, HTMX+SSE)            │
│  C2: tg_listener.py      (Telegram interface)        │
└─────────────────────────────────────────────────────┘
```

### The Closed Feedback Loop

```
DAY:   Oracle reads validated lessons → better decisions → logs to SQLite
                                                            ↓
NIGHT (03:00 CET):
  Step 0: VALIDATE existing lessons (pre vs post avg PnL)
    → CONFIRMED → confidence +0.1
    → DEGRADED  → confidence -0.15
    → < 15%     → auto-deactivated (lesson killed)
                        ↓
  Step 1: Statistical backtest (winners vs losers)
  Step 2: Worst-cycles export
  Step 3: Gemini Self-Critique → generate NEW lessons
                        ↓
NEXT DAY: Oracle reads VALIDATED lessons → even better decisions
  ↻ autonomous forever
```

## Project Structure

```
hft-sniper/
├── src/
│   ├── main.rs              # L0 HFT Engine (Hydra Grid, Zero-Copy WS)
│   ├── types.rs              # EngineState mmap struct (Atomic IPC)
│   ├── brain.rs              # Sniper Brain v10.4 (SQLite + Validator)
│   ├── dashboard.rs          # Dashboard (SSE + HTMX, zero JS)
│   └── config_cli.rs         # Parameter overrider (beroun-config)
├── scripts/
│   ├── l1_shield.py          # L1 Kinetic Shield (OBI, sweep detection)
│   ├── sniper_orchestrator.py # L2 Neural Cross Oracle + Validator
│   └── tg_listener.py        # Telegram Command Center (20 commands)
├── logs/
│   └── sniper.db             # Permanent SQLite Memory
├── beroun-start.sh           # Master Orchestrator
├── watchdog.sh               # Process guardian
├── Cargo.toml                # v10.4.0
└── .env                      # API keys (not in git)
```

## Rust Binaries

| Binary | Purpose |
|--------|---------|
| `beroun-core` | HFT engine (µs trades, Hydra Grid) |
| `beroun-dashboard` | HTMX+SSE dashboard (zero JavaScript) |
| `beroun-config` | CLI parameter overrider |
| `beroun-brain` | SQLite memory + Neural Cross + Lesson Validator |

## Sniper Brain CLI

```bash
# Memory
beroun-brain init                      # Create/migrate DB (6 tables)
beroun-brain log-cycle <json>          # Store Oracle cycle
beroun-brain log-alert <json>          # Store tactical alert
beroun-brain query [--last 6h]         # Query history
beroun-brain analyze                   # Self-analysis per regime
beroun-brain pattern TRENDING          # Learned optimal params
beroun-brain stats                     # Overall statistics
beroun-brain context [--cycles 6]      # Generate Gemini prompt

# Neural Cross (v10.3+)
beroun-brain backtest [--days 1]       # Statistical winners-vs-losers
beroun-brain worst-cycles [--n 5]      # Export worst cycles for AI critique
beroun-brain lessons [--regime R]      # Query active learned lessons
beroun-brain save-lesson <json>        # Store lesson from AI Coach

# Lesson Validator (v10.4)
beroun-brain validate-lessons [--hours 24]  # Validate lessons: pre vs post PnL
beroun-brain alpha-report [--hours 24]      # AI efficiency audit (saved/missed)
```

## SQLite Schema (6 tables)

| Table | Purpose |
|-------|---------|
| `cycles` | Every 5min Oracle cycle snapshot |
| `alerts` | Tactical alerts (inventory drift, latency spike) |
| `patterns` | Learned optimal params per regime |
| `experiments` | What-if counterfactual analysis |
| `lessons` | AI-generated corrective rules with confidence |
| `lesson_validations` | Validation results (pre/post PnL comparison) |

### Confidence System

| Confidence | Icon | Behavior |
|------------|------|----------|
| ≥ 70% | 🔴 | **Mandatory** — Oracle must follow |
| 30-70% | 🟡 | **Suggested** — Oracle considers |
| < 30% | ⚪ | Ignored (will decay) |
| < 15% | ❌ | **Auto-deactivated** by Validator |

Confidence is **dynamic**:
- CONFIRMED validation: +0.10
- DEGRADED validation: -0.15 (bad lessons punished harder)
- Auto-decay: -5% per 3 days without validation

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/status` | Live state (equity, PnL, position) |
| `/ai` | AI status (L1 Shield + L2 Oracle) |
| `/brain` | Sniper Brain (memory + lessons + Alpha Audit) |
| `/backtest` | Neural Cross (stats → worst → Coach → lessons) |
| `/validate` | **Lesson Validation + Alpha Report** |
| `/oracle` | Force Sniper AI cycle |
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

## Alpha Report

The Lesson Validator compares **pre-lesson avg PnL** vs **post-lesson avg PnL** per regime:

```
📉 AI ALPHA REPORT (24h)

PnL total: $-60.14
Cyklu: 27 (W:0 L:12)
Aktivni lekce: 4

AI Saved: +$0.00
AI Missed: -$14.17
Net Alpha: $-14.17
Verdict: OVER_CAUTIOUS
```

When Net Alpha is negative, the system knows its lessons are **too paranoid** and automatically degrades them.

## Version History

| Version | Codename | Key Feature |
|---------|----------|-------------|
| v10.4 | **Lesson Validator** | Closed-loop: validate → adjust → repeat |
| v10.3 | Neural Cross | Self-learning backtest + AI Coach |
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
