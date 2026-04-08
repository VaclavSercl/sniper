import asyncio
import json
import mmap
import os
import struct
import time
from collections import deque
import statistics
import websockets

ORACLE_MMAP_FILE = "/dev/shm/sniper/oracle_state.bin"
CACHE_LINE_SIZE = 64

# Konfigurace detekce
HISTORY_LENGTH = 300       # 5 minut (300 vteřinových bucketů)
WHALE_STD_DEV = 3.0        # Práh: Kolik směrodatných odchylek nad průměr?
HYSTERESIS_SEC = 5.0       # Jak dlouho držet poplach po detekci velryby

def init_oracle_mmap():
    if not os.path.exists(ORACLE_MMAP_FILE):
        with open(ORACLE_MMAP_FILE, "wb") as f:
            f.write(b'\x00' * CACHE_LINE_SIZE)
    f = open(ORACLE_MMAP_FILE, "r+b")
    return mmap.mmap(f.fileno(), CACHE_LINE_SIZE)

def write_oracle_state(mm, sentiment: int, whale_warn: int, regime: int):
    heartbeat_ms = int(time.time() * 1000)
    payload = struct.pack("<qqqq", sentiment, whale_warn, regime, heartbeat_ms)
    mm.seek(0)
    mm.write(payload)
    mm.flush()

class MempoolScanner:
    def __init__(self):
        self.mm = init_oracle_mmap()
        self.history = deque(maxlen=HISTORY_LENGTH)
        self.current_sec = int(time.time())
        self.current_vol = 0.0
        
        self.whale_warn_until = 0.0
        
        self.sentiment = 0
        self.regime = 0

    async def scan_binance(self):
        uri = "wss://stream.binance.com:9443/ws/btcusdt@aggTrade"
        
        print(f"🌊 Připojuji se k Binance Mempoolu (AggTrades)...")
        while True:
            try:
                async with websockets.connect(uri) as websocket:
                    print("✅ Připojeno. Naslouchám objemům přes L3 Radar...")
                    
                    # Spustíme heartbeat task, který periodicky posílá zápis,
                    # i když se zrovna nic neděje, aby Rust nezpanikařil.
                    asyncio.create_task(self.heartbeat_loop())
                    
                    async for message in websocket:
                        data = json.loads(message)
                        self.process_trade(data)
                        
            except Exception as e:
                print(f"⚠️ Spojení přerušeno: {e}. Reconnecting za 2s...")
                await asyncio.sleep(2)

    def process_trade(self, trade):
        now = time.time()
        now_sec = int(now)
        amount = float(trade['q'])  # Množství BTC
        
        if now_sec != self.current_sec:
            # Uložíme předchozí bucket
            self.history.append(self.current_vol)
            self.current_vol = 0.0
            self.current_sec = now_sec
            
        self.current_vol += amount
        
        # Detekce velryby (potřebujeme alespoň nějakou historii)
        if len(self.history) > 10:
            mean_vol = statistics.mean(self.history)
            std_vol = statistics.pstdev(self.history)
            
            # Pokud je aktuální objem statistická anomálie
            threshold = mean_vol + (WHALE_STD_DEV * std_vol)
            
            if self.current_vol > threshold and threshold > 1.0: # (min. 1 BTC aby to nebyl šum v klidu)
                if now > self.whale_warn_until:
                    print(f"🚨 [WHALE DUMP] Obrovský blok zachycen! Objem v bucketu: {self.current_vol:.2f} BTC (Práh: {threshold:.2f} BTC)")
                self.whale_warn_until = now + HYSTERESIS_SEC

    async def heartbeat_loop(self):
        while True:
            now = time.time()
            whale_status = 1 if now < self.whale_warn_until else 0
            
            write_oracle_state(self.mm, self.sentiment, whale_status, self.regime)
            await asyncio.sleep(0.5)

if __name__ == "__main__":
    scanner = MempoolScanner()
    
    try:
        asyncio.run(scanner.scan_binance())
    except KeyboardInterrupt:
        print("\n🛑 Mempool Scanner vypnut.")
        scanner.mm.close()
