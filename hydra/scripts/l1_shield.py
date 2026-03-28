#!/usr/bin/env python3
"""
🛡️ BEROUN L1 SHIELD v13.0 — Sovereign Intelligence
═══════════════════════════════════════════════════════════════
Cross-Layer Intelligence: L1 reads orderbook, writes skew + freeze + confidence
  - OBI Micro-Skewing: shifts grid bias based on order book imbalance
  - Sweep Protection: detects large sweeps and freezes execution
  - Adaptive Learning: tracks false positive rate and self-calibrates
  - Confidence Scoring: writes l1_confidence_score for L2 Oracle
  - L1→L2 JSON Bridge: exports state to /dev/shm/beroun/l1_state.json

Latency: <1ms cycle (reads atomics from /dev/shm/beroun/)
Communication: mmap atomics (zero-copy) + JSON bridge for L2
═══════════════════════════════════════════════════════════════
"""

import mmap
import struct
import time
import sys
import os
import json
import signal
import logging
from collections import deque

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_DIR = os.path.dirname(SCRIPT_DIR)
PROJECT_ROOT = os.path.dirname(BOT_DIR)
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [L1] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(LOG_DIR, 'l1_shield.log'))
    ]
)
log = logging.getLogger('L1')

# ═══ MMAP OFFSETS — Verified from repr(C) layout ═══
ENGINE_MMAP = "/dev/shm/beroun/engine_state.bin"
L1_STATE_JSON = "/dev/shm/beroun/l1_state.json"
PRICE_SCALE = 1e8

OFF_BEST_BID = 64
OFF_BEST_ASK = 72
OFF_BIDS = 80         # bids[25], each 24 bytes = 600 bytes total
OFF_ASKS = 680        # asks[25], each 24 bytes = 600 bytes total
OFF_MICRO = 1288      # micro_price
OFF_OBI = 1384        # l2_imbalance
OFF_NET_POS = 1408    # net_position (after _pad_hot_cold)
OFF_REALIZED_PNL = 1416  # realized_pnl
OFF_TOXIC = 1568      # toxic_flow_hits
OFF_SWEEP_FREEZE = 1576  # sweep_freeze_until
OFF_L1_SKEW = 1584    # l1_skew_adjustment
OFF_ANALYTICS_CK = 1592  # analytics_checkpoint_ms

# v10.4 Neural Cross: Cross-Layer AI offsets
OFF_L1_CONFIDENCE = 1600   # l1_confidence_score (u64, 0-10000)
OFF_L2_REGIME = 1608       # l2_regime_id
OFF_SHADOW_MODE = 1616     # is_shadow_mode
OFF_SHADOW_PNL = 1624      # shadow_pnl
OFF_L1_FP_RATE = 1632      # l1_false_positive_rate
OFF_L1_SUCCESS = 1640      # l1_sweep_success_rate
OFF_L2_ACTION_MS = 1648    # l2_last_action_ms
OFF_LEARNING_TRIG = 1656   # ai_learning_trigger

# v10.5 Resurrection: Anti-Paralysis
OFF_L1_UPTIME_PCT = 1664   # l1_uptime_pct (u64, 0-10000 = 0%-100%)

# v10.6 Ghost Orders: Hidden Liquidity
OFF_GHOST_TRANSPARENCY = 1672  # ghost_transparency (u64, 0-10000)
OFF_GHOST_ACTIVE_MASK = 1680   # ghost_active_mask (u64 bitmask)
OFF_GHOST_INJECTIONS = 1688    # ghost_injections (u64)
OFF_GHOST_VELOCITY_REJECTS = 1696  # ghost_velocity_rejects (u64)
# ghost_buy_prices[5] = 1704..1743 (5 × i64)
# ghost_sell_prices[5] = 1744..1783 (5 × i64)

# v10.7 Sovereign AI Control
OFF_AI_FREEZE_MS = 1784        # ai_freeze_ms (u64, default 4000)
OFF_AI_FIRE_INTERVAL = 1792    # ai_fire_interval_ms (u64, default 3000)
OFF_AI_GHOST_TRIGGER = 1800    # ai_ghost_trigger_pct (u64, ×100000, default 50)
OFF_AI_INTENT = 1808           # ai_intent (0=sovereign, 1=aggressive, 2=defensive, 3=scout)
OFF_AI_REGISTRY_VER = 1816     # ai_registry_version (u64)

OBL_SIZE = 24         # sizeof(OrderBookLevel) = 3 × 8 bytes

# ═══ CONFIGURATION ═══
CYCLE_MS = 50           # 50ms cycle = 20 updates/sec
OBI_SKEW_FACTOR = 0.3   # How aggressively to skew based on OBI
MAX_SKEW_USD = 3.0      # Maximum skew in USD
SWEEP_VOL_DROP_PCT = 0.85  # 85% depth volume drop = genuine sweep (v10.5: raised from 70%)
SWEEP_FREEZE_MS = 4000     # 4 second freeze after sweep (v10.5: reduced from 15s)
SWEEP_DEBOUNCE_MS = 500    # Ignore sweeps within 500ms of last one
BOOK_DEPTH = 10            # Read top 10 of 25 levels

# ═══ ADAPTIVE LEARNING ═══
CONFIDENCE_WINDOW = 100       # Last N sweep events for confidence calc
ADAPTATION_INTERVAL = 600     # Every 10 minutes, adapt thresholds
MIN_SWEEP_THRESHOLD = 0.60    # Minimum sweep detection threshold (v10.5: raised from 0.40)
MAX_SWEEP_THRESHOLD = 0.90    # Maximum sweep detection threshold

# v10.5 Anti-Paralysis
PARALYSIS_FREEZE_RATIO = 0.60      # If frozen >60% of time → force desensitize
PARALYSIS_CHECK_WINDOW = 300       # 5 minute window for uptime check
PARALYSIS_DESENSITIZE_STEP = 0.05  # Raise threshold by 0.05 per check
MAX_CONSECUTIVE_FREEZES = 8        # After 8 consecutive freezes, force raise threshold

# v10.6 Ghost Orders thresholds
GHOST_TOXIC_ACTIVATE = 300         # Activate ghost when toxic > 300
GHOST_OBI_ACTIVATE = -0.85         # Activate ghost when OBI < -0.85
GHOST_CALM_DEACTIVATE = 50         # Deactivate ghost when toxic < 50
GHOST_TRANSPARENCY_STEALTH = 1000  # 10% = only tip visible
GHOST_TRANSPARENCY_PUBLIC = 10000  # 100% = full public mode
GHOST_COOLDOWN_SEC = 120           # Min seconds between ghost state changes

running = True

def signal_handler(sig, frame):
    global running
    log.info("Shutting down L1 Shield...")
    running = False

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def read_u64(mm, offset):
    return struct.unpack_from('<Q', mm, offset)[0]

def read_i64(mm, offset):
    return struct.unpack_from('<q', mm, offset)[0]

def write_u64(mm, offset, val):
    struct.pack_into('<Q', mm, offset, int(val))

def write_i64(mm, offset, val):
    struct.pack_into('<q', mm, offset, int(val))


def read_orderbook_levels(mm, base_offset, n=10):
    """Read n OrderBookLevel from mmap. Each level is OBL_SIZE (24) bytes."""
    levels = []
    for i in range(n):
        off = base_offset + i * OBL_SIZE
        price = read_u64(mm, off) / PRICE_SCALE
        amount = read_i64(mm, off + 8) / PRICE_SCALE
        if price > 0:
            levels.append((price, amount))
    return levels


def compute_obi(bids, asks, depth=3):
    """Order Book Imbalance: (bid_vol - ask_vol) / (bid_vol + ask_vol)."""
    bid_vol = sum(abs(a) for _, a in bids[:depth])
    ask_vol = sum(abs(a) for _, a in asks[:depth])
    total = bid_vol + ask_vol
    if total < 1e-10:
        return 0.0
    return (bid_vol - ask_vol) / total


def detect_sweep(prev_levels, curr_levels, threshold):
    """Detect sweep by comparing total depth volume drop between cycles."""
    if not prev_levels or not curr_levels:
        return False
    prev_vol = sum(abs(a) for _, a in prev_levels)
    curr_vol = sum(abs(a) for _, a in curr_levels)
    if prev_vol < 0.001:
        return False
    drop = (prev_vol - curr_vol) / prev_vol
    return drop > threshold


def detect_flickering(price_history, window=20):
    """Detect bid/ask flickering (rapid oscillations without fills)."""
    if len(price_history) < window:
        return False, 0
    recent = list(price_history)[-window:]
    changes = sum(1 for i in range(1, len(recent)) if recent[i] != recent[i-1])
    flicker_rate = changes / window
    return flicker_rate > 0.7, flicker_rate


def compute_iceberg_score(levels):
    """Detect potential iceberg orders: small visible quantity at same price."""
    if len(levels) < 3:
        return 0.0
    # Count levels with same price but small visible amount
    price_counts = {}
    for price, amount in levels:
        p = round(price, 2)
        if p not in price_counts:
            price_counts[p] = 0
        price_counts[p] += 1
    repeated = sum(1 for c in price_counts.values() if c > 1)
    return min(1.0, repeated / 3.0)


class AdaptiveL1Brain:
    """Self-learning L1 Shield with confidence tracking + anti-paralysis."""

    def __init__(self):
        self.sweep_threshold = SWEEP_VOL_DROP_PCT
        self.sweep_events = deque(maxlen=CONFIDENCE_WINDOW)
        self.false_positives = 0
        self.true_positives = 0
        self.total_sweeps = 0
        self.last_adaptation = time.time()
        self.obi_history = deque(maxlen=120)
        self.bid_price_history = deque(maxlen=50)
        self.ask_price_history = deque(maxlen=50)
        self.depth_history = deque(maxlen=120)
        self.pre_sweep_signatures = []  # Store market "fingerprints" before sweeps
        # v10.5 Anti-Paralysis: Uptime Tracker
        self.freeze_time_ms = 0        # Total ms spent in freeze
        self.active_time_ms = 0        # Total ms spent active
        self.uptime_window_start = time.time()
        self.consecutive_freezes = 0   # Count consecutive freeze detections
        self.last_paralysis_fix = 0    # Last time we force-desensitized
        # v10.6 Ghost Mode
        self.ghost_active = False
        self.ghost_last_change = 0     # Timestamp of last ghost state change
        self.ghost_session_injections = 0

    def record_sweep(self, side, pnl_before, pnl_after, obi, depth):
        """Record a sweep event for learning."""
        # If PnL improved after freeze, it was a true positive
        pnl_delta = pnl_after - pnl_before
        was_helpful = pnl_delta >= 0  # Freeze prevented loss

        event = {
            "time": time.time(),
            "side": side,
            "helpful": was_helpful,
            "obi": obi,
            "depth": depth,
        }
        self.sweep_events.append(event)
        self.total_sweeps += 1

        if was_helpful:
            self.true_positives += 1
        else:
            self.false_positives += 1

        # Store pre-sweep signature for L2 analysis
        if len(self.obi_history) >= 5:
            signature = {
                "obi_trend": list(self.obi_history)[-5:],
                "depth_trend": list(self.depth_history)[-5:],
                "side": side,
                "timestamp": time.time(),
            }
            self.pre_sweep_signatures.append(signature)
            # Keep last 50 signatures
            if len(self.pre_sweep_signatures) > 50:
                self.pre_sweep_signatures = self.pre_sweep_signatures[-50:]

    def adapt_threshold(self):
        """Adapt sweep detection threshold based on success rate + anti-paralysis."""
        if time.time() - self.last_adaptation < ADAPTATION_INTERVAL:
            return

        self.last_adaptation = time.time()

        # ═══ v10.5 ANTI-PARALYSIS CHECK (runs BEFORE normal adaptation) ═══
        uptime_pct = self.get_uptime_pct()
        if uptime_pct < (1.0 - PARALYSIS_FREEZE_RATIO):  # Frozen >60% of time
            old_threshold = self.sweep_threshold
            self.sweep_threshold = min(MAX_SWEEP_THRESHOLD,
                                       self.sweep_threshold + PARALYSIS_DESENSITIZE_STEP)
            log.warning(f"🆘 L1 ANTI-PARALYSIS: Uptime {uptime_pct:.0%} too low! "
                        f"Threshold {old_threshold:.2f}→{self.sweep_threshold:.2f} "
                        f"(force desensitize, consecutive={self.consecutive_freezes})")
            self.last_paralysis_fix = time.time()
            # Reset uptime window
            self.freeze_time_ms = 0
            self.active_time_ms = 0
            self.uptime_window_start = time.time()
            self.consecutive_freezes = 0
            # Reset counters to prevent normal adaptation from fighting us
            self.true_positives = 1
            self.false_positives = 1
            return

        total = self.true_positives + self.false_positives
        if total < 5:
            return

        success_rate = self.true_positives / total
        fp_rate = self.false_positives / total

        old_threshold = self.sweep_threshold

        if fp_rate > 0.5:
            # Too many false positives → increase threshold (less sensitive)
            self.sweep_threshold = min(MAX_SWEEP_THRESHOLD,
                                       self.sweep_threshold + 0.05)
            log.info(f"🧠 L1 ADAPT: FP rate {fp_rate:.0%} too high. "
                     f"Threshold {old_threshold:.2f}→{self.sweep_threshold:.2f} "
                     f"(less sensitive)")
        elif fp_rate < 0.1 and success_rate > 0.8:
            # v10.5: Only allow sensitization if uptime is healthy (>80%)
            if uptime_pct > 0.8:
                self.sweep_threshold = max(MIN_SWEEP_THRESHOLD,
                                           self.sweep_threshold - 0.03)
                log.info(f"🧠 L1 ADAPT: Success rate {success_rate:.0%} excellent. "
                         f"Threshold {old_threshold:.2f}→{self.sweep_threshold:.2f} "
                         f"(more sensitive)")
            else:
                log.info(f"🧠 L1 ADAPT: Success rate {success_rate:.0%} but "
                         f"uptime {uptime_pct:.0%} — NOT sensitizing (anti-paralysis)")

        # Reset counters for next window
        self.true_positives = max(1, self.true_positives // 2)
        self.false_positives = max(0, self.false_positives // 2)

    def record_freeze_time(self, freeze_ms):
        """v10.5: Track time spent in freeze for uptime calculation."""
        self.freeze_time_ms += freeze_ms
        # Reset window every PARALYSIS_CHECK_WINDOW
        elapsed = time.time() - self.uptime_window_start
        if elapsed > PARALYSIS_CHECK_WINDOW:
            self.freeze_time_ms = 0
            self.active_time_ms = 0
            self.uptime_window_start = time.time()

    def record_active_time(self, active_ms):
        """v10.5: Track active (non-frozen) time."""
        self.active_time_ms += active_ms
        self.consecutive_freezes = 0  # Reset on active cycle

    def record_consecutive_freeze(self):
        """v10.5: Track consecutive freeze events for force-desensitization."""
        self.consecutive_freezes += 1
        if self.consecutive_freezes >= MAX_CONSECUTIVE_FREEZES:
            old = self.sweep_threshold
            self.sweep_threshold = min(MAX_SWEEP_THRESHOLD,
                                       self.sweep_threshold + PARALYSIS_DESENSITIZE_STEP)
            log.warning(f"🆘 L1 CONSECUTIVE FREEZE #{self.consecutive_freezes}: "
                        f"Threshold {old:.2f}→{self.sweep_threshold:.2f} (force raise)")
            self.consecutive_freezes = 0

    def get_uptime_pct(self):
        """v10.5: Calculate trading uptime percentage."""
        total = self.freeze_time_ms + self.active_time_ms
        if total < 1000:  # Not enough data yet
            return 1.0
        return self.active_time_ms / total

    def evaluate_ghost_mode(self, toxic_hits, obi, l2_regime):
        """v10.6: Decide whether to activate/deactivate Ghost Mode.
        Returns (transparency_value, changed, reason)."""
        now = time.time()
        if now - self.ghost_last_change < GHOST_COOLDOWN_SEC:
            return None, False, ""

        if not self.ghost_active:
            # ACTIVATE ghost if market is toxic
            if toxic_hits > GHOST_TOXIC_ACTIVATE:
                self.ghost_active = True
                self.ghost_last_change = now
                return GHOST_TRANSPARENCY_STEALTH, True, f"Toxic={toxic_hits} > {GHOST_TOXIC_ACTIVATE}"
            if obi < GHOST_OBI_ACTIVATE:
                self.ghost_active = True
                self.ghost_last_change = now
                return GHOST_TRANSPARENCY_STEALTH, True, f"OBI={obi:.3f} < {GHOST_OBI_ACTIVATE}"
        else:
            # DEACTIVATE ghost if market calms down (and regime is RANGING)
            if toxic_hits < GHOST_CALM_DEACTIVATE and l2_regime == 2:  # 2 = RANGING
                self.ghost_active = False
                self.ghost_last_change = now
                return GHOST_TRANSPARENCY_PUBLIC, True, f"Market calm (toxic={toxic_hits}, RANGING)"

        return None, False, ""

    def compute_confidence(self, obi, flicker_rate, iceberg_score, depth_ratio):
        """
        Compute L1 confidence score (0.0 - 1.0).
        Higher = more certain about market toxicity.
        """
        confidence = 0.0

        # Strong OBI = high confidence in directional pressure
        confidence += min(0.3, abs(obi) * 0.3)

        # Flickering = market manipulation signal
        confidence += min(0.25, flicker_rate * 0.25)

        # Iceberg detection
        confidence += iceberg_score * 0.2

        # Low depth ratio = thin book = dangerous
        if depth_ratio < 0.5:
            confidence += 0.25 * (1.0 - depth_ratio * 2)

        return min(1.0, max(0.0, confidence))

    def export_l1_state(self, obi, confidence, depth_btc):
        """Export L1 state as JSON for L2 Oracle consumption."""
        total = self.true_positives + self.false_positives
        fp_rate = self.false_positives / max(1, total)
        success_rate = self.true_positives / max(1, total)

        state = {
            "timestamp": time.time(),
            "obi": round(obi, 4),
            "confidence": round(confidence, 4),
            "sweep_threshold": round(self.sweep_threshold, 3),
            "total_sweeps": self.total_sweeps,
            "false_positive_rate": round(fp_rate, 4),
            "success_rate": round(success_rate, 4),
            "depth_btc": round(depth_btc, 6),
            "pre_sweep_signatures": self.pre_sweep_signatures[-5:],
            "adaptation_status": {
                "true_positives": self.true_positives,
                "false_positives": self.false_positives,
                "threshold": self.sweep_threshold,
            }
        }

        try:
            tmp = L1_STATE_JSON + ".tmp"
            with open(tmp, 'w') as f:
                json.dump(state, f)
            os.replace(tmp, L1_STATE_JSON)
        except Exception as e:
            log.error(f"L1 state export failed: {e}")

        return fp_rate, success_rate


def main():
    global running

    log.info("═══ L1 SHIELD v13.0 — SOVEREIGN INTELLIGENCE STARTING ═══")
    log.info(f"  mmap: {ENGINE_MMAP}")
    log.info(f"  Cycle: {CYCLE_MS}ms | Skew factor: {OBI_SKEW_FACTOR}")
    log.info(f"  Max skew: ${MAX_SKEW_USD} | Sweep freeze: {SWEEP_FREEZE_MS}ms")
    log.info(f"  Adaptive learning: ON (window={CONFIDENCE_WINDOW})")

    if not os.path.exists(ENGINE_MMAP):
        log.error(f"Engine mmap not found: {ENGINE_MMAP}")
        sys.exit(1)

    fd = os.open(ENGINE_MMAP, os.O_RDWR)
    mm = mmap.mmap(fd, 0, access=mmap.ACCESS_WRITE)

    brain = AdaptiveL1Brain()
    prev_bids = []
    prev_asks = []
    last_sweep_ms = 0
    cycle = 0
    pnl_at_sweep = 0  # Track PnL at time of sweep for learning

    log.info("═══ L1 SHIELD v13.0 ACTIVE ═══")

    while running:
        try:
            t0 = time.monotonic()

            # ── READ ORDERBOOK ──
            bids = read_orderbook_levels(mm, OFF_BIDS, BOOK_DEPTH)
            asks = read_orderbook_levels(mm, OFF_ASKS, BOOK_DEPTH)

            # ── OBI MICRO-SKEWING ──
            obi = compute_obi(bids, asks, depth=3)
            brain.obi_history.append(obi)

            skew_usd = obi * OBI_SKEW_FACTOR * MAX_SKEW_USD
            skew_usd = max(-MAX_SKEW_USD, min(MAX_SKEW_USD, skew_usd))
            skew_scaled = int(skew_usd * PRICE_SCALE)
            write_i64(mm, OFF_L1_SKEW, skew_scaled)

            # ── DEPTH TRACKING ──
            bid_depth = sum(abs(a) for _, a in bids)
            ask_depth = sum(abs(a) for _, a in asks)
            total_depth = bid_depth + ask_depth
            brain.depth_history.append(total_depth)

            avg_depth = (sum(brain.depth_history) / len(brain.depth_history)
                         if brain.depth_history else total_depth)
            depth_ratio = total_depth / max(0.001, avg_depth)

            # ── FLICKERING DETECTION ──
            if bids:
                brain.bid_price_history.append(bids[0][0])
            if asks:
                brain.ask_price_history.append(asks[0][0])

            is_flickering, flicker_rate = detect_flickering(brain.bid_price_history)

            # ── ICEBERG DETECTION ──
            iceberg_bid = compute_iceberg_score(bids)
            iceberg_ask = compute_iceberg_score(asks)
            iceberg_score = max(iceberg_bid, iceberg_ask)

            # ── CONFIDENCE SCORING ──
            confidence = brain.compute_confidence(obi, flicker_rate, iceberg_score, depth_ratio)
            write_u64(mm, OFF_L1_CONFIDENCE, int(confidence * 10000))

            # ── SWEEP PROTECTION (ADAPTIVE + ANTI-PARALYSIS v10.5) ──
            if cycle > 5:
                now_ms = int(time.time() * 1000)
                if now_ms - last_sweep_ms > SWEEP_DEBOUNCE_MS:
                    bid_sweep = detect_sweep(prev_bids, bids, brain.sweep_threshold)
                    ask_sweep = detect_sweep(prev_asks, asks, brain.sweep_threshold)

                    if bid_sweep or ask_sweep:
                        side = "BID" if bid_sweep else "ASK"
                        # v10.7: Dynamic freeze from AI registry
                        dynamic_freeze = read_u64(mm, OFF_AI_FREEZE_MS)
                        if dynamic_freeze < 500:  dynamic_freeze = SWEEP_FREEZE_MS  # Fallback
                        if dynamic_freeze > 30000: dynamic_freeze = SWEEP_FREEZE_MS  # Sanity
                        freeze_until = now_ms + dynamic_freeze
                        write_u64(mm, OFF_SWEEP_FREEZE, freeze_until)
                        last_sweep_ms = now_ms

                        # v10.5: Track consecutive freezes
                        brain.record_consecutive_freeze()
                        brain.record_freeze_time(dynamic_freeze)

                        # Record PnL at sweep time for learning
                        pnl_at_sweep = read_i64(mm, OFF_REALIZED_PNL) / PRICE_SCALE

                        current_toxic = read_u64(mm, OFF_TOXIC)
                        if current_toxic > 1_000_000:
                            write_u64(mm, OFF_TOXIC, 1)
                            current_toxic = 0
                        else:
                            write_u64(mm, OFF_TOXIC, current_toxic + 1)

                        # Record for adaptive learning
                        brain.record_sweep(side, pnl_at_sweep, pnl_at_sweep,
                                          obi, total_depth)

                        log.warning(f"🚨 SWEEP ({side})! Freeze {dynamic_freeze}ms. "
                                   f"Toxic: {current_toxic + 1} | "
                                   f"Confidence: {confidence:.2f} | "
                                   f"Threshold: {brain.sweep_threshold:.2f}")
                    else:
                        # v10.5: No sweep → record active time
                        brain.record_active_time(CYCLE_MS)

            prev_bids = bids
            prev_asks = asks
            cycle += 1

            # ── ADAPTIVE LEARNING (every 10 min) ──
            brain.adapt_threshold()

            # ── EXPORT L1 STATE + WRITE AI METRICS (every 60s) ──
            if cycle % (1000 // CYCLE_MS * 60) == 0:
                mid = read_u64(mm, OFF_MICRO) / PRICE_SCALE
                toxic = read_u64(mm, OFF_TOXIC)

                # Export JSON for L2 Oracle
                fp_rate, success_rate = brain.export_l1_state(
                    obi, confidence, total_depth / PRICE_SCALE)

                # Write to mmap for cross-layer reading
                write_u64(mm, OFF_L1_FP_RATE, int(fp_rate * 10000))
                write_u64(mm, OFF_L1_SUCCESS, int(success_rate * 10000))

                # v10.5: Write uptime to mmap
                uptime_pct = brain.get_uptime_pct()
                write_u64(mm, OFF_L1_UPTIME_PCT, int(uptime_pct * 10000))

                intent_names = {0: 'SOVEREIGN', 1: 'AGGRESSIVE', 2: 'DEFENSIVE', 3: 'SCOUT'}
                intent_id = read_u64(mm, OFF_AI_INTENT)
                intent = intent_names.get(intent_id, 'SOVEREIGN')

                log.info(f"L1 status: OBI={obi:+.3f} Skew=${skew_usd:+.2f} "
                        f"Mid=${mid:.0f} Toxic={toxic} Conf={confidence:.2f} "
                        f"Thresh={brain.sweep_threshold:.2f} Up={uptime_pct:.0%} "
                        f"Ghost={'ON' if brain.ghost_active else 'OFF'} "
                        f"Intent={intent} Cycle={cycle}")

                # v10.6: Ghost Mode evaluation
                l2_regime = read_u64(mm, OFF_L2_REGIME)
                ghost_val, ghost_changed, ghost_reason = brain.evaluate_ghost_mode(
                    toxic, obi, l2_regime)
                if ghost_changed:
                    write_u64(mm, OFF_GHOST_TRANSPARENCY, ghost_val)
                    if brain.ghost_active:
                        log.warning(f"👻 GHOST MODE ACTIVATED: {ghost_reason}")
                    else:
                        log.info(f"👁️ GHOST MODE DEACTIVATED: {ghost_reason}")

                # Check if L2 triggered learning
                learning_flag = read_u64(mm, OFF_LEARNING_TRIG)
                if learning_flag == 1:
                    log.info("🧠 L2 requested learning cycle! Adapting...")
                    brain.adapt_threshold()
                    write_u64(mm, OFF_LEARNING_TRIG, 0)

            # ── SLEEP ──
            elapsed = (time.monotonic() - t0) * 1000
            sleep_ms = max(1, CYCLE_MS - elapsed)
            time.sleep(sleep_ms / 1000)

        except KeyboardInterrupt:
            break
        except Exception as e:
            log.error(f"L1 error: {e}")
            time.sleep(1)

    # Clean shutdown
    write_i64(mm, OFF_L1_SKEW, 0)
    mm.close()
    os.close(fd)
    log.info("═══ L1 SHIELD v13.0 STOPPED ═══")


if __name__ == "__main__":
    main()
