# AI Infrastructure — Beroun Sniper v7.0

## Přehled

Lokální AI node poskytuje botovi „strategický mozek" — 100% offline, GPU-akcelerovaný,
s OpenAI-kompatibilním API na `localhost:1234`.

```
beroun-core (CPU 1)              LM Studio (GPU: GTX 1060)
    │                                 │
    ├─► engine_state.bin ─────►  beroun-sovereign-ai (CPU 2)
    │   (mmap: OBI, micro)            │
    │                                 ├─► POST /v1/chat/completions
    │   risk_state.bin  ◄─────────────┤   (localhost:1234)
    │   (bias_offset)                 │
    └── grid posun                    └── Phi-3.5-mini (3.8B, Q4_K_S)
```

## Hardware

| Komponenta | Specifikace |
|-----------|-------------|
| GPU | NVIDIA GeForce GTX 1060 6GB (Pascal) |
| VRAM | 6144 MiB (model: ~3.7 GB, KV cache: ~2.3 GB) |
| Driver | 580.126.09 |
| Runtime | llmster 0.0.7-4 (LM Studio v0.4.7) |

## Nasazený model

| Parametr | Hodnota |
|----------|---------|
| Model | Phi-3.5 Mini Instruct |
| Parametry | 3.8B |
| Kvantizace | Q4_K_S (2.19 GB) |
| Architektura | Phi-3 |
| Kontext | 4096 tokenů |
| GPU offloading | `--gpu max` (všechny vrstvy na GPU) |

### Výběr modelu podle VRAM

| VRAM | Doporučený model | Kvantizace | Velikost |
|------|-------------------|------------|----------|
| 6 GB | Phi-3.5-mini-instruct | Q4_K_S | 2.19 GB |
| 8 GB | Llama 3.1 8B | Q4_K_M | ~4.7 GB |
| 12 GB | Mistral 7B | Q6_K | ~5.5 GB |
| 16+ GB | Gemma 2 9B / Command-R | Q8_0 | ~9 GB |

## LM Studio v0.4.7 — klíčové funkce

| Funkce | Popis | Přínos pro Snipera |
|--------|-------|-------------------|
| **Continuous Batching** | Paralelní inference (`--parallel N`) | AI Manager + manuální analýza současně |
| **LM Link** | Tailscale E2E šifrované připojení | Vzdálený přístup k AI bez VPN |
| **Anthropic API** | `/v1/messages` kompatibilita | Claude-style agenti lokálně |
| **Headless daemon** | `llmster` bez GUI | Čistý server deployment |

## Služby (systemd)

### lmstudio.service (system-level)
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
nvidia-persistenced → lmstudio.service → beroun-sniper → beroun-dashboard
                      (GPU + AI model)    (HFT engine)    (Web UI)
```

## API Endpoint

```
POST http://localhost:1234/v1/chat/completions
Content-Type: application/json
```

### Příklad volání (Rust)
```rust
let res = client.post("http://localhost:1234/v1/chat/completions")
    .json(&json!({
        "model": "phi-3.5-mini-instruct",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 10
    }))
    .send().await?;
```

### Příklad volání (curl)
```bash
curl -s http://localhost:1234/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"phi-3.5-mini-instruct","messages":[{"role":"user","content":"OBI +0.45. Bias?"}],"temperature":0,"max_tokens":10}'
```

## CLI příkazy

```bash
lms status                    # Stav serveru a modelů
lms ps                        # Načtené modely v paměti
lms ls                        # Dostupné modely na disku
lms load <model> --gpu max    # Načíst model na GPU
lms unload <model>            # Uvolnit VRAM
lms server start --port 1234  # Spustit API server
lms server stop               # Zastavit API server
nvidia-smi                    # Kontrola VRAM využití
```

## Bezpečnost

- ✅ **100% offline** — žádná data neopouštějí server
- ✅ **Žádné API klíče** — lokální inference
- ✅ **GPU izolace** — AI na GPU, Sniper na CPU Core 1
- ✅ **LM Link (volitelně)** — E2E šifrovaný vzdálený přístup
