---
description: How to add a new exchange (VenueAdapter) to the Sniper Armada platform
---

# 🏦 Nasazení nové burzy (VenueAdapter)

Hexagonální architektura umožňuje přidat novou burzu **bez změny jakéhokoli bota ani frameworku**.

## Prerekvizity

- API klíče v `.env` → `{PREFIX}_API_KEY`, `{PREFIX}_API_SECRET`
- Dokumentace API burzy (WebSocket + REST)
- Znalost: tick size, lot size, fee struktura

## Krok za krokem

### 1. Vytvořit adapter soubor

```bash
touch shared/src/exchange/{nazev}_venue.rs
```

### 2. Implementovat VenueAdapter trait

Minimální implementace (~250 řádků):

```rust
// shared/src/exchange/kraken_venue.rs
use super::types::*;
use super::venue::*;

pub struct KrakenVenue {
    symbols: arrayvec::ArrayVec<(u64, arrayvec::ArrayString<16>), 32>,
}

impl KrakenVenue {
    pub fn new() -> Self {
        Self { symbols: arrayvec::ArrayVec::new() }
    }

    pub fn register_symbol(&mut self, symbol: &str) {
        let hash = crate::moonshot_types::str_to_symbol_hash(symbol);
        if !self.symbols.iter().any(|(h, _)| *h == hash) {
            if let Ok(s) = arrayvec::ArrayString::try_from(symbol) {
                let _ = self.symbols.try_push((hash, s));
            }
        }
    }
}
```

### 3. Implementovat 10 povinných metod

```rust
impl VenueAdapter for KrakenVenue {
    fn id(&self) -> ExchangeId { ExchangeId::Kraken }
    fn name(&self) -> &str { "Kraken" }
    fn env_prefix(&self) -> &str { "KRAKEN" }             // → KRAKEN_API_KEY
    fn ws_url(&self) -> &str { "wss://ws.kraken.com/v2" }

    fn physics(&self) -> ExchangePhysics {
        ExchangePhysics {
            maker_fee_bps: 2.5,      // Kraken maker fee
            taker_fee_bps: 4.0,      // Kraken taker fee
            tick_size: 0.1,          // BTCUSD tick
            lot_size: 0.00001,       // Min BTC
            min_order_size: 0.0001,
            max_orders_per_sec: 15,
        }
    }

    fn auth_message(&self, creds: &ExchangeCredentials) -> Option<String> {
        // Kraken WS auth: generate token via REST, then send via WS
        // ... implementace specifická pro burzu
        None
    }

    fn subscribe_ticker(&self, symbol: &str) -> String {
        format!(r#"{{"method":"subscribe","params":{{"channel":"ticker","symbol":["{}"]}}}}"#, symbol)
    }

    fn subscribe_book(&self, symbol: &str, _precision: &str, depth: u32) -> String {
        format!(r#"{{"method":"subscribe","params":{{"channel":"book","depth":{},"symbol":["{}"]}}}}"#, depth, symbol)
    }

    fn parse_raw(&mut self, raw: &[u8]) -> VenueMessage {
        // Parsování Kraken JSON → VenueMessage
        // Tick: {"channel":"ticker","data":[{"symbol":"BTC/USD","bid":83000,"ask":83001}]}
        // Fill: {"channel":"executions","data":[...]}
        VenueMessage::Unknown
    }

    fn encode_batch(&self, batch: &VenueBatchOrder, buf: &mut bytes::BytesMut) {
        // Kraken batch order:
        // {"method":"add_order","params":{"order_type":"limit","side":"buy",...}}
        // ... Kraken-specific encoding
    }

    fn encode_cancel_gid(&self, _gid: u32, _buf: &mut bytes::BytesMut) { }
    fn encode_cancel_all(&self, buf: &mut bytes::BytesMut) {
        buf.extend_from_slice(br#"{"method":"cancel_all"}"#);
    }

    fn symbol_for_hash(&self, hash: u64) -> Option<&str> {
        self.symbols.iter().find(|(h, _)| *h == hash).map(|(_, s)| s.as_str())
    }
}
```

### 4. Registrovat modul

```rust
// shared/src/exchange/mod.rs — přidat řádek:
pub mod kraken_venue;
```

### 5. Přidat ExchangeId variantu

```rust
// shared/src/exchange/types.rs
pub enum ExchangeId {
    Bitfinex,
    Binance,
    Kraken,   // ← NOVÉ
}
```

### 6. API klíče do .env

```bash
echo 'KRAKEN_API_KEY=your_key_here' >> .env
echo 'KRAKEN_API_SECRET=your_secret_here' >> .env
```

### 7. Build + test

```bash
cargo test -p sniper-shared --lib
cargo build --release
```

### 8. Spustit existujícího bota na nové burze

```rust
// grid/src/main.rs — JEDINÁ ZMĚNA:
let mut runner = SovereignRunner::new(
    engine,
    KrakenVenue::new(),   // ← změní se jen tento řádek
    "Grid-Kraken"
);
```

## REST vs WebSocket burzy

- **WebSocket ordery** (Bitfinex, Kraken): `encode_batch()` píše do `out_buf`
- **REST ordery** (Binance): `encode_batch()` je no-op, místo toho volat `rest_submit_batch()`

## Checklist

- [ ] `VenueAdapter` trait implementován (10 metod)
- [ ] Unit testy (min 5: physics, parse_tick, parse_fill, auth, encode)
- [ ] ExchangeId přidáno
- [ ] Modul registrován v `exchange/mod.rs`
- [ ] API klíče v `.env`
- [ ] `cargo test` projde
- [ ] `cargo build --release` projde
