#!/usr/bin/env python3
"""
🧠 L1 ML Shield — Protocol 4.0 Sovereign Oracle
Sniper Armada · Institucionální Standard s Volume-Bucketed VPIN a NumPy Zero-Copy

Blesková paměťová vektorizace nahrazuje pomalé dekódování v cyklu. Tím získáváme True HFT
parametry bez GIL jitteru při skenování všech vrstev knihy.
"""
import os
import time
import mmap
import struct
import numpy as np
import logging

try:
    from l2_rust_offsets import OFF_L1_SKEW, OFF_L1_CONF
except ImportError:
    OFF_L1_SKEW = 1296
    OFF_L1_CONF = 1300 # Aproximace pokud nenalezeno

logging.basicConfig(level=logging.INFO, format='%(asctime)s [ml_shield] 🧠 %(message)s')
log = logging.getLogger("ml_shield")

# ═══════════════════════════════════════════════════════════
# KONFIGURACE PAMĚTI A STRUKTUR
# ═══════════════════════════════════════════════════════════
L2_CMD_PATH = '/dev/shm/beroun/l2_command.bin'
TOXIC_STORM_PATH = '/dev/shm/beroun/toxic_storm.bin'
ENGINE_PATH = '/dev/shm/beroun/engine_state.bin'

PRICE_SCALE = 1e8
L2_BOOK_LEVELS = 25  # Na základě striktní verifikace C struktur Rust jádra (50 úr. je pro větší paměti)

# NumPy dtype mapující přesně C-strukturu O(1) Zero-Copy pro l2_book/engine_state
engine_dtype = np.dtype([
    ('latency', np.uint64),          # offset 0
    ('pad_hb', np.uint8, 56),        # padding to align cache
    ('best_bid', np.uint64),         # offset 64
    ('best_ask', np.uint64),         # offset 72
    ('bids', np.dtype([              # offset 80
        ('price', np.uint64),
        ('amount', np.int64),
        ('count', np.uint64),
    ]), L2_BOOK_LEVELS),
    ('asks', np.dtype([              # offset 680
        ('price', np.uint64),
        ('amount', np.int64),
        ('count', np.uint64),
    ]), L2_BOOK_LEVELS),
])

# ═══════════════════════════════════════════════════════════
# MATEMATICKÉ JÁDRO (VB-VPIN & DYNAMIC THRESHOLDS)
# ═══════════════════════════════════════════════════════════
class VolumeBucketedVPIN:
    def __init__(self, bucket_size_btc=5.0, window_size=50):
        self.bucket_size = bucket_size_btc
        self.window_size = window_size
        self.current_buy_vol = 0.0
        self.current_sell_vol = 0.0
        self.buckets = []
        
    def add_trade_flow(self, buy_vol, sell_vol):
        self.current_buy_vol += buy_vol
        self.current_sell_vol += sell_vol
        total_vol = self.current_buy_vol + self.current_sell_vol
        
        # Pokud se "kyblík" naplní, uzavřeme ho a posuneme okno
        if total_vol >= self.bucket_size:
            imbalance = abs(self.current_buy_vol - self.current_sell_vol)
            self.buckets.append(imbalance)
            if len(self.buckets) > self.window_size:
                self.buckets.pop(0)
            
            # Zbytek převádíme do nového kyblíku (zjednodušeně resetujeme)
            self.current_buy_vol = 0.0
            self.current_sell_vol = 0.0
            
    def get_vpin(self):
        if len(self.buckets) < self.window_size // 2:
            return 0.5 # Default neutrál, dokud nemáme data
        
        # VPIN = Sum(|Buy - Sell|) / (Total Volume in Window)
        total_imbalance = sum(self.buckets)
        total_volume = len(self.buckets) * self.bucket_size
        return min(1.0, total_imbalance / total_volume)

class DynamicThreshold:
    def __init__(self, alpha=0.05, min_std=0.5):
        self.alpha = alpha
        self.ema_val = 0.0
        self.ema_var = 0.0
        self.min_std = min_std
        self.initialized = False
        
    def update_and_get_zscore(self, current_val):
        if not self.initialized:
            self.ema_val = current_val
            self.ema_var = self.min_std ** 2
            self.initialized = True
            return 0.0
            
        # Standardní EMA
        delta = current_val - self.ema_val
        self.ema_val += self.alpha * delta
        self.ema_var = (1 - self.alpha) * self.ema_var + self.alpha * (delta ** 2)
        
        std_dev = max(np.sqrt(self.ema_var), self.min_std)
        z_score = abs(current_val - self.ema_val) / std_dev
        return z_score

# ═══════════════════════════════════════════════════════════
# HLAVNÍ SMYČKA ORÁKULA
# ═══════════════════════════════════════════════════════════
def run_oracle():
    log.info("Inicializace paměťových rovin a NumPy Memory-Views...")
    
    # MMap připojení (ReadOnly pro Book, ReadWrite pro Command)
    if not os.path.exists(L2_CMD_PATH):
        with open(L2_CMD_PATH, 'wb') as f: f.write(b'\x00' * 896)
    fd_cmd = os.open(L2_CMD_PATH, os.O_RDWR)
    mm_cmd = mmap.mmap(fd_cmd, 896)
    
    # Řešení Toxic Storm mapování na 1 byte (Srovnáno dle jádra z předchozí verze)
    os.makedirs(os.path.dirname(TOXIC_STORM_PATH), exist_ok=True)
    if not os.path.exists(TOXIC_STORM_PATH):
        with open(TOXIC_STORM_PATH, 'wb') as f: f.write(b'\x00')
    fd_storm = os.open(TOXIC_STORM_PATH, os.O_RDWR)
    mm_storm = mmap.mmap(fd_storm, 1)
    
    while not os.path.exists(ENGINE_PATH):
        log.info("Čekám na spuštění Rust L0 a vytvoření L2 Booku...")
        time.sleep(1)
        
    fd_book = os.open(ENGINE_PATH, os.O_RDWR) # Musíme mít RDWR, z bezpečnostních důvodů resetujeme Skew
    mm_book = mmap.mmap(fd_book, 0)
    
    # Zero-Copy NumPy View (Neprovádí žádné alokace ani for cykly!)
    book_view = np.frombuffer(mm_book, dtype=engine_dtype, count=1, offset=0)
    
    vpin_engine = VolumeBucketedVPIN(bucket_size_btc=2.0) # Velrybí objemový kyblík
    spread_tracker = DynamicThreshold(alpha=0.01) # Pomalá adaptace na volatilitu
    
    # === PARAMETRICKÁ INJEKCE LEVEL 5 ===
    PARAMS_PATH = '/dev/shm/beroun/oracle_params.bin'
    
    if not os.path.exists(PARAMS_PATH):
        with open(PARAMS_PATH, 'wb') as f:
            f.write(struct.pack('<ddd', 2.0, 3.5, 0.8)) # Default: bucket, z_score, vpin
            
    fd_params = os.open(PARAMS_PATH, os.O_RDONLY)
    mm_params = mmap.mmap(fd_params, 24, access=mmap.ACCESS_READ)
    params_view = np.frombuffer(mm_params, dtype=np.float64, count=3)
    # ====================================

    log.info("L2 Oracle v3.0 (NumPy Zero-Copy | Dynamic Parameters) ONLINE. Čekám na trh...")
    
    last_mid = 0.0
    
    while True:
        try:
            # 1. BLESKOVÉ ČTENÍ Z PAMĚTI (Bez GIL blokace)
            data = book_view[0]
            best_bid = data['best_bid'] / PRICE_SCALE
            best_ask = data['best_ask'] / PRICE_SCALE
            
            if best_bid == 0 or best_ask == 0:
                time.sleep(0.01)
                continue
                
            # === AKTUALIZOVAT UVNITŘ SMYČKY (LIVE TUNING) ===
            current_bucket_size = params_view[0]
            current_z_threshold = params_view[1]
            current_vpin_threshold = params_view[2]
            
            vpin_engine.bucket_size = current_bucket_size
            # ================================================
                
            mid_price = (best_bid + best_ask) / 2.0
            spread = best_ask - best_bid
                
            # Simulace Trade Flow
            delta_mid = mid_price - last_mid
            buy_pressure = abs(delta_mid) if delta_mid > 0 else 0
            sell_pressure = abs(delta_mid) if delta_mid < 0 else 0
            vpin_engine.add_trade_flow(buy_pressure, sell_pressure)
            last_mid = mid_price
            
            # 2. VEKTORIZOVANÁ DEEP OBI (Celých 25 úrovní naráz)
            bids = data['bids']
            asks = data['asks']
            
            # Váha = 1 / Vzdálenost od středu (Vektorizovaně!)
            bid_distances = np.maximum(mid_price - (bids['price'] / PRICE_SCALE), 0.1)
            ask_distances = np.maximum((asks['price'] / PRICE_SCALE) - mid_price, 0.1)
            
            valid_bids = bids['price'] > 0
            valid_asks = asks['price'] > 0
            
            weighted_bids = np.sum((np.abs(bids['amount'][valid_bids]) / PRICE_SCALE) / bid_distances[valid_bids])
            weighted_asks = np.sum((np.abs(asks['amount'][valid_asks]) / PRICE_SCALE) / ask_distances[valid_asks])
            
            total_weighted_liquidity = weighted_bids + weighted_asks
            obi = (weighted_bids - weighted_asks) / total_weighted_liquidity if total_weighted_liquidity > 0 else 0.0
            
            # 3. KOMBINOVANÁ LOGIKA A DYNAMICKÉ Z-SCORE
            current_vpin = vpin_engine.get_vpin()
            spread_z = spread_tracker.update_and_get_zscore(spread)
            
            # Výpočet Ranging / Trending Skóre
            trending_raw = current_vpin + (abs(obi) * 0.5)
            ranging_raw = 1.0 - trending_raw
            
            # Normalizace
            total_score = trending_raw + ranging_raw
            trending_score = (trending_raw / total_score) if total_score > 0 else 0.0
            ranging_score = (ranging_raw / total_score) if total_score > 0 else 1.0
            
            # DETEKCE TOXICKÉ BOUŘE S AUTONOMNÍMI LIMITY
            is_storm = 1 if (spread_z > current_z_threshold and current_vpin > current_vpin_threshold and abs(obi) > 0.7) else 0
            if is_storm == 1:
                log.warning(f"🌩️ TOXIC STORM ACTIVATED — vpin={current_vpin:.3f} spread_z={spread_z:.2f} obi={obi:.3f} (Limity: {current_z_threshold:.1f}/{current_vpin_threshold:.2f})")
            
            # 4. ZÁPIS DO SDÍLENÉ PAMĚTI (Přesně na bajty)
            ranging_scaled = int(ranging_score * PRICE_SCALE)
            trending_scaled = int(trending_score * PRICE_SCALE)
            
            struct.pack_into('<QQ', mm_cmd, 240, ranging_scaled, trending_scaled)
            mm_storm.seek(0)
            mm_storm.write(struct.pack('<B', is_storm))
            
            # Bezpečnostní vynulování Skew parametrů (už nepoužíváme model z minulé verze)
            struct.pack_into('<q', mm_book, OFF_L1_SKEW, 0)
            struct.pack_into('<Q', mm_book, OFF_L1_CONF, 10000)
            
            # Spánek 10ms (100 Hz refresh rate - Python zvládá s nulovým driftem díky NumPy maticím)
            time.sleep(0.01)
            
        except Exception as e:
            log.error(f"Kritická chyba v Oracle smyčce: {e}")
            time.sleep(1)

if __name__ == '__main__':
    run_oracle()
