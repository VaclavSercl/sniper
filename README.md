# 🐺 Beroun Sniper v6.2

Vysokofrekvenční (HFT) market-making bot pro Bitfinex BTC/USD.
Rust 2024 • Zero-copy • Sub-ms latence • CPU-pinned • mmap IPC

## Quick Start

```bash
# 1. Konfigurace
cp .env.example .env
# Vyplň: BITFINEX_API_KEY, BITFINEX_API_SECRET
# Volitelně: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

# 2. Build
cargo build --release

# 3. Spuštění jako systemd služby
systemctl --user enable --now beroun-sniper
systemctl --user enable --now beroun-dashboard

# 4. Dashboard
# Web:  http://localhost:3000
# TUI:  cargo run --release --bin beroun-monitor
```

## Architektura

```
beroun-core         ──► engine_state.bin (mmap) ◄── beroun-dashboard ──► :3000
  │ HFT Loop               │                          │ WebSocket push
  │ Order Tracking          │                          └── dashboard.html
  │ Trade Aggregation       │
  └── Bitfinex WS    ──► risk_state.bin (mmap) ◄── beroun-monitor (TUI)
```

## Klíčové vlastnosti

| Vrstva | Technologie | Detail |
|--------|-------------|--------|
| Parser | `simd_json` | SIMD-akcelerovaný, ~10× vs serde_json |
| IPC | `memmap2` | Atomické ops, cache-line aligned |
| Network | TCP_NODELAY | Dual WebSocket (data + exec) |
| Runtime | Tokio current_thread | CPU pinned na core 1 |
| Intelligence | Micro-Price, OBI, Inv Skew | Volume-weighted pricing + L2 imbalance |
| Sizing | Dynamic | Signal convergence → 0.7×–1.5× base |
| Orders | Targeted cancel | Chirurgický oc_multi s přesnými ID |
| Alerting | Telegram | BotEvent: okamžité alerty + hodinové reporty |

## Binárky

| Binárka | Účel |
|---------|------|
| `beroun-core` | HFT engine (main loop, orders, WS) |
| `beroun-dashboard` | Web dashboard server (:3000) |
| `beroun-monitor` | TUI dashboard (ANSI, mmap reader) |
| `risk-control` | Risk parameter management |
| `beroun-sovereign-ai` | AI risk module (experimental) |

## Příkazy

```bash
systemctl --user status beroun-sniper       # Status HFT engine
systemctl --user status beroun-dashboard    # Status dashboard
systemctl --user restart beroun-sniper      # Restart (graceful cancel)
journalctl --user -u beroun-sniper -f       # Live logy
cargo run --release --bin beroun-monitor    # TUI monitor
```

## Dokumentace

| Soubor | Obsah |
|--------|-------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Kompletní architektura, memory layout, intelligence |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Coding standards (Rust HFT) |
| [docs/STRATEGY.md](docs/STRATEGY.md) | Obchodní strategie (OFI, Grid) |
| [docs/HFT_AUDIT_2026.md](docs/HFT_AUDIT_2026.md) | Technické standardy a audit |
| [docs/MONITORING.md](docs/MONITORING.md) | Monitoring stack a metriky |
| [docs/RESEARCH.md](docs/RESEARCH.md) | Akademický výzkum HFT v Rustu |

## Roadmap (v7.0)

Viz [GitHub Issues](https://github.com/VaclavSercl/HFT-Sniper/issues) a [Project Board](https://github.com/users/VaclavSercl/projects/2).

- **#1** PnL Tracker (Realized vs. Unrealized)
- **#2** Machine Learning Risk (AI Manager)
- **#3** True Zero-Copy (FastWebSockets Migration)

## Licence

Proprietární — VaclavSercl
