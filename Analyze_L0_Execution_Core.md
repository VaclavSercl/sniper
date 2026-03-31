# Analyze_L0_Execution_Core.md

## 1. Bug Hunting & Security
- **Memory Allocation in Hot Path**: Ve smyčce exekučního mdata loopu (`tokio::select!` block) neustále alokujeme paměť pro zprávy přes `String::with_capacity(128)`, volání `format!` a `to_string()`, potažmo vytváříme celá nová pole přes `order_parts.push(...)` a `order_parts.join(",")`. To je u Rust HFT fatální anti-pattern. Způsobuje to garbage traffic a zbytečně přetěžuje alokátor, což dává nedeterministické spike k latenci o mikrosekundy. Veškeré sestavování zpráv musí být na bufferovaném poli (static / thread-local `Vec<u8>` na který se použije `clear()`, nebo alokace na stacku).
- **Poignant Latency Trap**: Používání logovacího makra `tracing::warn!` a `info!` s formátovací alokací interpolace řetězců jako `format!("{:.4}", depth_btc)` přímo uprostřed kritické exekuční tick smyčky HFT bota (line 821, 910 atd.). Logování musí přijímat primitivní typy a formátovat je až asynchronně worker vláknem, nikoliv zpomalovat hlavní selektor čekačkou na konverzi a `ryu`.

## 2. Zero-Tolerance na Zombie kód
Následující nebezpečně sestavované sekvence zpráv z bloku `GHOST` injektora se mažou a přepisují na binární payload buffering:
```rust
// Zcela zakázaný vzor v HFT smyčce
let mut msg = String::with_capacity(128); // ALOKACE
let mut itoa_buf = itoa::Buffer::new();
let mut ryu1 = ryu::Buffer::new();
let mut ryu2 = ryu::Buffer::new();
msg.push_str(r#"[0,"on",null,{"gid":"#);
...
let _ = order_tx.send(msg);

// Totéž pro tvorbu MULTI LIMIT příkazů (Line 1078)
order_parts.push(format!(
    r#"["on",{{"gid":{},"symbol":"{}","amount":"{:.5}","price":"{:.2}","type":"EXCHANGE LIMIT","flags":4096}}]"#,
     BOT_GID_HYDRA, TRADING_SYMBOL, amt, bp
));
```

## 3. Optimalizace podle Vrstvy (L0 / Rust)
- **Eliminate FPU & f64 z Main Loopu**: Obchodní logika spoléhá na floaty: `amt_i as f64 / PRICE_SCALE` a tunu dalších f64 výpočtů (`base_usd * 0.5`, `buy_i as f64`, atd.). L0 musí interně počítat s `i64` satoshis fixed point numbers. Výpočty order imbalance a OBI je nutné předělat na promile v i64 a z konverze floatů udělat "zapovězené slovo".
- **Zero-Copy Serialization do Websocketu**: Odstranit posílání zpráv přes `mpsc::unbounded_channel::<String>()`, které žongluje s ownershipem `String`u napříč thready. Místo toho musí loop posílat buď signály pro před-alokované `[u8]` buffery, anebo pracovat s poolovanými `Bytes`/`Vec<u8>`.

## 4. Refaktorovaný kód (Návrh pro `hydra/src/main.rs`)
```rust
// Příklad přechodu k Zero-Allocation string builderu pomocí Fixed Stack Buffer
// nebo recyklovaných objektů v L0.
use std::io::Write;
use bytes::BytesMut;

// Lokální thread buffer (recyklován v každém ticku)
// ... před spuštěním smyčky ...
let mut ws_buffer = BytesMut::with_capacity(2048); 

// uvnitř HFT smyčky:
ws_buffer.clear();
write!(
    &mut ws_buffer,
    r#"[0,"on",null,{{"gid":{},"symbol":"{}","amount":"{}","price":"{}","type":"EXCHANGE IOC"}}]"#,
    sniper_types::BOT_GID_HYDRA,
    sniper_types::TRADING_SYMBOL,
    amt_i,   // Použijeme fixed-point Integer a Bitfinex API přijme SATs nebo jej asynchronně mapujeme mimo L0 stack. 
    gbp_u
).expect("Buffer write failed");

// posíláme pouhý byte buffer přes bytes objekt na zero-copy frontu místo String ownershipu
let _ = order_tx.send(ws_buffer.split().freeze());
```
