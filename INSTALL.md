# 🐺 SNIPER ARMADA — Instalace na čistý server

## Požadavky

### Hardware (minimum)
- CPU: 4+ jádra (Intel i5-6400 nebo lepší)
- RAM: 8+ GB
- GPU: NVIDIA GTX 1060+ (pro AI inferenci, volitelné)
- Disk: 20+ GB SSD
- OS: Ubuntu 22.04+ / Debian 12+

### Software
```bash
# Rust toolchain
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
rustup default stable

# Python 3.12+
sudo apt install python3 python3-pip

# Python závislosti
pip install --user websocket-client requests google-generativeai python-telegram-bot

# Systémové
sudo apt install build-essential pkg-config libssl-dev
```

## Instalace

### 1. Klonování
```bash
cd /home/$USER
git clone git@github.com:VaclavSercl/HFT-Sniper.git hft-sniper
cd hft-sniper
```

### 2. Konfigurace
```bash
cp .env.example .env
nano .env
```

Vyplň:
```env
BITFINEX_KEY=tvůj_api_key
BITFINEX_SECRET=tvůj_api_secret
TELEGRAM_BOT_TOKEN=tvůj_bot_token
TELEGRAM_CHAT_ID=tvůj_chat_id
GEMINI_API_KEY=tvůj_gemini_key
```

### 3. Build
```bash
cargo build --release --workspace
```

Výstup:
```
target/release/hydra-core          # Hydra L0 engine
target/release/hydra-dashboard     # Hydra dashboard (:3000)
target/release/hydra-brain         # Hydra SQLite brain
target/release/hydra-config        # Hydra config CLI

target/release/moonshot-core       # Moonshot L0 engine
target/release/moonshot-dashboard  # Moonshot dashboard (:3001)
target/release/moonshot-brain      # Moonshot SQLite brain
target/release/moonshot-config     # Moonshot config CLI
```

### 4. IPC (Shared Memory)
```bash
mkdir -p /dev/shm/beroun
```

### 5. Manuální spuštění
```bash
# Spustí všechny boty najednou
./deploy_armada.sh

# Nebo jednotlivě:
./hydra/hydra-start.sh       # Hydra (Core 0)
./moonshot/moonshot-start.sh # Moonshot (Core 1)
```

### 6. Systemd (automatický start)
```bash
sudo cp sniper-armada.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sniper-armada
sudo systemctl start sniper-armada

# Kontrola
sudo systemctl status sniper-armada
journalctl -u sniper-armada -f
```

## Verifikace

```bash
# Je systém živý?
systemctl is-active sniper-armada

# Hydra Dashboard
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000
# Očekáváno: 200

# Moonshot Dashboard
curl -s -o /dev/null -w "%{http_code}" http://localhost:3001
# Očekáváno: 200

# Telegram
# Pošli /status do bota → Hydra odpovídá
# Pošli /moonshot → Moonshot odpovídá

# Logy (žádné paniky)
journalctl -u sniper-armada --since "1 min ago" | grep -c panic
# Očekáváno: 0

# Moonshot config
./target/release/moonshot-config export-json
```

## Struktura souborů

```
hft-sniper/
├── Cargo.toml                  # Workspace root
├── shared/                      # Sdílené typy a utility
│   └── src/
│       ├── types.rs             # Hydra EngineState, RiskState
│       ├── moonshot_types.rs    # Moonshot PairState[20]
│       ├── math.rs              # Fixed-point konverze
│       └── logging.rs           # Tracing setup
│
├── hydra/                       # Bot #1: BTC-USD Delta Lead
│   ├── src/
│   │   ├── main.rs              # L0 HFT Engine
│   │   ├── dashboard.rs         # Dashboard (:3000)
│   │   ├── brain.rs             # SQLite Brain
│   │   └── config_cli.rs        # Live config (mmap)
│   ├── scripts/
│   │   ├── l1_shield.py         # L1 Tactical AI
│   │   ├── sniper_orchestrator.py # L2 Oracle
│   │   ├── tg_listener.py       # Telegram C2
│   │   └── macro_monitor.py     # Binance + F&G + RSS
│   ├── dashboard.html
│   └── hydra-start.sh
│
├── moonshot/                    # Bot #2: Multi-Symbol Flash Crash
│   ├── src/
│   │   ├── main.rs              # L0 Engine (20 pairs)
│   │   ├── dashboard.rs         # Dashboard (:3001)
│   │   ├── brain.rs             # SQLite Brain
│   │   └── config_cli.rs        # Live config (mmap)
│   ├── scripts/
│   │   ├── l1_shield.py         # BTC volatility shield
│   │   ├── sniper_orchestrator.py # AI Pair Selection (L2)
│   │   ├── tg_listener.py       # Telegram C2
│   │   └── macro_monitor.py     # BTC reference feed
│   ├── AI_MOONSHOT_PROMPT.md    # Gemini system prompt
│   ├── dashboard.html
│   └── moonshot-start.sh
│
├── deploy_armada.sh             # Master launcher
├── sniper-armada.service        # systemd unit
├── .env                         # API klíče (NEKOPÍROVAT do gitu!)
└── watchdog.sh                  # Heartbeat monitor
```

## Porty

| Port | Služba |
|------|--------|
| 3000 | Hydra Dashboard |
| 3001 | Moonshot Dashboard |
| 3002 | Trigon Dashboard (budoucí) |
| 3003 | Architect Dashboard (budoucí) |

## Důležité cesty

| Cesta | Účel |
|-------|------|
| `/dev/shm/beroun/engine_state.bin` | Hydra mmap IPC |
| `/dev/shm/beroun/risk_state.bin` | Hydra risk mmap |
| `/dev/shm/beroun/moonshot_engine.bin` | Moonshot engine mmap |
| `/dev/shm/beroun/moonshot_risk.bin` | Moonshot risk mmap |
| `~/.local/share/sniper/sniper.db` | Hydra SQLite Brain |
| `~/.local/share/sniper/moonshot.db` | Moonshot SQLite Brain |

## CPU Allocation

| Core | Proces | Priority |
|------|--------|----------|
| Core 0 | Hydra (L0 engine) | SCHED_FIFO 90 |
| Core 1 | Moonshot (L0 engine) | SCHED_FIFO 70 |
| Core 2 | OS + Python AI | normal |
| Core 3 | Dashboards + Telegram | normal |
