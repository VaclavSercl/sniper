import mmap
import os
import struct
import time

ORACLE_MMAP_FILE = "/dev/shm/beroun/oracle_state.bin"
CACHE_LINE_SIZE = 64

def init_oracle_mmap():
    """Zajistí existenci souboru a vrátí namapovanou paměť."""
    if not os.path.exists(ORACLE_MMAP_FILE):
        with open(ORACLE_MMAP_FILE, "wb") as f:
            f.write(b'\x00' * CACHE_LINE_SIZE)
            
    f = open(ORACLE_MMAP_FILE, "r+b")
    return mmap.mmap(f.fileno(), CACHE_LINE_SIZE)

def write_oracle_state(mm, sentiment: int, whale_warn: int, regime: int):
    """
    Atomický zápis do L0.
    Formát '<qqqq' znamená 4x little-endian 64-bit signed/unsigned integer.
    """
    heartbeat_ms = int(time.time() * 1000)
    
    # Sbalení dat do bajtů (přesně 32 bajtů)
    payload = struct.pack("<qqqq", sentiment, whale_warn, regime, heartbeat_ms)
    
    # Návrat na začátek a přepsání
    mm.seek(0)
    mm.write(payload)
    mm.flush()
    print(f"[L3 ORACLE] Zapsáno: Sent={sentiment}, Whale={whale_warn}, Reg={regime}, HB={heartbeat_ms}")

if __name__ == "__main__":
    print("🚀 Startuji Asynchronní L3 Orákulum...")
    mm = init_oracle_mmap()
    
    try:
        while True:
            # TODO: Zde bude tvá budoucí logika (LLM, Mempool API)
            # Prozatím simulujeme klidný trh (Sentiment 0, Whale 0, Regime 0=Chop)
            simulated_sentiment = 0 
            simulated_whale = 0
            simulated_regime = 0
            
            write_oracle_state(mm, simulated_sentiment, simulated_whale, simulated_regime)
            
            # Python Orákulum běží na klidném 1 Hz
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n🛑 Orákulum vypnuto.")
        mm.close()
