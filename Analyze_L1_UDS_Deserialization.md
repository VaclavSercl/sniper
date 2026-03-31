# Analyze_L1_UDS_Deserialization.md

## 1. Bug Hunting & Security
- **UDS Memory Deserialization Kopírování**: Naslouchací smyčka `run_uds_server` používá `String::new()` a `buf_reader.read_line(&mut line)`, z čehož deserializuje přes `serde_json::from_str`. U síťového UDS parsování (navíc s JSON payloady až stovek bytů) to produkuje obrovské množství heap trafficu v L1. Parsování textu nutí paměť dělat minimálně dvě kopie dat.
- **Data Races a alokace uvnitř asynchronních tokio tasků**: Struktura zpráv `UdsRequest` je definována s `String` poli (`cmd`, `bot`, `regime`). V kombinaci s `serde_json` to znamená stoprocentní kopírování stringů do haldy při každém parser hitu. Pro HFT L1 je toto plýtváním cycles. 

## 2. Odstranění Zombie
Tato ukázka line-by-line čtenáře s plnou heap alokací nedává jako multiplexer na UDS smysl:
```rust
let mut line = String::new(); // Zombie / Alokace při každém connection!
if buf_reader.read_line(&mut line).await.is_err() { return; }
let response = match serde_json::from_str::<UdsRequest>(line.trim()) { ... }
```
A stejně tak u serializace, kde se dělá `to_string()` místo přímého zápisu do TCP stream bufferu:
```rust
if let Ok(json) = serde_json::to_string(&response) { ... } // Zombie Heap trashing
```

## 3. HFT Optimalizace podle vrstvy (L1 Rust/GPU)
- **Zero-Copy struktury**: Přepsat `UdsRequest` tak, aby používalo lifetime borrow. Tzn. `struct UdsRequest<'a> { cmd: &'a str, bot: Option<&'a str> ... }`.
- **BytesMut + from_slice**: Tokio má framework `bytes` a `BytesMut`. Namísto string-readeru se buffer načte do před-alokovaného bytového pole a pomocí `serde_json::from_slice` z něj Serde vytahá rovnou reference `&str`, což sníží garbage collection na absolutní NULU.
- **Položená ruka na serializeru**: `serde_json::to_writer` nad TCP streamem (nebo do pre-alokovaného byte-buffere) zamezí tvorbě mezi-tvaru `String`. 

## 4. Refaktorovaný kód (Návrh optimalizace UDS `architect/cortex/src/uds.rs`)
```rust
use bytes::BytesMut;
use serde::Deserialize;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

// L1 ZERO-COPY: Namísto String žijeme z buffer stacku 
#[derive(Deserialize)]
struct UdsRequest<'a> {
    cmd: &'a str,
    #[serde(default)]
    bot: Option<&'a str>,
    #[serde(default)]
    value: Option<f64>,
    #[serde(default)]
    regime: Option<&'a str>,
}

pub async fn run_uds_server(memory: Arc<RwLock<ArmadaMemory>>) {
    // ...
    loop {
        let (mut stream, _) = match listener.accept().await {
            Ok(s) => s,
            Err(_) => continue,
        };
        let mem = Arc::clone(&memory);
        let uptime = start.elapsed().as_secs();

        // 1 spojení = 1 task, ale bez zbytečných vnitřních alokací
        tokio::spawn(async move {
            let mut buf = BytesMut::with_capacity(1024);
            // ZERO-COPY: Čtení do statického bufferu
            stream.read_buf(&mut buf).await.unwrap_or(0);

            let response = match serde_json::from_slice::<UdsRequest>(&buf) {
                Ok(req) => handle_request(req, &mem, uptime).await,
                Err(e) => UdsResponse::err("Invalid JSON_SLICE"),
            };

            // ZERO-COPY: Přímý zápis na rouru přes buffer (žádný to_string())
            let mut out_buf = Vec::with_capacity(2048);
            serde_json::to_writer(&mut out_buf, &response).unwrap();
            out_buf.push(b'\n');
            let _ = stream.write_all(&out_buf).await;
        });
    }
}
```
