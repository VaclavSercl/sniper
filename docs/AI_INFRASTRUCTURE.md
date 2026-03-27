# AI Infrastructure — Beroun Sniper v10.0

## Přehled

Třívrstvý AI systém: lokální L1 Shield (Python/mmap), lokální LM Studio (GPU), vzdálený Gemini Oracle.

```
                    Beroun Sniper AI Stack
┌─────────────────────────────────────────────────────┐
│                                                     │
│  L1 TACTICAL SHIELD (l1_shield.py)                  │
│  ├─ Python 3, 1s cycle, mmap IPC                   │
│  ├─ Reads: engine_state.bin (OBI, mid, bids/asks)  │
│  ├─ Writes: risk_state.bin (bias_offset)            │
│  └─ Functions: OBI skewing, micro-skew, sweep det. │
│                                                     │
│  LOCAL AI NODE (LM Studio, optional)                │
│  ├─ GTX 1060 6GB, Phi-3.5-mini, localhost:1234     │
│  └─ Greedy sampling, heartbeat-fused                │
│                                                     │
│  L2 STRATEGIC ORACLE (oracle_brain.sh)              │
│  ├─ Gemini 3.1 Pro, 26h macro cycle                │
│  └─ RSS + Fear/Greed → beroun-config adjustments   │
│                                                     │
└─────────────────────────────────────────────────────┘
```

## L1 Tactical Shield (l1_shield.py)

### Funkce
| Funkce | Popis | Input → Output |
|--------|-------|----------------|
| **OBI Skewing** | Čte order book imbalance, posouvá bias | engine_state OBI → bias_offset |
| **Micro-Skew** | Tlumí bidy při sell pressure | OBI < -0.3 → záporný skew |
| **Sweep Detection** | Detekce toxických large-order sweepů | Volume spike → Toxic flag |

### Live Metriky
```
OBI=-0.651  Skew=$-0.59  Mid=$68,470  Toxic=0  Cycle=1200
```

### Bezpečnost
- Pokud mmap read selže → bias = 0 (safe default, pure grid)
- Pokud engine heartbeat > 30s stale → bias zeroed
- Všechny bias hodnoty clampovány na ±$5

## Lokální AI Node (LM Studio)

### Stack
| Komponenta | Hodnota |
|-----------|--------|
| Runtime | LM Studio v0.4.7 (llmster headless) |
| Model | Phi-3.5-mini-instruct (3.8B, Q4_K_S) |
| GPU | GTX 1060 6GB (VRAM: ~3.7 GB model + ~2.3 GB KV cache) |
| API | `localhost:1234` (OpenAI-compatible) |
| Sampling | Greedy: temp=0, top_p=0.1, max_tokens=5 |
| Systemd | `lmstudio.service` (Restart=always) |

### Výběr modelu podle VRAM

| VRAM | Doporučený model | Kvantizace | Velikost |
|------|-------------------|------------|----------|
| 6 GB | Phi-3.5-mini-instruct | Q4_K_S | 2.19 GB |
| 8 GB | Llama 3.1 8B | Q4_K_M | ~4.7 GB |
| 12 GB | Mistral 7B | Q6_K | ~5.5 GB |
| 16+ GB | Gemma 2 9B / Command-R | Q8_0 | ~9 GB |

### AI Safety Systems
| System | Trigger | Akce |
|--------|---------|------|
| **Heartbeat Fuse** | `ai_heartbeat_ms` > 30s stale | Bias zeroed, pure grid |
| **Thermal Guard** | GPU ≥ 82°C | Cycle 5s → 10s |
| **Sanity Clamp** | beroun-config writes | Grid $1-$200, ±50%/update |
| **Alpha Tracking** | Every trade execution | Measures AI $ contribution |

## L2 Strategic Oracle (oracle_brain.sh)

### Pipeline
```
main.rs (hourly) → runtime/state.json
                        ↓
oracle_brain.sh  → beroun-config export-json + RSS + Fear/Greed
                        ↓
                   gemini-cli → {new_grid, max_position, risk_level, reasoning}
                        ↓
                   beroun-config set-grid + set-max-inv (with 3× safety)
                        ↓
                   risk_state.bin (mmap) → Sniper reads immediately
```

## Služby (systemd)

### lmstudio.service
```ini
[Unit]
Description=LM Studio Headless Daemon (llmster v0.4.7)
After=network.target nvidia-persistenced.service

[Service]
Type=simple
User=wwwenda
ExecStartPre=lms daemon up
ExecStartPre=lms load phi-3.5-mini-instruct --gpu max -y
ExecStart=lms server start --port 1234
Restart=always
RestartSec=5
```

### Pořadí startu služeb
```
nvidia-persistenced → lmstudio.service → beroun-sniper
                      (GPU + AI model)    (HFT engine + L1 Shield + dashboard)
```

## CLI příkazy

```bash
# LM Studio
lms status                    # Stav serveru a modelů
lms ps                        # Načtené modely v paměti
lms load <model> --gpu max    # Načíst model na GPU
nvidia-smi                    # Kontrola VRAM využití

# L1 Shield
python3 scripts/l1_shield.py  # Spustí L1 Shield
ps aux | grep l1_shield       # Zkontroluje běh

# Oracle
./scripts/oracle_brain.sh     # Vynutí Oracle cyklus

# mmap debug
cargo run --release --bin dump-offsets  # Ověří struct offsets
```

## Bezpečnost

- ✅ **L1 Shield 100% offline** — mmap IPC only, žádné síťové volání
- ✅ **LM Studio 100% offline** — žádná data neopouštějí server
- ✅ **GPU izolace** — AI na GPU, Sniper na CPU Core 1
- ✅ **Triple Safety** — Heartbeat fuse + bias clamping + beroun-config validation
- ✅ **Graceful degradation** — pokud L1 selže, L0 běží s bias=0
