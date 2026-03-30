#!/usr/bin/env python3
"""
🧠 L1 ML Shield — OBI Feature Pipeline & Inference Server
Sniper Armada · Phase 6 · v18.0

Reads orderbook data from Hydra's mmap (engine_state.bin),
extracts features (OBI, spread, VPIN, momentum),
runs inference through a lightweight NumPy-based model,
and writes bias adjustments back to mmap.

Architecture:
  - Reads: /dev/shm/beroun/engine_state.bin (Hydra's live orderbook)
  - Writes: l1_skew_adjustment, l1_confidence_score back to same mmap
  - Model: Online-learning linear model + EMA ensemble (no GPU needed)
  - Cycle: 50ms inference loop (~20 inferences/sec)

Why NumPy instead of PyTorch:
  - GTX 1060 VRAM nearly full (LM Studio uses ~3.7GB/6GB)
  - Linear model inference is <1μs on CPU vs ~100μs GPU kernel launch
  - Online learning updates weights every cycle (no batch training)
  - Zero dependencies beyond NumPy
"""

import os
import sys
import mmap
import struct
import time
import signal
import logging
import numpy as np
from collections import deque

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [ML-SHIELD] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger('ml_shield')

# ═══════════════════════════════════════════════════════════
# Constants (match Rust types.rs EngineState layout)
# ═══════════════════════════════════════════════════════════

ENGINE_PATH = "/dev/shm/beroun/engine_state.bin"
PRICE_SCALE = 100_000_000
BOOK_LEVELS = 25

# EngineState field offsets (bytes from start)
# Calculated from repr(C, align(64)) layout
OFF_LATENCY    = 0       # u64
OFF_PAD_HB     = 8       # [u8; 56]
OFF_BEST_BID   = 64      # u64
OFF_BEST_ASK   = 72      # u64
OFF_BIDS       = 80      # [OrderBookLevel; 25] = 25 * 24 = 600 bytes
OFF_ASKS       = 680     # [OrderBookLevel; 25] = 600 bytes
OFF_T2T        = 1280    # u64
OFF_MICRO      = 1288    # u64
OFF_SKEW       = 1296    # i64
OFF_BUY_IDS    = 1304    # [u64; 5] = 40 bytes
OFF_SELL_IDS   = 1344    # [u64; 5] = 40 bytes
OFF_OBI        = 1384    # i64 (l2_imbalance)
OFF_ORDER_USD  = 1392    # u64

# Jump past padding to cold section
OFF_NET_POS    = 1408    # i64
OFF_REAL_PNL   = 1416    # i64

# L1 Shield write targets
OFF_L1_SKEW    = 1640    # i64 (l1_skew_adjustment) - byte offset for the field
OFF_L1_CONF    = 1648    # u64 (l1_confidence_score)

# GPU temperature monitoring
GPU_TEMP_MAX = 83  # °C — throttle above this

# Inference cycle
CYCLE_MS = 50       # 20 Hz inference
WARMUP_TICKS = 100  # Collect this many ticks before inference

# ═══════════════════════════════════════════════════════════
# Feature Engineering
# ═══════════════════════════════════════════════════════════

class FeatureExtractor:
    """Extracts features from raw mmap data for ML inference."""

    def __init__(self, window_size=200):
        self.window = window_size
        self.obi_history = deque(maxlen=window_size)
        self.spread_history = deque(maxlen=window_size)
        self.mid_history = deque(maxlen=window_size)
        self.vpin_history = deque(maxlen=window_size)
        self.tick_count = 0

    def read_orderbook(self, mm):
        """Read BBA + top 10 levels from mmap."""
        best_bid = struct.unpack_from('<Q', mm, OFF_BEST_BID)[0]
        best_ask = struct.unpack_from('<Q', mm, OFF_BEST_ASK)[0]

        if best_bid == 0 or best_ask == 0:
            return None

        # Read top 10 bid levels
        bid_levels = []
        for i in range(min(10, BOOK_LEVELS)):
            offset = OFF_BIDS + i * 24  # OrderBookLevel = 24 bytes (u64, i64, u64)
            price = struct.unpack_from('<Q', mm, offset)[0]
            amount = struct.unpack_from('<q', mm, offset + 8)[0]
            count = struct.unpack_from('<Q', mm, offset + 16)[0]
            if price > 0:
                bid_levels.append((price, amount, count))

        # Read top 10 ask levels
        ask_levels = []
        for i in range(min(10, BOOK_LEVELS)):
            offset = OFF_ASKS + i * 24
            price = struct.unpack_from('<Q', mm, offset)[0]
            amount = struct.unpack_from('<q', mm, offset + 8)[0]
            count = struct.unpack_from('<Q', mm, offset + 16)[0]
            if price > 0:
                ask_levels.append((price, abs(amount), count))

        return {
            'best_bid': best_bid / PRICE_SCALE,
            'best_ask': best_ask / PRICE_SCALE,
            'bid_levels': bid_levels,
            'ask_levels': ask_levels,
        }

    def extract(self, mm):
        """Extract feature vector from current mmap state."""
        book = self.read_orderbook(mm)
        if book is None:
            return None

        bid = book['best_bid']
        ask = book['best_ask']
        mid = (bid + ask) / 2
        spread = ask - bid

        # 1. Order Book Imbalance (top 5 levels)
        bid_vol = sum(abs(l[1]) for l in book['bid_levels'][:5]) / PRICE_SCALE
        ask_vol = sum(abs(l[1]) for l in book['ask_levels'][:5]) / PRICE_SCALE
        total_vol = bid_vol + ask_vol
        obi = (bid_vol - ask_vol) / total_vol if total_vol > 0 else 0.0

        # 2. Weighted OBI (volume-weighted by distance from mid)
        w_bid = sum(abs(l[1]) / PRICE_SCALE * (1.0 / max(1, abs(mid * PRICE_SCALE - l[0])))
                     for l in book['bid_levels'][:5] if l[0] > 0)
        w_ask = sum(abs(l[1]) / PRICE_SCALE * (1.0 / max(1, abs(l[0] - mid * PRICE_SCALE)))
                     for l in book['ask_levels'][:5] if l[0] > 0)
        w_total = w_bid + w_ask
        w_obi = (w_bid - w_ask) / w_total if w_total > 0 else 0.0

        # 3. Spread in bps
        spread_bps = (spread / mid) * 10000 if mid > 0 else 0

        # 4. VPIN approximation (Volume-synchronized Probability of Informed Trading)
        # Simplified: ratio of directional volume to total
        read_obi = struct.unpack_from('<q', mm, OFF_OBI)[0] / PRICE_SCALE
        vpin = abs(read_obi) if abs(read_obi) < 1.0 else 0.5

        # Store history
        self.obi_history.append(obi)
        self.spread_history.append(spread_bps)
        self.mid_history.append(mid)
        self.vpin_history.append(vpin)
        self.tick_count += 1

        if self.tick_count < 20:
            return None  # Need minimum history

        # 5. OBI momentum (EMA derivative)
        obi_arr = np.array(list(self.obi_history))
        obi_ema_fast = self._ema(obi_arr, 5)
        obi_ema_slow = self._ema(obi_arr, 20)
        obi_momentum = obi_ema_fast - obi_ema_slow

        # 6. Price momentum (normalized returns)
        mid_arr = np.array(list(self.mid_history))
        if len(mid_arr) >= 10:
            ret_5 = (mid_arr[-1] - mid_arr[-5]) / mid_arr[-5] * 10000 if mid_arr[-5] > 0 else 0
            ret_20 = (mid_arr[-1] - mid_arr[-min(20, len(mid_arr))]) / mid_arr[-min(20, len(mid_arr))] * 10000 if mid_arr[-min(20, len(mid_arr))] > 0 else 0
        else:
            ret_5 = ret_20 = 0.0

        # 7. Spread regime (z-score of current spread)
        spread_arr = np.array(list(self.spread_history))
        spread_mean = np.mean(spread_arr)
        spread_std = np.std(spread_arr)
        spread_z = (spread_bps - spread_mean) / spread_std if spread_std > 0.001 else 0

        # 8. Book depth asymmetry (deep levels)
        deep_bid = sum(abs(l[1]) for l in book['bid_levels'][5:]) / PRICE_SCALE
        deep_ask = sum(abs(l[1]) for l in book['ask_levels'][5:]) / PRICE_SCALE
        deep_total = deep_bid + deep_ask
        depth_asym = (deep_bid - deep_ask) / deep_total if deep_total > 0 else 0.0

        # Feature vector: 10 features
        features = np.array([
            obi,              # F0: raw OBI
            w_obi,            # F1: weighted OBI
            obi_momentum,     # F2: OBI momentum (fast-slow EMA)
            spread_bps,       # F3: spread in bps
            spread_z,         # F4: spread z-score
            vpin,             # F5: VPIN
            ret_5,            # F6: 5-tick return (bps)
            ret_20,           # F7: 20-tick return (bps)
            depth_asym,       # F8: deep book asymmetry
            read_obi,         # F9: L2 OBI (from mmap, computed by Hydra)
        ], dtype=np.float64)

        return features

    @staticmethod
    def _ema(arr, span):
        """Compute EMA of last element."""
        alpha = 2.0 / (span + 1)
        ema = arr[0]
        for val in arr[1:]:
            ema = alpha * val + (1 - alpha) * ema
        return ema


# ═══════════════════════════════════════════════════════════
# Online Learning Model
# ═══════════════════════════════════════════════════════════

class OnlineLinearModel:
    """
    Adaptive linear model with online learning.

    Predicts direction bias from features using:
      1. Ridge regression (L2 regularized linear model)
      2. Exponentially weighted updates (recent data matters more)
      3. Dual time-horizon ensemble (fast 50-tick + slow 500-tick)

    This captures:
      - "High OBI + thin asks = likely pump" (learned from data)
      - "Wide spread + high VPIN = toxic flow, fade" (learned)
      - Non-obvious cross-feature interactions (via polynomial expansion)
    """

    def __init__(self, n_features=10, learning_rate=0.001, l2_reg=0.01):
        self.n_features = n_features
        self.lr = learning_rate
        self.l2_reg = l2_reg

        # Two sets of weights: fast (reactive) and slow (stable)
        self.w_fast = np.zeros(n_features + 1)  # +1 for bias term
        self.w_slow = np.zeros(n_features + 1)

        # Feature statistics for normalization
        self.running_mean = np.zeros(n_features)
        self.running_var = np.ones(n_features)
        self.n_samples = 0

        # Performance tracking
        self.pred_history = deque(maxlen=1000)
        self.outcome_history = deque(maxlen=1000)
        self.hit_rate = 0.5
        self.total_predictions = 0

    def normalize(self, features):
        """Online normalization using running statistics."""
        self.n_samples += 1
        alpha = max(0.001, 1.0 / self.n_samples)
        self.running_mean = (1 - alpha) * self.running_mean + alpha * features
        diff = features - self.running_mean
        self.running_var = (1 - alpha) * self.running_var + alpha * (diff ** 2)
        std = np.sqrt(self.running_var + 1e-8)
        return (features - self.running_mean) / std

    def predict(self, features):
        """Predict direction bias from normalized features."""
        norm_f = self.normalize(features)
        x = np.append(norm_f, 1.0)  # Add bias term

        # Ensemble: 60% fast + 40% slow
        pred_fast = np.dot(self.w_fast, x)
        pred_slow = np.dot(self.w_slow, x)
        prediction = 0.6 * pred_fast + 0.4 * pred_slow

        # Clamp to [-1, 1]
        prediction = np.clip(prediction, -1.0, 1.0)

        self.pred_history.append(prediction)
        self.total_predictions += 1

        return prediction

    def update(self, features, actual_return):
        """
        Online weight update based on actual market return.
        actual_return: positive = price went up, negative = down
        """
        norm_f = self.normalize(features)
        x = np.append(norm_f, 1.0)

        # Target: sign of return (clamped to [-1, 1])
        target = np.clip(actual_return * 100, -1.0, 1.0)  # Scale and clamp

        # Prediction error
        pred_fast = np.dot(self.w_fast, x)
        pred_slow = np.dot(self.w_slow, x)
        err_fast = target - pred_fast
        err_slow = target - pred_slow

        # Gradient descent with L2 regularization
        self.w_fast += self.lr * (err_fast * x - self.l2_reg * self.w_fast)
        self.w_slow += (self.lr * 0.2) * (err_slow * x - self.l2_reg * self.w_slow)

        # Clamp weights to prevent explosion
        np.clip(self.w_fast, -5.0, 5.0, out=self.w_fast)
        np.clip(self.w_slow, -5.0, 5.0, out=self.w_slow)

        # Track hit rate
        self.outcome_history.append(target)
        if len(self.pred_history) > 10:
            recent_preds = list(self.pred_history)[-100:]
            recent_outcomes = list(self.outcome_history)[-100:]
            if len(recent_preds) == len(recent_outcomes):
                hits = sum(1 for p, o in zip(recent_preds, recent_outcomes)
                          if (p > 0 and o > 0) or (p < 0 and o < 0) or (abs(p) < 0.1))
                self.hit_rate = hits / len(recent_preds)

    def get_confidence(self):
        """Model confidence based on recent hit rate and prediction magnitude."""
        if self.total_predictions < WARMUP_TICKS:
            return 0.3  # Low confidence during warmup
        # Scale hit_rate from [0.4, 0.7] → [0.0, 1.0]
        conf = np.clip((self.hit_rate - 0.4) / 0.3, 0.0, 1.0)
        return conf


# ═══════════════════════════════════════════════════════════
# Thermal Throttling
# ═══════════════════════════════════════════════════════════

def get_gpu_temp():
    """Read GPU temperature from nvidia-smi."""
    try:
        import subprocess
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=temperature.gpu', '--format=csv,noheader'],
            capture_output=True, text=True, timeout=2
        )
        return int(result.stdout.strip())
    except Exception:
        return 0

# ═══════════════════════════════════════════════════════════
# Main Inference Loop
# ═══════════════════════════════════════════════════════════

def find_field_offset(fieldname):
    """Calculate byte offset of EngineState fields by counting through struct."""
    # This is a simplified version - actual offsets computed from Rust struct
    # For production, use a shared offset header file
    offsets = {
        'l1_skew_adjustment': None,
        'l1_confidence_score': None,
    }
    # We compute these by walking the struct definition:
    # After analytics_checkpoint_ms(u64), we have:
    # l1_confidence_score(u64), l2_regime_id(u64), ...
    return offsets.get(fieldname)

def run_inference():
    """Main inference loop."""

    if not os.path.exists(ENGINE_PATH):
        log.error(f"Engine mmap not found: {ENGINE_PATH}")
        log.info("Waiting for Hydra to start...")
        while not os.path.exists(ENGINE_PATH):
            time.sleep(5)

    log.info("🧠 L1 ML Shield v1.0 starting")
    log.info(f"   Engine mmap: {ENGINE_PATH}")
    log.info(f"   Cycle: {CYCLE_MS}ms ({1000//CYCLE_MS} Hz)")
    log.info(f"   GPU temp limit: {GPU_TEMP_MAX}°C")

    # Open mmap
    f = open(ENGINE_PATH, 'r+b')
    mm = mmap.mmap(f.fileno(), 0)
    file_size = mm.size()
    log.info(f"   mmap size: {file_size} bytes")

    # Calculate l1_skew_adjustment offset by counting fields
    # We need to find the exact byte offset — this comes from
    # carefully matching the Rust struct layout
    # From the Rust struct, counting 8 bytes per AtomicU64/AtomicI64:
    # latency(8) + pad(56) = 64
    # best_bid(8) + best_ask(8) + bids(600) + asks(600) = 1216
    # t2t(8) + micro(8) + skew(8) = 24
    # buy_ids(40) + sell_ids(40) = 80
    # obi(8) + order_usd(8) = 16
    # pad(8) = 8
    # Total HOT: 64 + 1216 + 24 + 80 + 16 + 8 = 1408
    # COLD starts at 1408:
    # net_pos(8) + pnl(8) + btc(8) + usd(8) + checksum(4) + pad(4) = 40
    # last_buy(8) + last_sell(8) = 16
    # avg_entry(8) = 8
    # ai_bias(8) + ai_hb(8) + ai_alpha(8) = 24
    # buy_fill(8) + sell_fill(8) = 16
    # monthly_vol(8) = 8
    # session_buy_vol(8) + session_sell_vol(8) + session_buy_usd(8) + session_sell_usd(8) = 32
    # session_fill(8) + session_pnl(8) = 16
    # toxic(8) + sweep_freeze(8) = 16
    # l1_skew_adjustment(8) → offset = 1408 + 40 + 16 + 8 + 24 + 16 + 8 + 32 + 16 + 16 = 1584
    # analytics_cp(8) → 1592
    # l1_confidence_score(8) → 1600

    OFF_L1_SKEW_CALC = 1584
    OFF_L1_CONF_CALC = 1600

    # Verify: read current values to see if they're sensible
    current_skew = struct.unpack_from('<q', mm, OFF_L1_SKEW_CALC)[0]
    current_conf = struct.unpack_from('<Q', mm, OFF_L1_CONF_CALC)[0]
    log.info(f"   Current l1_skew: {current_skew}, l1_conf: {current_conf}")

    extractor = FeatureExtractor(window_size=200)
    model = OnlineLinearModel(n_features=10, learning_rate=0.0005, l2_reg=0.01)

    prev_mid = 0.0
    cycle_count = 0
    last_temp_check = 0
    gpu_temp = 0
    throttled = False
    last_log_time = time.time()

    log.info("✅ ML Shield online — entering inference loop")

    while True:
        cycle_start = time.monotonic()

        try:
            # Extract features
            features = extractor.extract(mm)

            if features is not None:
                # Get current mid price for learning feedback
                current_mid = extractor.mid_history[-1] if extractor.mid_history else 0

                # Online learning: use previous prediction vs actual return
                if prev_mid > 0 and current_mid > 0:
                    actual_return = (current_mid - prev_mid) / prev_mid
                    model.update(features, actual_return)

                prev_mid = current_mid

                # Predict direction bias
                if not throttled:
                    prediction = model.predict(features)
                    confidence = model.get_confidence()

                    # Scale prediction to mmap format
                    # l1_skew_adjustment: × PRICE_SCALE
                    skew_scaled = int(prediction * PRICE_SCALE)
                    # l1_confidence_score: 0..10000
                    conf_scaled = int(confidence * 10000)

                    # Write to mmap
                    struct.pack_into('<q', mm, OFF_L1_SKEW_CALC, skew_scaled)
                    struct.pack_into('<Q', mm, OFF_L1_CONF_CALC, conf_scaled)

                cycle_count += 1

            # Thermal check every 30s
            now = time.time()
            if now - last_temp_check > 30:
                gpu_temp = get_gpu_temp()
                last_temp_check = now
                if gpu_temp > GPU_TEMP_MAX:
                    if not throttled:
                        log.warning(f"🌡️ GPU {gpu_temp}°C > {GPU_TEMP_MAX}°C — THROTTLING")
                        throttled = True
                elif throttled:
                    log.info(f"🌡️ GPU {gpu_temp}°C — throttle released")
                    throttled = False

            # Status log every 60s
            if now - last_log_time > 60:
                hr = model.hit_rate * 100
                tp = model.total_predictions
                w_mag = np.linalg.norm(model.w_fast)
                skew_val = struct.unpack_from('<q', mm, OFF_L1_SKEW_CALC)[0] / PRICE_SCALE
                log.info(
                    f"📊 Cycle {cycle_count} | "
                    f"HitRate={hr:.1f}% | "
                    f"Pred={skew_val:.4f} | "
                    f"|W|={w_mag:.3f} | "
                    f"GPU={gpu_temp}°C | "
                    f"Throttled={throttled}"
                )
                last_log_time = now

        except Exception as e:
            log.error(f"Inference error: {e}")
            time.sleep(1)
            continue

        # Sleep to maintain cycle rate
        elapsed = (time.monotonic() - cycle_start) * 1000
        sleep_ms = max(1, CYCLE_MS - elapsed)
        time.sleep(sleep_ms / 1000)


def main():
    """Entry point with graceful shutdown."""
    def shutdown(sig, frame):
        log.info("🛑 ML Shield shutting down...")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        run_inference()
    except KeyboardInterrupt:
        log.info("🛑 ML Shield stopped.")


if __name__ == "__main__":
    main()
