---
description: How to add a new trading bot to the Sniper Armada platform
---

# 🤖 Nasazení nového bota

Hexagonální architektura umožňuje přidat nového bota **bez změny frameworku ani exchange kódu**.

## Prerekvizity

- Fungující VenueAdapter (BitfinexVenue, BinanceVenue, ...)
- GID rozsah (přidělit v `shared/src/types.rs`)
- mmap state file (engine + risk)

## Krok za krokem

### 1. Vytvořit nový crate

```bash
# V kořenu workspace:
mkdir -p newbot/src
```

### 2. Cargo.toml

```toml
# newbot/Cargo.toml
[package]
name = "newbot"
version = "20.1.0"
edition = "2024"

[[bin]]
name = "newbot-core"
path = "src/main.rs"

[dependencies]
sniper-shared = { path = "../shared", features = ["runtime"] }
sniper_types = { path = "../shared" }
anyhow = "1.0"
tracing = "0.1"
tracing-subscriber = "0.3"
dotenvy = "0.15"
tokio = { version = "1.50", features = ["full"] }
ryu = "1.0"
bytes = "1.10"
serde_json = "1.0"
```

### 3. Registrovat v workspace Cargo.toml

```toml
# Cargo.toml (root)
[workspace]
members = [
    "shared",
    "hydra",
    "grid",
    "moonshot",
    "trigon",
    "nexus",
    "newbot",    # ← NOVÉ
]
```

### 4. Přidělit GID

```rust
// shared/src/types.rs
pub const BOT_GID_NEWBOT: u32 = 6000;  // Range: 6000–6999
```

### 5. Implementovat SovereignEngine

```rust
// newbot/src/main.rs
use anyhow::Result;
use sniper_types::*;
use sniper_types::exchange::bitfinex_venue::BitfinexVenue;
use sniper_shared::framework::{SovereignEngine, SovereignRunner};
use std::sync::atomic::Ordering;
use tracing::info;

struct NewBotEngine {
    // Vaše strategie fields
}

impl SovereignEngine for NewBotEngine {
    fn subscriptions(&mut self) -> Vec<String> {
        // Které kanály chcete odebírat po auth
        vec![
            sniper_types::exchange::bitfinex_subscribe_ticker("tBTCUSD"),
        ]
    }

    fn on_start(&mut self) -> Result<()> {
        info!("NewBot starting...");
        // Init mmap, load state, etc.
        Ok(())
    }

    fn on_auth(&mut self) {
        info!("NewBot authenticated");
    }

    fn on_market_message(&mut self, payload: &mut [u8], out_buf: &mut bytes::BytesMut) {
        // Zde je vaše HFT strategie.
        // `payload` = raw bytes z WebSocket
        // `out_buf` = píšete sem objednávky pro burzu

        // Příklad: parse ticker
        if let Some((_, bid, ask)) = sniper_types::exchange::fast_parse_ticker(payload) {
            let spread = ask - bid;
            // ... vaše logika ...

            // Příklad: poslat limit order přes BitfinexVenue builder
            if spread > 100_000_000 {  // > $1 spread
                let mut ryu1 = ryu::Buffer::new();
                let mut ryu2 = ryu::Buffer::new();
                let qty = 0.001;
                let price = bid as f64 / PRICE_SCALE;

                BitfinexVenue::write_batch_open_cancel_gid(out_buf, BOT_GID_NEWBOT);
                BitfinexVenue::write_limit_order(
                    out_buf, BOT_GID_NEWBOT, b"tBTCUSD",
                    ryu1.format(qty), ryu2.format(price),
                );
                BitfinexVenue::write_batch_close(out_buf);
            }
        }
    }

    fn on_system_event(&mut self, _value: &serde_json::Value, _out_buf: &mut bytes::BytesMut) {}

    fn on_loop(&mut self, _out_buf: &mut bytes::BytesMut) {
        // Autonomní scan (voláno každou ms)
    }

    fn on_shutdown(&mut self, out_buf: &mut bytes::BytesMut) {
        // Cancel all orders
        BitfinexVenue::write_cancel_gid_standalone(out_buf, BOT_GID_NEWBOT);
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    dotenvy::dotenv().ok();
    tracing_subscriber::fmt::init();

    let engine = NewBotEngine { /* ... */ };
    let venue = BitfinexVenue::new();
    let mut runner = SovereignRunner::new(engine, venue, "NewBot");
    runner.run().await
}
```

### 6. Pro cross-exchange bota (Nexus-style)

```rust
use sniper_shared::framework::{CrossVenueEngine, SovereignCrossVenueRunner};
use sniper_types::exchange::bitfinex_venue::BitfinexVenue;
use sniper_types::exchange::binance_venue::BinanceVenue;

impl CrossVenueEngine for ArbEngine {
    fn secondary_subscriptions(&mut self) -> Vec<String> {
        vec![sniper_types::exchange::binance::Binance::subscribe_book_ticker(&["btcusdt"], 1)]
    }

    fn on_secondary_message(&mut self, payload: &mut [u8], out_buf: &mut bytes::BytesMut) {
        // Binance price update → compare with Bitfinex → arb signal
    }
}

// main():
let mut runner = SovereignCrossVenueRunner::new(
    engine,
    BitfinexVenue::new(),   // primary (execution)
    BinanceVenue::new(),    // secondary (data feed)
    "ArbBot"
);
runner.run().await
```

### 7. BotManifest (pro AI discovery)

```rust
// shared/src/bot_manifest.rs — přidat:
pub const NEWBOT_MANIFEST: BotManifest = BotManifest {
    name: "NewBot",
    version: "20.1",
    strategy: StrategyType::MarketMaking,
    gid: crate::BOT_GID_NEWBOT,
    supported_venues: &[ExchangeId::Bitfinex],
    runner_type: RunnerType::SingleWs,
    params: &[
        BotParam { name: "spread_threshold", description: "Min spread to trade",
            param_type: ParamType::Float { min: 0.01, max: 100.0 },
            default: "1.0", unit: "USD" },
    ],
    engine_path: "/dev/shm/beroun/newbot_engine.bin",
    risk_path: "/dev/shm/beroun/newbot_risk.bin",
    dashboard_port: None,
};
```

### 8. Deploy script

Přidat do `deploy_armada.sh`:
```bash
nohup ./target/release/newbot-core >> /home/wwwenda/beroun-brain/short_term/newbot.log 2>&1 &
```

### 9. Build + test + restart

```bash
cargo build --release
sudo systemctl restart beroun-sniper.service
ps aux | grep newbot-core
```

## Runner typy

| Runner | Použití | Příklad |
|--------|---------|---------|
| `SovereignRunner<E, V>` | 1 WS stream | Grid, Moonshot, Trigon |
| `SovereignDualRunner<E, V>` | 2 WS na stejnou burzu (MDATA+EXEC) | Hydra |
| `SovereignCrossVenueRunner<E, V1, V2>` | 2 WS na různé burzy | Nexus |

## Checklist

- [ ] Cargo.toml + workspace registrace
- [ ] GID přiděleno (nový `BOT_GID_*` v types.rs)
- [ ] `SovereignEngine` implementován (7 metod)
- [ ] BotManifest přidán do `bot_manifest.rs`
- [ ] Deploy script aktualizován
- [ ] `cargo test` projde
- [ ] `cargo build --release` projde
- [ ] Proces běží po `systemctl restart`
