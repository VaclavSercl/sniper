# 🐺 Beroun Sniper v5.0 (Sovereign HFT)

[![Language](https://img.shields.io/badge/Language-Rust_2024-orange.svg)](https://www.rust-lang.org/)
[![Performance](https://img.shields.io/badge/Latency-Sub--Microsecond-green.svg)]()
[![License](https://img.shields.io/badge/License-Proprietary-red.svg)]()

Ultra-nízko latenční obchodní engine pro Bitfinex, optimalizovaný pomocí **PGO (Profile-Guided Optimization)** a postavený na **Zero-copy mmap** architektuře pro rok 2026.

## 🏗 Architektura systému

```mermaid
graph TD
    subgraph "External Exchange"
        BF[Bitfinex WebSocket]
    end

    subgraph "High Priority Hot Path"
        Core[beroun-core Sniper]
        Mmap[(Shared Mmap State)]
    end

    subgraph "Passive Intelligence"
        AI[beroun-ai Manager]
        SovAI[beroun-sovereign-ai]
        Dash[Real-time Dashboard]
    end

    BF -- TCP/TLS --> Core
    Core -- Atomic Store --> Mmap
    Mmap -- Atomic Load --> AI
    Mmap -- Atomic Load --> SovAI
    AI -- WebSocket --> Dash
    SovAI -- Bias Control --> Mmap
    Core -- Telegram API --> TG[Telegram Alert]
```

## 🚀 Klíčové vlastnosti
- **Zero-copy parsing:** Minimální režie při zpracování tickerů.
- **Atomic Fixed-Point:** Eliminace jitterů FPU jednotky použitím celočíselné aritmetiky.
- **Sovereign Risk Management:** AI upravuje grid bias v reálném čase podle čisté pozice.
- **Watchdog Guardian:** Automatické sestřelení a restart při detekci stale dat v paměti.

## 🛠 Instalace (Agentic Deployment)

Systém využívá **Zero-Touch AI instalaci**. Místo psaní manuálních příkazů nebo řešení chybějících závislostí v Linuxu, pouze probudíte umělou inteligenci na prázdném serveru a ona systém kompletně sestaví a optimalizuje na míru vašemu hardware (vč. PGO kompilace).

**Stačí spustit tento JEDINÝ PŘÍKAZ na čistém Ubuntu/Debian serveru:**
```bash
curl -fsSL https://raw.githubusercontent.com/VaclavSercl/HFT-Sniper/main/bootstrap.sh | bash
```

**Co tento příkaz udělá:**
1. Nainstaluje základní mozek (`gemini-cli`).
2. AI prozkoumá stroj a připraví ho (vytvoří SWAP, apod.).
3. **GPU Detekce:** Pokud najde **NVIDIA GPU s min. 6GB VRAM**, automaticky stáhne systém **Ollama** s bleskovým lokálním modelem pro HFT filtrování.
4. AI nainstaluje Rust, naklonuje tento repozitář, vyřeší Linux závislosti a zkompiluje hotové binárky.

### Konfigurace
Zkopírujte `.env.example` do `.env` a vyplňte své API klíče. Pro registraci démona do Linuxu proveďte zkopírování služby podle instrukcí na konci instalace.

## 📚 Dokumentace
- [Architektonické detaily](docs/ARCHITECTURE.md)
- [Obchodní strategie](docs/STRATEGY.md)
- [Pravidla pro AI agenty](GEMINI.md)

---
*Proprietární software pro autonomní obchodování.*
