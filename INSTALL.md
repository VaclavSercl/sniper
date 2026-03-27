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
git clone git@github.com:VaclavSercl/sniper.git hft-sniper
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

Výstup: `target/release/hydra-core`, `hydra-dashboard`, `hydra-config`, `hydra-brain`

### 4. IPC (Shared Memory)
```bash
mkdir -p /dev/shm/beroun
```

### 5. Manuální spuštění
```bash
# Spustí Hydru (Bot #1) s CPU pinning na Core 0
./hydra/hydra-start.sh
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

# Dashboard
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000
# Očekáváno: 200

# Telegram
# Pošli /status do bota → měl by odpovědět

# Logy (žádné paniky)
journalctl -u sniper-armada --since "1 min ago" | grep -c panic
# Očekáváno: 0
```

## Struktura souborů

```
hft-sniper/
├── Cargo.toml               # Workspace root
├── shared/                   # Sdílené typy a utility
│   └── src/
│       ├── types.rs          # EngineState, RiskState (mmap)
│       ├── math.rs           # Fixed-point konverze
│       └── logging.rs        # Tracing setup
├── hydra/                    # Bot #1: BTC-USD Delta Lead
│   ├── src/
│   │   ├── main.rs           # L0 HFT Engine
│   │   ├── dashboard.rs      # HTMX Dashboard (:3000)
│   │   ├── brain.rs          # SQLite Brain
│   │   └── config_cli.rs     # Live config (mmap)
│   ├── scripts/
│   │   ├── l1_shield.py      # L1 Tactical AI
│   │   ├── sniper_orchestrator.py # L2 Oracle
│   │   ├── tg_listener.py    # Telegram C2
│   │   └── macro_monitor.py  # Binance + F&G + RSS
│   ├── dashboard.html        # Dashboard template
│   └── hydra-start.sh        # Bot startup (Core 0)
├── deploy_armada.sh          # Master launcher
├── sniper-armada.service     # systemd unit
├── .env                      # API klíče (NEKOPÍROVAT do gitu!)
└── watchdog.sh               # Heartbeat monitor
```

## Porty

| Port | Služba |
|------|--------|
| 3000 | Hydra Dashboard |
| 3001 | Trigon Dashboard (budoucí) |

## Důležité cesty

| Cesta | Účel |
|-------|------|
| `/dev/shm/beroun/engine_state.bin` | Hydra mmap IPC |
| `/dev/shm/beroun/risk_state.bin` | Risk management mmap |
| `~/.local/share/sniper/sniper.db` | SQLite Brain |
