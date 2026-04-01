# 🐺 Sniper Armada v19.0

**Sovereign HFT Trading System** — Institutional-grade algorithmic trading fleet on Bitfinex.

[![Architecture](https://img.shields.io/badge/Architecture-3_Layer-blue)]()
[![Rust](https://img.shields.io/badge/L0_Engine-Rust_2024-orange)]()
[![Python](https://img.shields.io/badge/L2_Oracle-Python_3.12-green)]()
[![Latency](https://img.shields.io/badge/Latency-<1ms-red)]()

---

## ⚡ Key Features

- **Sub-millisecond execution** — Zero-allocation Rust hot paths with fixed-point arithmetic (`i64 × 1e8`)
- **5-bot fleet** — Hydra (MM), Moonshot (flash dip), Grid (grid maker), Trigon (tri-arb), Nexus (cross-exchange)
- **AI-driven strategy** — Gemini 2.5 Pro strategic oracle + local Phi-3.5 tactical shield on GTX 1060
- **Autonomous operation** — Sovereign Boot Protocol with paper→live validation, 4-layer Sentinel guardian
- **Lock-free IPC** — mmap with SeqLock atomics, 64-byte cache-line aligned structs via `/dev/shm/beroun/`

## 🏗️ Architecture

```
L0 Execution (Rust)  ──mmap──▶  L1 Tactical (Rust/GPU)  ──UDS──▶  L2 Strategic (Python/Gemini)
 <1ms hot path                   50ms cycle                        5min cycle
 Zero alloc, lock-free           Phi-3.5 inference                 Macro analysis + tuning
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for full system documentation including memory topology, CPU pinning, and IPC protocols.

## 🚀 Quick Start

```bash
# Build + Deploy (compiles Rust, generates Python offsets, boots fleet)
./deploy_armada.sh --build

# Boot only (use cached binaries)
./deploy_armada.sh

# Hot restart via systemd
sudo systemctl restart sniper-armada

# Stop
sudo systemctl stop sniper-armada
```

## 📦 Repository Structure

```
sniper/
├── hydra/              # L0 Market Maker (Rust — Bitfinex WS)
├── moonshot/           # L0 Flash Crash Dip Buyer (Rust — multi-pair)
├── grid/               # L0 Dynamic Grid Maker (Rust — L2 warped)
├── trigon/             # L0 Triangular Arbitrage (Rust — 14 triangles)
├── nexus/              # L0 Cross-Exchange Arb (Rust — Binance↔Bitfinex)
├── shared/             # sniper-shared crate (types, IPC, framework)
├── architect/          # L1 Cortex (Rust) + L2 Oracle + Commander (Python)
│   ├── cortex/         #   Sovereign Cortex daemon (Sentinel, GPU, UDS)
│   ├── l2_oracle.py    #   Gemini strategic brain
│   ├── tg_commander.py #   Telegram interface + bot management
│   └── dashboard_server.py  # Master Dashboard SSE API
├── infra/              # systemd service, install/stop scripts
├── state/              # Persistent pre-crash state (armada_state.json)
├── deploy_armada.sh    # Sovereign Boot Script
├── watchdog.sh         # Cron-based process monitor (last line of defense)
├── ARCHITECTURE.md     # Full system documentation
└── README.md           # This file
```

## 🛡️ Safety Guarantees

| Layer | Mechanism | Protection |
|-------|-----------|------------|
| L0 | SeqLock + Atomics | Data consistency without locks |
| L1 | OBI + Sweep detection | Toxic flow protection (50ms) |
| L1 | Sentinel (4-layer) | PnL crash, position drift, heartbeat |
| L2 | Sovereign Boot Protocol | Paper→Live validation (66min) |
| OS | systemd `Type=oneshot` | Auto-restart on failure, OOM protection |
| OS | watchdog.sh (cron) | Last-resort process resurrection |

## 🔧 Production Setup

```bash
# Install systemd service + kernel tuning
sudo ./infra/install_service.sh

# Verify
sudo systemctl status sniper-armada
journalctl -u sniper-armada -f
```

## 📄 License

Private. All rights reserved.
