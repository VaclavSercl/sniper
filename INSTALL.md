# 🐺 SNIPER ARMADA v15.1 — Installation Guide

## Requirements

### Hardware (minimum)
- CPU: 4+ cores (Intel i5-6400 or better)
- RAM: 8+ GB
- GPU: NVIDIA GTX 1060+ (optional, for AI inference)
- Disk: 20+ GB SSD
- OS: Ubuntu 22.04+ / Debian 12+

### Software
```bash
# Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
rustup default stable

# System deps
sudo apt install python3 python3-pip build-essential pkg-config libssl-dev

# Python deps
pip install --user pyTelegramBotAPI websocket-client requests
```

## Installation

### 1. Clone
```bash
git clone git@github.com:VaclavSercl/sniper.git ~/sniper
cd ~/sniper
```

### 2. Configure
```bash
cp .env.example .env && nano .env
```

Required variables:
```
BITFINEX_API_KEY=...
BITFINEX_API_SECRET=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

### 3. Build
```bash
cargo build --release --workspace
```

### 4. IPC
```bash
mkdir -p /dev/shm/beroun
```

### 5. Deploy
```bash
./deploy_armada.sh
```

This starts:
- 🐍 Hydra L0 (Core 0, :3000)
- 🏛️ Architect Dashboard (:3004)
- 📱 Telegram Commander
- 💰 PnL Daemon

Other bots are started on-demand via Telegram:
```
/moonshot start
/grid start
/trigon start
```

### 6. Systemd (auto-start on boot)
```bash
sudo cp sniper-armada.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable sniper-armada
sudo systemctl start sniper-armada
```

## Verification
```bash
systemctl is-active sniper-armada
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000  # 200 (Hydra)
curl -s -o /dev/null -w "%{http_code}" http://localhost:3004  # 200 (Architect)
journalctl -u sniper-armada --since "1 min ago" | grep -c panic  # 0
```

## Ports

| Port | Service | Description |
|------|---------|-------------|
| 3000 | Hydra Dashboard | BTC-USD Market Making |
| 3004 | Architect Dashboard | Master Control Panel |

## mmap Paths

| Path | Bot | Purpose |
|------|-----|---------|
| `/dev/shm/beroun/engine_state.bin` | Hydra | Engine IPC |
| `/dev/shm/beroun/risk_state.bin` | Hydra | Risk IPC |
| `/dev/shm/beroun/moonshot_engine.bin` | Moonshot | Engine IPC |
| `/dev/shm/beroun/moonshot_risk.bin` | Moonshot | Risk IPC |
| `/dev/shm/beroun/grid_engine.bin` | Grid | Engine IPC |
| `/dev/shm/beroun/grid_risk.bin` | Grid | Risk IPC |
| `/dev/shm/beroun/trigon_engine.bin` | Trigon | Engine IPC |
| `/dev/shm/beroun/trigon_risk.bin` | Trigon | Risk IPC |
| `/dev/shm/beroun/pnl_state.bin` | PnL Daemon | FIFO PnL data |
| `/dev/shm/beroun/mdf.bin` | MDF | Market data feed |

## SQLite Databases

| Path | Service |
|------|---------|
| `~/.local/share/sniper/sniper.db` | Hydra Brain |
| `~/.local/share/sniper/moonshot.db` | Moonshot Brain |
| `~/.local/share/sniper/grid.db` | Grid Brain |
| `~/.local/share/sniper/pnl.db` | PnL Engine (FIFO fills) |

## CPU Allocation

| Core | Process | Priority |
|------|---------|----------|
| 0 | Hydra L0 | SCHED_FIFO 90 |
| 1 | Moonshot L0 | SCHED_FIFO 70 |
| 2 | Grid L0 | normal |
| 3 | Trigon L0 + OS + Python AI + Dashboards | normal |
