# 🐺 Sniper Armada v21.1

**Sovereign HFT Trading System** — Pure Rust Hive with in-process AI inference.

[![Version](https://img.shields.io/badge/Version-v21.1-gold)]()
[![Architecture](https://img.shields.io/badge/Architecture-3_Layer_Sovereign-blue)]()
[![Rust](https://img.shields.io/badge/L0+L1-Rust_2024-orange)]()
[![AI](https://img.shields.io/badge/L2-Gemini_3.1_Pro-green)]()
[![Latency](https://img.shields.io/badge/L1_AI-\<20ms-red)]()

---

## ⚡ Key Features

- **Sub-millisecond execution** — Zero-allocation Rust hot paths with fixed-point arithmetic (`i64 × 1e8`)
- **5-bot fleet** — Hydra (MM), Moonshot (flash dip), Grid (grid maker), Trigon (tri-arb), Nexus (cross-venue arb)
- **Dual AI inference** — In-process Candle Logit Sniping (<20ms) + Gemini 3.1 Pro strategic oracle via ZeroClaw
- **Hexagonal Architecture** — `VenueAdapter` trait enables zero-effort exchange integration (Bitfinex, Binance, ...)
- **Cognitive Engineering** — Few-Shot anchored L1 prompt + Sovereign Constitution L2 with Chain-of-Thought forcing
- **Brain Transplant Protocol** — Safe hot-swap GGUF models with shadow mode validation, zero downtime
- **Autonomous operation** — Sovereign Boot Protocol (v20.0 Strategy Pattern) with 5 RiskClass Pillars (HEDGE, ARBITRAGE, MARKET_MAKER, STAT_ARB, POSITIONAL), quota watchdog, Sentinel guardian
- **Lock-free IPC** — mmap with SeqLock atomics, 64-byte cache-line aligned structs via `/dev/shm/beroun/`

## 🏗️ Architecture

```
┌─────────────────────┐     ┌─────────────────────────┐     ┌──────────────────────────┐
│   L0: MUSCLE         │     │   L1: REFLEXES           │     │   L2: GENERAL             │
│   (Rust Execution)   │     │   (Candle + Cortex)      │     │   (ZeroClaw + Gemini)     │
│                     │     │                         │     │                          │
│  Hydra  ──┐         │     │  Candle L1 Brain        │     │  Sovereign Constitution  │
│  Grid   ──┤ mmap ◀──┼─────┤  Logit Sniping (<20ms)  │     │  Gemini 3.1 Pro Preview  │
│  Moonshot─┤         │     │  4-token: BID/ASK/      │     │  <thinking> CoT forcing  │
│  Trigon ──┤         │     │    HOLD/KILL             │     │  RAG memory (SQLite)     │
│  Nexus  ──┘         │     │  CPU mode (GTX 1060)    │     │  Telegram alerts         │
│                     │     │                         │     │  Quota Watchdog (5min)   │
│  VenueAdapter trait  │     │                         │     │                          │
│  Bitfinex + Binance  │     │                         │     │  Hard Guardrails:        │
│                     │     │  ChatTemplate Resolver   │     │  FAIL-FAST / MAX EXPO /  │
│                     │     │  (Phi3/Llama3/Qwen2/    │     │  ANTI-JITTER             │
│                     │     │   Gemma2/Mistral)       │     │                          │
└─────────────────────┘     └─────────────────────────┘     └──────────────────────────┘
        <1ms                        <20ms                          ~5min cycle
```

## 🚀 Quick Start

```bash
# Build all bots + AI
cargo build --release

# Deploy fleet
./deploy_armada.sh --build

# Start ZeroClaw L2 Oracle
export GEMINI_API_KEY=your_key
zeroclaw daemon

# Hot restart via systemd
sudo systemctl restart sniper-armada

# Status
zeroclaw status
```

## 📦 Repository Structure

```
sniper/
├── hydra/              # L0 Market Maker (Rust — Bitfinex WS)
├── moonshot/           # L0 Flash Crash Dip Buyer (Rust — multi-pair)
├── grid/               # L0 Dynamic Grid Maker (Rust — L2 warped)
├── trigon/             # L0 Triangular Arbitrage (Rust — 14 triangles)
├── nexus/              # L0 Cross-Exchange Arb (Rust — Binance↔Bitfinex)
├── shared/             # sniper-shared crate (types, IPC, framework, venues)
│   └── src/exchange/   #   VenueAdapter trait + BitfinexVenue + BinanceVenue
├── candle-brain/       # 🧠 In-process L1 AI inference (Candle + GGUF)
│   ├── src/brain.rs    #   CandleL1Brain — GGUF model loading + inference
│   ├── src/logit_sniper.rs  # Logit Sniping — 4-token classifier
│   ├── src/chat_template.rs # Model-agnostic template resolver
│   └── src/transplant.rs    # SBP v3.1 Brain Transplant Protocol
├── architect/          # L1 Cortex (Rust) + L2 Oracle + Commander
│   ├── cortex/         #   Sovereign Cortex daemon (Sentinel, GPU, UDS)
│   ├── l2_oracle.py    #   Legacy L2 brain (replaced by ZeroClaw)
│   └── tg_commander.py #   Telegram interface + bot management
├── zeroclaw/           # 🐝 ZeroClaw L2 Orchestrator
│   ├── config.toml     #   Gemini 3.1 Pro, SQLite RAG, Telegram
│   ├── system_prompt.md#   Sovereign Constitution (CRO persona)
│   └── tools/          #   mmap tools (read_engine_state, write_risk_param, quota_watchdog)
├── infra/              # systemd service, install/stop scripts
├── state/              # Persistent pre-crash state (armada_state.json)
├── deploy_armada.sh    # Sovereign Boot Script
├── watchdog.sh         # Cron-based process monitor
├── ARCHITECTURE.md     # Full system documentation
└── README.md           # This file
```

## 🧠 AI Layers

### L1 — Logit Sniping (Candle, In-Process)

```
ROLE: Ultra-low latency HFT Reflex Core.
TASK: Classify L2 microstructure to predict next 100ms price tick.
OUTPUT: EXACTLY ONE TOKEN FROM [BID, ASK, HOLD, KILL].
```

- **No text generation** — reads raw logits for 4 tokens in <20ms
- **Model-agnostic** — ChatTemplate auto-detects Phi-3.5, Llama-3, Qwen2, Gemma2, Mistral
- **Few-Shot anchored** — 3 in-context examples force deterministic classification
- **KILL action** — immediate order cancellation on toxic sweep detection

### L2 — Sovereign Constitution (ZeroClaw + Gemini 3.1 Pro)

- **Chain-of-Thought forcing** — `<thinking>` block required before any Tool Call
- **Hard Guardrails** — FAIL-FAST, MAX EXPOSURE (0.05 BTC), ANTI-JITTER
- **RAG Memory** — SQLite vector DB recalls past decisions and their outcomes
- **Brain Transplant** — safe hot-swap of GGUF models with shadow validation

## 🛡️ Safety Guarantees

| Layer | Mechanism | Protection |
|-------|-----------|------------|
| L0 | SeqLock + Atomics | Data consistency without locks |
| L1 | OBI + Sweep + KILL | Toxic flow protection (<20ms) |
| L1 | Sentinel (4-layer) | PnL crash, position drift, heartbeat |
| L1 | Brain Transplant | Safe model swap with rollback |
| L2 | Hard Guardrails | FAIL-FAST, MAX EXPO, ANTI-JITTER |
| L2 | Quota Watchdog | Telegram alert on API quota exhaustion |
| L2 | Sovereign Boot Protocol | Paper→Live validation (66min) |
| OS | systemd `Restart=always` | Auto-restart on failure, OOM protection |
| OS | watchdog.sh (cron) | Last-resort process resurrection |

## 🔧 Production Setup

```bash
# Prerequisites
sudo apt install -y nvidia-cuda-toolkit   # For future GPU acceleration

# Install systemd service + kernel tuning
sudo ./infra/install_service.sh

# Configure API keys
cp .env.example .env
# Edit: BITFINEX_API_KEY, BINANCE_API_KEY, TELEGRAM_BOT_TOKEN, GEMINI_API_KEY

# ZeroClaw onboarding
zeroclaw onboard --quick --provider gemini --model gemini-2.5-pro

# Verify
sudo systemctl status sniper-armada
zeroclaw status
```

## 📚 Guides

- [Add a New Bot](/.agents/workflows/add-bot.md) — Step-by-step bot creation with SovereignEngine
- [Add a New Exchange](/.agents/workflows/add-exchange.md) — VenueAdapter implementation guide

## 📄 License

Private. All rights reserved.
