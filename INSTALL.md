# 🐺 SNIPER ARMADA v11.2 — Instalace na čistý server

## Požadavky

### Hardware (minimum)
- CPU: 4+ jádra (Intel i5-6400 nebo lepší)
- RAM: 8+ GB
- GPU: NVIDIA GTX 1060+ (pro AI inferenci, volitelné)
- Disk: 20+ GB SSD
- OS: Ubuntu 22.04+ / Debian 12+

### Software
```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
rustup default stable
sudo apt install python3 python3-pip build-essential pkg-config libssl-dev
pip install --user websocket-client requests google-generativeai python-telegram-bot
```

## Instalace

### 1. Klonování
```bash
cd /home/$USER
git clone git@github.com:VaclavSercl/sniper.git sniper
cd sniper
```

### 2. Konfigurace
```bash
cp .env.example .env && nano .env
```

### 3. Build
```bash
cargo build --release --workspace
```

Výstup: 12 binárních souborů:
```
target/release/hydra-{core,dashboard,brain,config}
target/release/moonshot-{core,dashboard,brain,config}
target/release/grid-{core,dashboard,brain,config}
```

### 4. IPC
```bash
mkdir -p /dev/shm/beroun
```

### 5. Spuštění
```bash
./deploy_armada.sh           # Všechny 3 boty
./hydra/hydra-start.sh       # Hydra (Core 0)
./moonshot/moonshot-start.sh # Moonshot (Core 1)
./grid/grid-start.sh         # Grid (Core 2)
```

### 6. Systemd
```bash
sudo cp sniper-armada.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable sniper-armada
sudo systemctl start sniper-armada
```

## Verifikace
```bash
systemctl is-active sniper-armada
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000  # 200
curl -s -o /dev/null -w "%{http_code}" http://localhost:3001  # 200
curl -s -o /dev/null -w "%{http_code}" http://localhost:3002  # 200
journalctl -u sniper-armada --since "1 min ago" | grep -c panic  # 0
```

## Porty

| Port | Bot | Služba |
|------|-----|--------|
| 3000 | Hydra | BTC-USD Dashboard |
| 3001 | Moonshot | Multi-Symbol Dashboard |
| 3002 | Grid | Grid Levels Dashboard |

## mmap cesty

| Cesta | Bot | Účel |
|-------|-----|------|
| `/dev/shm/beroun/engine_state.bin` | Hydra | Engine IPC |
| `/dev/shm/beroun/risk_state.bin` | Hydra | Risk IPC |
| `/dev/shm/beroun/moonshot_engine.bin` | Moonshot | Engine IPC |
| `/dev/shm/beroun/moonshot_risk.bin` | Moonshot | Risk IPC |
| `/dev/shm/beroun/grid_engine.bin` | Grid | Engine IPC |
| `/dev/shm/beroun/grid_risk.bin` | Grid | Risk IPC |

## SQLite databáze

| Cesta | Bot |
|-------|-----|
| `~/.local/share/sniper/sniper.db` | Hydra |
| `~/.local/share/sniper/moonshot.db` | Moonshot |
| `~/.local/share/sniper/grid.db` | Grid |

## CPU Allocation

| Core | Proces | Priority |
|------|--------|----------|
| 0 | Hydra L0 | SCHED_FIFO 90 |
| 1 | Moonshot L0 | SCHED_FIFO 70 |
| 2 | Grid L0 | normal |
| 3 | OS + Python AI + Dashboards | normal |
