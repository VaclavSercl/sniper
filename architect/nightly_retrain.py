#!/usr/bin/env python3
"""
📈 Nightly ML Shield Retrain — SIM v2.0 P2-A
Sniper Armada · Continuous Learning Pipeline

Runs at midnight via cron. Trains new model weights on last 24h of fills,
validates against 6h counterfactual backtest (Shadow Validation Gate).
Only hot-swaps if v_new outperforms v_old.

Cron entry:
  0 0 * * * cd /home/wwwenda/sniper/architect && python3 nightly_retrain.py >> /home/wwwenda/sniper/logs/retrain.log 2>&1

Architecture:
  1. Load 24h fills from pnl.db
  2. Load 24h feature snapshots from market_data.db (1m candles)
  3. Train v_new model (gradient descent, 1 epoch)
  4. Load v_old weights from disk
  5. Shadow Validation: run both on last 6h OOS data
  6. If v_new hit_rate > v_old hit_rate AND v_new mark_out > v_old → swap
  7. Save winner to model_weights.npz

Safety: If v_new is worse → silently discard. Zero risk of catastrophic forgetting.
"""

import os
import sys
import json
import time
import sqlite3
import logging
import mmap
import struct
import numpy as np
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [RETRAIN] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger('retrain')

# Paths
PNL_DB = os.path.expanduser("~/.local/share/sniper/pnl.db")
MARKET_DB = os.path.expanduser("~/.local/share/sniper/market_data.db")
MODEL_PATH = os.path.expanduser("~/.local/share/sniper/model_weights.npz")
HISTORY_PATH = os.path.expanduser("~/.local/share/sniper/retrain_history.json")
PRICE_SCALE = 100_000_000

# Model config (must match ml_shield.py)
N_FEATURES = 10
LEARNING_RATE = 0.0005
L2_REG = 0.01


def load_fills(hours=24):
    """Load closing fills from pnl.db for the last N hours."""
    if not os.path.exists(PNL_DB):
        log.error(f"PnL DB not found: {PNL_DB}")
        return []

    conn = sqlite3.connect(PNL_DB)
    cutoff_ms = int((time.time() - hours * 3600) * 1000)
    
    rows = conn.execute(
        """SELECT ts_ms, bot, price, amount, net_pnl, is_closer 
           FROM fills WHERE ts_ms >= ? ORDER BY ts_ms""",
        (cutoff_ms,)
    ).fetchall()
    conn.close()
    
    log.info(f"Loaded {len(rows)} fills from last {hours}h")
    return rows


def load_candles(hours=24):
    """Load 1m candles from market_data.db for feature reconstruction."""
    if not os.path.exists(MARKET_DB):
        log.warning(f"Market DB not found: {MARKET_DB} — skipping candle data")
        return []

    conn = sqlite3.connect(MARKET_DB)
    cutoff_ms = int((time.time() - hours * 3600) * 1000)
    
    rows = conn.execute(
        """SELECT ts, open, high, low, close, volume 
           FROM candles_1m WHERE ts >= ? ORDER BY ts""",
        (cutoff_ms,)
    ).fetchall()
    conn.close()
    
    log.info(f"Loaded {len(rows)} candles from last {hours}h")
    return rows


def extract_features_from_candles(candles):
    """Reconstruct approximate features from 1m candle data.
    
    Returns list of (features_10d, direction) tuples.
    direction: +1 if next candle closed higher, -1 if lower.
    """
    if len(candles) < 25:
        return []
    
    training_data = []
    
    for i in range(20, len(candles) - 1):
        c = candles[i]
        ts, open_p, high, low, close, volume = c
        
        # Feature 1: OBI approximation (close relative to high-low range)
        hl_range = high - low if high > low else 1
        obi = (close - low) / hl_range * 2 - 1  # [-1, +1]
        
        # Feature 2: Weighted OBI (use volume as weight)
        w_obi = obi * min(volume / 1.0, 1.0)  # vol-weighted
        
        # Feature 3: Spread approx (high-low relative to close)
        spread_bps = (high - low) / close * 10000
        
        # Feature 4: VPIN approx (abs of directional flow)
        vpin = abs(obi) * 0.5
        
        # Feature 5: OBI momentum (EMA derivative)
        if i >= 25:
            obi_5ago = (candles[i-5][4] - candles[i-5][3]) / max(1, candles[i-5][2] - candles[i-5][3]) * 2 - 1
            obi_momentum = obi - obi_5ago
        else:
            obi_momentum = 0
        
        # Feature 6: Price return 5 candles
        prev_5_close = candles[i-5][4]
        ret_5 = (close - prev_5_close) / prev_5_close * 10000 if prev_5_close > 0 else 0
        
        # Feature 7: Price return 20 candles
        idx_20 = max(0, i - 20)
        prev_20_close = candles[idx_20][4]
        ret_20 = (close - prev_20_close) / prev_20_close * 10000 if prev_20_close > 0 else 0
        
        # Feature 8: Spread z-score
        recent_spreads = [(candles[j][2] - candles[j][3]) / candles[j][4] * 10000 
                          for j in range(max(0, i-50), i) if candles[j][4] > 0]
        if len(recent_spreads) > 5:
            s_mean = np.mean(recent_spreads)
            s_std = np.std(recent_spreads)
            spread_z = (spread_bps - s_mean) / s_std if s_std > 0.001 else 0
        else:
            spread_z = 0
        
        # Feature 9: Depth asymmetry approx (volume trend)
        if i >= 3:
            vol_recent = sum(candles[j][5] for j in range(i-3, i+1))
            vol_prev = sum(candles[j][5] for j in range(max(0, i-7), i-3))
            depth_asym = (vol_recent - vol_prev) / max(vol_prev, 0.001) if vol_prev > 0 else 0
        else:
            depth_asym = 0
        
        # Feature 10: Volatility regime
        recent_rets = [(candles[j][4] - candles[j-1][4]) / candles[j-1][4] 
                       for j in range(max(1, i-20), i+1) if candles[j-1][4] > 0]
        vol_regime = np.std(recent_rets) * 10000 if len(recent_rets) > 5 else 0
        
        features = np.array([
            obi, w_obi, spread_bps, vpin, obi_momentum,
            ret_5, ret_20, spread_z, depth_asym, vol_regime
        ])
        
        # Direction: did next candle go up or down?
        next_close = candles[i+1][4]
        direction = 1.0 if next_close > close else -1.0
        
        training_data.append((features, direction))
    
    log.info(f"Extracted {len(training_data)} training samples from candles")
    return training_data


def train_model(training_data, weights=None):
    """Train a fresh model on training data. Returns (w_fast, w_slow, mean, var, hit_rate)."""
    n = N_FEATURES + 1  # +1 bias
    
    if weights is not None:
        w_fast = weights['w_fast'].copy()
        w_slow = weights['w_slow'].copy()
        running_mean = weights['running_mean'].copy()
        running_var = weights['running_var'].copy()
    else:
        w_fast = np.zeros(n)
        w_slow = np.zeros(n)
        running_mean = np.zeros(N_FEATURES)
        running_var = np.ones(N_FEATURES)
    
    hits = 0
    total = 0
    
    for features, direction in training_data:
        target = np.clip(direction, -1.0, 1.0)
        
        # Online normalization
        alpha = 0.01
        diff = features - running_mean
        running_mean += alpha * diff
        diff2 = features - running_mean
        running_var = (1 - alpha) * running_var + alpha * diff2**2
        
        std = np.sqrt(running_var + 1e-8)
        norm_f = (features - running_mean) / std
        
        x = np.append(norm_f, 1.0)
        
        # Predictions
        pred_fast = np.dot(w_fast, x)
        pred_slow = np.dot(w_slow, x)
        
        # Errors
        err_fast = target - pred_fast
        err_slow = target - pred_slow
        
        # Gradient descent
        w_fast += LEARNING_RATE * (err_fast * x - L2_REG * w_fast)
        w_slow += (LEARNING_RATE * 0.2) * (err_slow * x - L2_REG * w_slow)
        
        np.clip(w_fast, -5.0, 5.0, out=w_fast)
        np.clip(w_slow, -5.0, 5.0, out=w_slow)
        
        # Hit tracking
        prediction = 0.6 * pred_fast + 0.4 * pred_slow
        if (prediction > 0 and direction > 0) or (prediction < 0 and direction < 0):
            hits += 1
        total += 1
    
    hit_rate = hits / total if total > 0 else 0.5
    
    return {
        'w_fast': w_fast,
        'w_slow': w_slow,
        'running_mean': running_mean,
        'running_var': running_var,
        'hit_rate': hit_rate,
        'samples': total,
    }


def evaluate_oos(model_weights, oos_data):
    """Run model on out-of-sample data. Returns (hit_rate, avg_pnl_direction)."""
    w_fast = model_weights['w_fast']
    w_slow = model_weights['w_slow']
    running_mean = model_weights['running_mean'].copy()
    running_var = model_weights['running_var'].copy()
    
    hits = 0
    total = 0
    cumulative_pnl = 0.0
    
    for features, direction in oos_data:
        std = np.sqrt(running_var + 1e-8)
        norm_f = (features - running_mean) / std
        x = np.append(norm_f, 1.0)
        
        pred = 0.6 * np.dot(w_fast, x) + 0.4 * np.dot(w_slow, x)
        
        if (pred > 0 and direction > 0) or (pred < 0 and direction < 0):
            hits += 1
            cumulative_pnl += abs(pred)
        else:
            cumulative_pnl -= abs(pred)
        total += 1
    
    hit_rate = hits / total if total > 0 else 0.5
    avg_pnl = cumulative_pnl / total if total > 0 else 0
    
    return hit_rate, avg_pnl


def load_current_model():
    """Load current production model weights."""
    if os.path.exists(MODEL_PATH):
        data = np.load(MODEL_PATH)
        return {
            'w_fast': data['w_fast'],
            'w_slow': data['w_slow'],
            'running_mean': data['running_mean'],
            'running_var': data['running_var'],
        }
    return None


def save_model(weights):
    """Save model weights to disk."""
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    np.savez(MODEL_PATH,
        w_fast=weights['w_fast'],
        w_slow=weights['w_slow'],
        running_mean=weights['running_mean'],
        running_var=weights['running_var'],
    )
    log.info(f"Model saved to {MODEL_PATH}")


MMAP_FILE = "/dev/shm/sniper/ml_weights.bin"
FILE_SIZE = 192

def inject_weights_to_rust(w_fast, w_slow, r_mean, r_var):
    """
    Provede Hot-Swap ML vah za behu bez zablokovani HFT L0 botu.
    """
    os.makedirs(os.path.dirname(MMAP_FILE), exist_ok=True)
    if not os.path.exists(MMAP_FILE):
        with open(MMAP_FILE, "wb") as f:
            f.write(b'\x00' * FILE_SIZE)

    with open(MMAP_FILE, "r+b") as f:
        mm = mmap.mmap(f.fileno(), FILE_SIZE)

        current_version = struct.unpack("<Q", mm[:8])[0]
        new_version = current_version + 1

        floats_format = "<11f 11f 10f 10f"
        packed_floats = struct.pack(floats_format, *w_fast, *w_slow, *r_mean, *r_var)
        
        mm.seek(8)
        mm.write(packed_floats)

        mm.flush()

        packed_version = struct.pack("<Q", new_version)
        mm.seek(0)
        mm.write(packed_version)

        log.info(f"Mozek prepsan. Hot-Swap uspesny. Nova verze: {new_version}")

        mm.close()


def save_history(entry):
    """Append retrain result to history JSON."""
    history = []
    if os.path.exists(HISTORY_PATH):
        try:
            with open(HISTORY_PATH) as f:
                history = json.load(f)
        except Exception:
            pass
    
    history.append(entry)
    # Keep last 90 days
    history = history[-90:]
    
    with open(HISTORY_PATH, 'w') as f:
        json.dump(history, f, indent=2)


def main():
    log.info("═══ NIGHTLY RETRAIN — SIM v2.0 P2-A ═══")
    log.info(f"Time: {datetime.now(timezone.utc).isoformat()}")
    
    # 1. Load data
    candles = load_candles(hours=24)
    if len(candles) < 100:
        log.error(f"Insufficient data: {len(candles)} candles (need 100+). Skipping retrain.")
        return
    
    # 2. Extract features
    all_data = extract_features_from_candles(candles)
    if len(all_data) < 50:
        log.error(f"Insufficient training data: {len(all_data)} samples. Skipping.")
        return
    
    # 3. Split: 75% train (IS), 25% OOS validation (last 6h ≈ 360 candles)
    split_idx = int(len(all_data) * 0.75)
    train_data = all_data[:split_idx]
    oos_data = all_data[split_idx:]
    
    log.info(f"Split: {len(train_data)} train / {len(oos_data)} OOS")
    
    # 4. Load current production model (v_old)
    v_old = load_current_model()
    
    # 5. Train v_new (initialized from v_old if available)
    log.info("Training v_new...")
    v_new = train_model(train_data, weights=v_old)
    log.info(f"v_new train hit_rate: {v_new['hit_rate']:.1%} ({v_new['samples']} samples)")
    
    # 6. Shadow Validation Gate
    log.info("═══ SHADOW VALIDATION GATE ═══")
    
    new_hr, new_pnl = evaluate_oos(v_new, oos_data)
    log.info(f"v_new OOS: hit_rate={new_hr:.1%}, mark_out_pnl={new_pnl:+.4f}")
    
    if v_old:
        old_hr, old_pnl = evaluate_oos(v_old, oos_data)
        log.info(f"v_old OOS: hit_rate={old_hr:.1%}, mark_out_pnl={old_pnl:+.4f}")
        
        # Decision: v_new must be strictly better on BOTH metrics
        swap = (new_hr > old_hr) and (new_pnl > old_pnl)
        
        if swap:
            log.info("✅ v_new WINS — executing hot-swap")
            save_model(v_new)
            inject_weights_to_rust(
                w_fast=v_new['w_fast'].tolist(),
                w_slow=v_new['w_slow'].tolist(),
                r_mean=v_new['running_mean'].tolist(),
                r_var=v_new['running_var'].tolist()
            )
            verdict = "SWAP"
        else:
            log.info("❌ v_new LOSES — discarding (catastrophic forgetting prevention)")
            verdict = "KEEP_OLD"
    else:
        # No old model exists → always save v_new
        log.info("📦 No previous model — saving v_new as baseline")
        save_model(v_new)
        inject_weights_to_rust(
            w_fast=v_new['w_fast'].tolist(),
            w_slow=v_new['w_slow'].tolist(),
            r_mean=v_new['running_mean'].tolist(),
            r_var=v_new['running_var'].tolist()
        )
        old_hr, old_pnl = 0.5, 0.0
        verdict = "INITIAL"
    
    # 7. Record history
    entry = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'train_samples': len(train_data),
        'oos_samples': len(oos_data),
        'v_new_train_hr': round(v_new['hit_rate'], 4),
        'v_new_oos_hr': round(new_hr, 4),
        'v_new_oos_pnl': round(new_pnl, 6),
        'v_old_oos_hr': round(old_hr, 4),
        'v_old_oos_pnl': round(old_pnl, 6),
        'verdict': verdict,
    }
    save_history(entry)
    
    log.info(f"═══ RETRAIN COMPLETE: {verdict} ═══")


if __name__ == '__main__':
    main()
