# 🐺 Beroun Sniper v5.2

Vysokofrekvenční (HFT) obchodní bot pro Bitfinex BTC/USD.  
Rust 2024 • Zero-copy • Sub-ms latence • CPU-pinned • mmap IPC

## Quick Start

```bash
# 1. Konfigurace
cp .env.example .env
# Vyplň BITFINEX_API_KEY a BITFINEX_API_SECRET

# 2. Build
cargo build --release

# 3. Spuštění jako systemd služba
systemctl --user enable --now beroun-sniper

# 4. Sledování logů
journalctl --user -u beroun-sniper -f
```

## Klíčové vlastnosti

- **simd_json** — SIMD-akcelerovaný JSON parser
- **mmap IPC** — sdílená paměť s atomickými operacemi
- **TCP_NODELAY** — nulové zpoždění na síťové vrstvě
- **CPU pinned** — single-thread Tokio runtime na dedikovaném jádru
- **Anti-spam** — ochrana proti Bitfinex rate limitům
- **Cache-line isolation** — prevence false sharing v mmap

## Dokumentace

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — Kompletní architektura, memory layout, optimalizace
- **[.env](.env)** — API klíče (BITFINEX_API_KEY, BITFINEX_API_SECRET)

## Příkazy

| Příkaz | Popis |
|--------|-------|
| `systemctl --user status beroun-sniper` | Status bota |
| `systemctl --user restart beroun-sniper` | Restart |
| `journalctl --user -u beroun-sniper -f` | Live logy |

## Build

```bash
cargo build --release    # Optimalizovaný build
```

Binárky v `target/release/beroun-core`.

## Licence

Proprietární — VaclavSercl
