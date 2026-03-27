# 🐺 BEROUN SNIPER v10.2 "SNIPER BRAIN"
**Status:** Live Fire | **Intelligence:** Permanent SQLite Memory (Rust)

## Architecture

```
┌─────────────────────────────────────────────────┐
│              SNIPER v10.2 BRAIN                  │
│                                                  │
│  L0: beroun-core         (Rust, µs HFT engine)  │
│  L1: l1_shield.py        (Python, 50ms OBI)     │
│  L2: sniper_orchestrator (Python, 5min Gemini)   │
│  🧠: beroun-brain         (Rust, SQLite memory)  │
│  UI: beroun-dashboard    (Rust, HTMX+SSE)       │
│  C2: tg_listener.py      (Telegram interface)   │
└─────────────────────────────────────────────────┘
```

### Key Upgrades (v10.2):
- **Sniper Brain:** SQLite persistence eliminates memory loss after restart
- **Self-Improving Loop:** Oracle compares past profits per regime with current settings
- **Zero-JS Dashboard:** SSE + HTMX rendered server-side in Rust
- **Permanent Memory:** Every 5min cycle stored in `sniper.db`
- **Pattern Learning:** Optimal grid/position per regime (TRENDING/RANGING/CHAOS)

## Project Structure

```
hft-sniper/
├── src/
│   ├── main.rs          # L0 HFT Engine (Hydra Grid, Zero-Copy WS)
│   ├── types.rs         # EngineState mmap struct (Atomic IPC)
│   ├── brain.rs         # Sniper Brain (SQLite CLI)
│   ├── dashboard.rs     # Dashboard (SSE + HTMX, zero JS)
│   └── config_cli.rs    # Parameter overrider (beroun-config)
├── scripts/
│   ├── l1_shield.py           # L1 Kinetic Shield (OBI, sweep detection)
│   ├── sniper_orchestrator.py # L2 Sovereign Oracle (Gemini + Brain)
│   └── tg_listener.py        # Telegram Command Center
├── logs/
│   └── sniper.db        # Permanent SQLite Memory
├── beroun-start.sh      # Master Orchestrator
├── watchdog.sh          # Process guardian
├── Cargo.toml           # rusqlite + fastwebsockets + axum
└── .env                 # API keys (not in git)
```

## Rust Binaries

| Binary | Purpose |
|--------|---------|
| `beroun-core` | HFT engine (µs trades, Hydra Grid) |
| `beroun-dashboard` | HTMX+SSE dashboard (zero JavaScript) |
| `beroun-config` | CLI parameter overrider |
| `beroun-brain` | SQLite permanent memory CLI |

## Sniper Brain CLI

```bash
beroun-brain init              # Create/migrate DB
beroun-brain log-cycle <json>  # Store Oracle cycle
beroun-brain log-alert <json>  # Store tactical alert
beroun-brain query [--last 6h] # Query history
beroun-brain analyze           # Self-analysis per regime
beroun-brain pattern TRENDING  # Learned optimal params
beroun-brain stats             # Overall statistics
beroun-brain context           # Generate Gemini prompt
```

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/status` | Live state (equity, PnL, position) |
| `/ai` | AI status (L1 Shield + L2 Oracle) |
| `/brain` | Sniper Brain (permanent memory stats) |
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

## Quick Start

```bash
# Build
cargo build --release

# Initialize brain
./target/release/beroun-brain init

# Start everything
sudo systemctl start beroun-sniper
```

## Standards
- **Rust 2024 Edition**, Zero-copy, Atomic Fixed-Point (PRICE_SCALE=1e8)
- **PGO profiling**, `opt-level=3`, `lto=fat`, `panic=abort`
- **SQLite WAL mode** for concurrent reads during trading
