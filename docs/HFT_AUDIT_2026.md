# HFT-Sniper: Technický Audit a Architektonická Příručka (v5.0+)
*Standardy pro Ultra-Low Latency Trading 2026*

Tento dokument definuje klíčové požadavky a strategie pro vývoj a provoz systému **Beroun Sniper**.

## 1. Architektura a Latency Budget
*   **Cílová latence:** "Tick-to-Trade" pipeline musí dosahovat softwarové latence přibližně **4.5 µs**.
*   **Integrita Hot Path:** Kritická smyčka (`main.rs`) musí být bez blokujících operací, systémových volání OS nebo nedeterministického chování.
*   **Zero-Cost Abstractions:** Využití Rust ownership modelu pro výkon na úrovni C++ se zárukou bezpečnosti paměti bez Garbage Collectoru (GC).

## 2. Networking a Kernel Bypass
*   **Kernel Bypass:** Standardní Berkeley sockety jsou nepřípustné kvůli režii OS. Povinné technologie: **DPDK**, **Solarflare ef_vi**, nebo **Mellanox VMA**.
*   **Poll Mode Drivers (PMD):** Dedikovaná CPU jádra musí běžet v nepřetržité polling smyčce (DMA příjem), bez přerušení (interrupts).
*   **Moderní Linux API:** Použití **io_uring** a **eBPF/AF_XDP** pro vysoce výkonné asynchronní I/O a zero-copy networking.

## 3. Protokoly a Datový Tok
*   **Binary over Text:** Odklon od ASCII FIX protokolu k binárním standardům jako **SBE (Simple Binary Encoding)**, **OUCH**, nebo **ITCH**.
*   **Zero-Copy Parsing:** Přímé přetypování (casting) raw síťových bufferů do Rust struktur pro eliminaci režie deserializace.
*   **Eliminace Stringů:** Zákaz alokace `String` v hot path; použití `&[u8]` (byte slicing) pro veškerou logiku.

## 4. Správa Paměti
*   **No Allocation v Hot Path:** Striktní pravidlo "Alokuj téměř nikdy".
*   **Memory Pools & Arenas:** Před-alokace veškeré potřebné paměti při startu aplikace.
*   **Memory Locking:** Použití `mlock` pro zabránění swapování kritických stránek paměti na disk (prevence page faults).
*   **Cache-Aware Struktury:** Použití lock-free order booků (např. `orderbook-rs`) využívajících souvislé bloky paměti.

## 5. Konkurence a Synchronizace
*   **Lock-Free Design:** Nahrazení `Mutex` a `RwLock` pomocí **atomických operací** (`Compare-and-Swap`) k zamezení context switchům.
*   **Memory Ordering:** Precizní použití `Ordering::Release` a `Ordering::Acquire` pro řízení konzistence CPU cache.
*   **SPSC Queues:** Využití **Single-Producer Single-Consumer** ring bufferů pro IPC s minimální kontencí.

## 6. Ladění Hardware a OS
*   **CPU Pinning & Isolation:** Použití `isolcpus` a `nohz_full` k dedikaci specifických jader výhradně pro bota.
*   **NUMA Awareness:** Zajištění alokace paměti na stejném fyzickém CPU socketu, kde běží procesní vlákno.
*   **Prevence False Sharing:** Implementace **paddingu** (64-byte alignment) v datových strukturách (včetně IPC mmap).

## 7. Kompilátor a Build Optimalizace
*   **LLVM Optimalizace:** Nastavení `codegen-units = 1` a aktivace **LTO (Link-Time Optimization)**.
*   **PGO (Profile-Guided Optimization):** Třístupňový proces optimalizace predikce větvení na základě reálných tržních dat (pcap).
*   **Native Instructions:** Použití `-C target-cpu=native` pro využití hardware instrukcí jako **AVX-512**.

## 8. Specializované Strategie (Crypto/Solana)
*   **Block-0 Execution:** Pro Solanu podpora **Jito-Bundle** exekuce a native buffer injection.
*   **Direct Validator Injection:** Obcházení standardních RPC uzlů a zasílání transakcí přímo prioritním validátorům.
