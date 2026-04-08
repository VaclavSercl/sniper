#!/usr/bin/env python3
import os
import time
import mmap
import struct
import logging
import numpy as np
from sklearn.linear_model import SGDClassifier
from sklearn.preprocessing import StandardScaler
import joblib

logging.basicConfig(level=logging.INFO, format='%(asctime)s [ml_injector] 💉 %(message)s')
log = logging.getLogger("ml_injector")

WEIGHTS_PATH = '/dev/shm/sniper/ml_weights.bin'
TELEMETRY_PATH = '/dev/shm/sniper/ml_telemetry.npy'
MODEL_CACHE_PATH = '/home/wwwenda/sniper/state/ml_model.joblib'
SCALER_CACHE_PATH = '/home/wwwenda/sniper/state/ml_scaler.joblib'
FMT_WEIGHTS = '<Q 11f 11f 10f 10f 16x'

if os.path.exists(MODEL_CACHE_PATH) and os.path.exists(SCALER_CACHE_PATH):
    clf = joblib.load(MODEL_CACHE_PATH)
    scaler = joblib.load(SCALER_CACHE_PATH)
    log.info("Načteny uložené AI modely z disku.")
else:
    clf = SGDClassifier(loss='log_loss', penalty='l2', alpha=0.0001, learning_rate='optimal') 
    scaler = StandardScaler()
    log.info("Založeny nové AI modely pro online trénink.")

last_telemetry_mtime = 0

def fetch_training_data():
    global last_telemetry_mtime
    if not os.path.exists(TELEMETRY_PATH):
        return None, None
        
    mtime = os.path.getmtime(TELEMETRY_PATH)
    if mtime <= last_telemetry_mtime:
        return None, None # Žádná nová data
        
    last_telemetry_mtime = mtime
    
    # Načtení dat (Zero-Copy O(1))
    X = np.load(TELEMETRY_PATH)
    
    # --- AUTO-ŠTÍTKOVÁNÍ (Labeling) ---
    # Nechceme manuálně štítkovat toxicitu. Použijeme tvé pravidlo:
    # Top 5% nejhorších momentů = Toxic(1), zbytek(0).
    # Jako ukazatel paniky spojíme vysoký Spread (idx 0) a VPIN (idx 1).
    panic_indicator = X[:, 0] * X[:, 1]
    threshold = np.percentile(panic_indicator, 95) # 95. percentil
    
    y = np.where(panic_indicator >= threshold, 1, 0)
    
    log.info(f"Načteno {len(X)} vzorků L2 telemetrie. Detekováno {np.sum(y)} toxických stavů.")
    return X, y

def train_and_extract_weights():
    global clf, scaler
    
    X_raw, y = fetch_training_data()
    if X_raw is None or len(np.unique(y)) < 2:
        return None

    scaler.partial_fit(X_raw)
    X_scaled = scaler.transform(X_raw)

    clf.partial_fit(X_scaled, y, classes=np.array([0, 1]))
    
    os.makedirs(os.path.dirname(MODEL_CACHE_PATH), exist_ok=True)
    joblib.dump(clf, MODEL_CACHE_PATH)
    joblib.dump(scaler, SCALER_CACHE_PATH)

    coefficients = clf.coef_[0].astype(np.float32)
    bias = np.array([clf.intercept_[0]], dtype=np.float32)

    w_fast = np.concatenate([coefficients, bias])
    w_slow = w_fast * 0.5 

    mean = scaler.mean_.astype(np.float32)
    var = scaler.var_.astype(np.float32)

    return w_fast, w_slow, mean, var

def inject_weights():
    weights_data = train_and_extract_weights()
    if not weights_data:
        return
        
    w_fast, w_slow, mean, var = weights_data

    try:
        with open(WEIGHTS_PATH, 'r+b') as f:
            mm = mmap.mmap(f.fileno(), 192)

            current_version = struct.unpack_from('<Q', mm, 0)[0]
            new_version = current_version + 1

            struct.pack_into(FMT_WEIGHTS, mm, 0,
                             new_version,
                             *w_fast,
                             *w_slow,
                             *mean,
                             *var)

            log.info(f"HOT-SWAP ÚSPĚŠNÝ! Mozek v{new_version} | Bias: {w_fast[-1]:.4f} | Váhy C[0-3]: {w_fast[0]:.2f}, {w_fast[1]:.2f}, {w_fast[2]:.2f}, {w_fast[3]:.2f}")
            mm.close()
            
    except Exception as e:
        log.error(f"Chyba při injekci: {e}")

if __name__ == '__main__':
    log.info("Spouštím Autonomous Neural Link...")
    while True:
        inject_weights()
        time.sleep(10) # Kontroluje nové telemetry soubory každých 10s
