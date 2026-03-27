# 🐺 BEROUN SNIPER v10.7 — Sovereign AI HFT Engine

> **Autonomous High-Frequency Trading system for BTC/USD on Bitfinex.**
> Three-layer architecture: L0 Rust Engine → L1 Python Shield → L2 Gemini Oracle.

## Architecture

```
┌─────────────────────── L2: Gemini Oracle (Strategic) ───────────────────────┐
│  sniper_orchestrator.py · 5-min cycle · Regime detection · Grid & position  │
│  decisions · SQLite permanent memory · Neural Cross self-learning           │
└─────────────┬──────────────── beroun-config → mmap ──────────┬──────────────┘
              │                                                │
┌─────────────▼────── L1: Python Shield (Tactical, 50ms) ──────▼──────────────┐
│  l1_shield.py · Sweep detection · OBI monitoring · Adaptive threshold       │
│  Ghost Mode activation · Anti-paralysis · Inventory skew → mmap            │
└─────────────┬──────────────── mmap IPC ──────────────────────┬──────────────┘
              │                                                │
┌─────────────▼────── L0: Rust Engine (Execution, <1ms) ───────▼──────────────┐
│  main.rs · WebSocket orderbook · Micro-price · Hydra multi-level grid       │
│  Ghost proximity monitor · Flash IOC injection · Atomic mmap state          │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Features

### v10.7 Sovereign AI (Current)
- **AI Control Registry**: `freeze_ms`, `fire_interval`, `ghost_trigger_zone`, `intent` — all AI-tunable via mmap
- **Intent System**: `/aggressive`, `/defensive`, `/scout`, `/sovereign` — strategic directives via Telegram
- **Dynamic Parameters**: No hardcoded constants in hot path — everything reads from AI registry

### v10.6 Ghost Shadow
- **Shadow Grid**: Hidden liquidity levels stored in RAM, invisible to orderbook
- **Flash IOC Injection**: Sub-millisecond order injection when micro-price approaches ghost level
- **Anti-Toxic Shield**: Velocity-based rejection prevents injection during sweeps
- **Transparency Control**: L1 Shield auto-activates ghost mode when `toxic > 300` or `OBI < -0.85`

### v10.5 Resurrection
- **Anti-Paralysis**: Breaks "death by safety" spiral — uptime tracking + threshold desensitization
- **L2 Grid Override**: Oracle decisions override vol-engine

### Core (v1.0–v10.4)
- **Zero-copy IPC**: Lock-free atomic mmap on `/dev/shm/beroun/`
- **Micro-price**: Volume-weighted BBA midpoint for sub-tick precision
- **Hydra Grid**: Fibonacci-spaced multi-level market making with dynamic bias
- **SIMD JSON**: `simd-json` + `fastwebsockets` for minimal parsing latency
- **SQLite Brain**: Permanent memory — cycles, lessons, experiments, alpha audit
- **Neural Cross**: Nightly AI self-critique with lesson generation and validation

## Project Structure

```
hft-sniper/
├── src/
│   ├── main.rs           # L0: HFT execution engine (1330 lines)
│   ├── types.rs           # Shared mmap schema (AtomicU64/I64, cache-aligned)
│   ├── dashboard.rs       # HTMX+SSE real-time dashboard on :3000
│   ├── brain.rs           # SQLite permanent memory + Neural Cross
│   └── config_cli.rs      # beroun-config CLI for mmap writes
├── scripts/
│   ├── l1_shield.py       # L1: Tactical shield (sweep detection, ghost control)
│   ├── sniper_orchestrator.py  # L2: Gemini Oracle + regime detection
│   └── tg_listener.py     # Telegram command center (v10.7 Sovereign)
├── beroun-start.sh        # Master startup script
├── beroun-sniper.service  # systemd unit file
├── watchdog.sh            # mmap heartbeat monitor
├── dashboard.html         # HTMX template for dashboard
├── Cargo.toml             # Rust dependencies (edition 2024)
└── .env                   # API keys (not tracked)
```

## Quick Start

```bash
# 1. Build
cargo build --release

# 2. Configure
cp .env.example .env  # Add Bitfinex + Telegram keys

# 3. Install systemd service
sudo cp beroun-sniper.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable beroun-sniper
sudo systemctl start beroun-sniper

# 4. Monitor
journalctl -u beroun-sniper -f
open http://localhost:3000  # Dashboard
```

## Telegram Commands

| Command | Effect |
|---|---|
| `/aggressive` | Tight grid, fast fire, no ghost |
| `/defensive` | Wide grid, slow fire, ghost ON |
| `/scout` | Shadow mode + data collection |
| `/sovereign` | AI full autonomy (default) |
| `/status` | Live PnL, position, equity |
| `/ai` | L1/L2 + Ghost + Sovereign status |
| `/brain` | SQLite memory + lessons |
| `/oracle` | Force Gemini cycle |
| `/close CONFIRM` | Emergency market close |

## Safety

- **DLL Circuit Breaker**: Daily loss limit auto-pauses trading (non-overridable)
- **Capital Guard**: Maximum authorized capital enforced at L0
- **Anti-Cross Guard**: Prevents self-crossing orders
- **Fire Interval Clamp**: 500ms–10000ms (L0 enforced)
- **Freeze Clamp**: 500ms–30000ms (L1 enforced)

## Tech Stack

| Component | Technology |
|---|---|
| Engine | Rust 2024, tokio, fastwebsockets |
| IPC | mmap `/dev/shm/`, lock-free atomics |
| JSON | simd-json (SIMD-accelerated) |
| AI | Google Gemini 2.5 Pro via CLI |
| Database | SQLite (rusqlite, bundled) |
| Dashboard | HTMX + SSE (zero JavaScript) |
| Release | Fat LTO, PGO-ready, panic=abort |

## License

Proprietary. © 2024–2026 Beroun Systems.
