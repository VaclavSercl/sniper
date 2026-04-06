# Beroun Sniper - Phase 11 Proposal: The Dual-Orbit System

## 1. Architektonická Filosofie 🧠
Přesouváme těžkou, asynchronní logiku a umělou inteligenci do L3 (Python). Rust na úrovni L0 bude nadále udržovat svoji puristickou, zero-allocation podstatu, ale začne slyšet "hlas orákul". Tok informací bude plně jednosměrný a lock-free:

`Python Oracles (1-10 Hz)` → `/dev/shm/beroun/oracle_state.bin` → `armada-core (10 Hz)` → `/dev/shm/beroun/armada_state.bin` → `HFT Boti (Mikrosekundy)`

Armada Orchestrator dostane novou mocnou vstupní proměnnou. Na základě Mempoolových a Sentiment makro-zpráv dynamicky přenastaví Kellyho Matici ještě PŘEDTÍM, než cena na burze vůbec zareaguje.

## 2. Krok 1: Struktura v `shared/src/armada_types.rs` 🌉
Vytvoříme novou MMap bariéru. Python démon sem zapíše svoje výsledky z AI odhadů.

```rust
#[repr(C, align(64))]
pub struct OracleState {
    /// Sentiment vypočtený LLM (-1e8 = Extreme Fear, +1e8 = Extreme Greed)
    pub sentiment_score_fp: AtomicI64,
    
    /// On-Chain Mempool Whale Monitor (0 = Klid, 1 = DUMP Warning, -1 = PUMP Warning)
    pub mempool_whale_warning: AtomicI64,
    
    /// HMM Regime Detection (0 = Chop, 1 = Bull, 2 = Bear, 3 = Toxic)
    pub market_regime: AtomicI64,
    
    /// Epoch MS od posledního vyhodnocení. Pokud je starší než X sekund, 
    /// armada-core padá do "Blind" fallbacku.
    pub oracle_heartbeat_ms: AtomicU64,
}
```

## 3. Krok 2: Python Mempool & Sentiment Spawner 🐍
Místo složitosti napíšeme nový asynchronní Python proces a přidáme jej do `watchdog.sh`: `oracle_daemon.py`.
Démon bude:
1. Vykonávat těžké asynchronní HTTP requesty na LLM API.
2. Poslouchat WebSockety on-chain sítí (Binance Mempool, Glassnode/WhaleAlert).
3. Atomicky paketovat výsledky struktury přes modul `mmap` a `struct.pack('<qqqq')`.

## 4. Krok 3: Exekuce v `armada-core` (Změna Vzorců) ⚔️
Armada Orchestrator načte `OracleState` každých 100 milisekund a pokud zaznamená paniku v Mempoolu (`mempool_whale_warning == 1`), provede preemptivní drtivý Covariance Penalty (úder kapitálu dolů pro všechny long boty) ještě před tím, než reálně spadne cena na trhu. Boti přes "Graceful Shrink", který jsme dokončili dnes, plynule sníží sítě bez výpadku exekuce.

## 5. Cílový Definition of Done
✔ Python démon atomicky odesílá informace o makroprostředí.
✔ Soubor `oracle_state.bin` komunikuje se zbytkem systému bez navýšení latence.
✔ Rust Orchestrator plynule přiškrcuje `authorized_capital` když se očekává volatilita.
✔ Integrace je vizualizována na portu 3000 jako "Oracle Intelligence".

Tento návrh zhmotní "Sovereign AI" infrastrukturu.
