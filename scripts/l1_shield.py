#!/usr/bin/env python3
"""
🛡️ BEROUN L1 SHIELD v9.2 — Tactical GPU AI Sidecar
═══════════════════════════════════════════════════
Architecture: Reads orderbook from mmap, writes skew + freeze back
  - OBI Micro-Skewing: shifts grid bias based on order book imbalance
  - Sweep Protection: detects large sweeps and freezes execution
  - Toxic Flow Detection: tracks adverse selection patterns

Latency: <1ms cycle (reads atomics from /dev/shm/beroun/)
Communication: mmap atomics (zero-copy, zero-latency)
═══════════════════════════════════════════════════
"""

import mmap
import struct
import time
import sys
import os
import signal
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [L1] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/home/wwwenda/hft-sniper/logs/l1_shield.log')
    ]
)
log = logging.getLogger('L1')

# ═══ MMAP LAYOUT (must match types.rs EngineState) ═══
# We need offsets for specific atomic fields.
# The struct uses fixed-size atomics, so we calculate offsets.
# Key fields we READ:
#   - bids[0..5] and asks[0..5] (OrderBookLevel = price:u64 + amount:i64 = 16 bytes each)
#   - micro_price (u64)
# Key fields we WRITE:
#   - l1_skew_adjustment (i64)  — micro-skew for grid bias
#   - sweep_freeze_until (u64)  — epoch_ms freeze deadline
#   - toxic_flow_hits (u64)     — counter

ENGINE_MMAP = "/dev/shm/beroun/engine_state.bin"
PRICE_SCALE = 1e8

# ═══ OFFSET CALCULATOR ═══
# OrderBookLevel = (u64 price, i64 amount) = 16 bytes
# EngineState layout from types.rs:
#   bids: [OrderBookLevel; 10]   = 160 bytes  (offset 0)
#   asks: [OrderBookLevel; 10]   = 160 bytes  (offset 160)
#   micro_price: u64             = 8 bytes    (offset 320)
#   wallet_btc: i64              = 8 bytes    (offset 328)
#   wallet_usd: i64              = 8 bytes    (offset 336)
#   position: i64                = 8 bytes    (offset 344)
#   aep: u64                     = 8 bytes    (offset 352)
#   obi: i64                     = 8 bytes    (offset 360)
#   last_trade_price: u64        = 8 bytes    (offset 368)
#   last_trade_amount: i64       = 8 bytes    (offset 376)
#   last_trade_ts: u64           = 8 bytes    (offset 384)
#   vwap: u64                    = 8 bytes    (offset 392)
#   ai_bias: i64                 = 8 bytes    (offset 400)
#   tick_to_trade_us: u64        = 8 bytes    (offset 408)
#   ai_heartbeat_ms: u64         = 8 bytes    (offset 416)
#   buy_fill_count: u64          = 8 bytes    (offset 424)
#   sell_fill_count: u64         = 8 bytes    (offset 432)
#   monthly_volume_usd: u64     = 8 bytes    (offset 440)
#   --- v9.2 Hybrid Intelligence ---
#   session_buy_volume: u64      = 8 bytes    (offset 448)
#   session_sell_volume: u64     = 8 bytes    (offset 456)
#   session_buy_usd: u64         = 8 bytes    (offset 464)
#   session_sell_usd: u64        = 8 bytes    (offset 472)
#   session_fill_count: u64      = 8 bytes    (offset 480)
#   session_pnl_realized: i64    = 8 bytes    (offset 488)
#   toxic_flow_hits: u64         = 8 bytes    (offset 496)
#   sweep_freeze_until: u64      = 8 bytes    (offset 504)
#   l1_skew_adjustment: i64      = 8 bytes    (offset 512)
#   analytics_checkpoint_ms: u64 = 8 bytes    (offset 520)

OFF_BIDS = 0       # bids[0].price
OFF_ASKS = 160     # asks[0].price
OFF_MICRO = 320
OFF_OBI = 360
OFF_LAST_TRADE_PRICE = 368
OFF_LAST_TRADE_AMT = 376
OFF_LAST_TRADE_TS = 384
OFF_TOXIC = 496
OFF_SWEEP_FREEZE = 504
OFF_L1_SKEW = 512

# ═══ CONFIGURATION ═══
CYCLE_MS = 50           # 50ms cycle = 20 updates/sec
OBI_SKEW_FACTOR = 0.3   # How aggressively to skew based on OBI (0.0-1.0)
MAX_SKEW_USD = 3.0       # Maximum skew in USD
SWEEP_THRESHOLD = 5      # Number of levels consumed = sweep
SWEEP_FREEZE_MS = 15000  # 15 second freeze after sweep
VWAP_VELOCITY_WINDOW = 20  # cycles for velocity calc

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
    struct.pack_into('<Q', mm, offset, val)

def write_i64(mm, offset, val):
    struct.pack_into('<q', mm, offset, val)


def read_orderbook_levels(mm, base_offset, n=5):
    """Read n OrderBookLevel (price, amount) from mmap."""
    levels = []
    for i in range(n):
        off = base_offset + i * 16
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
    return (bid_vol - ask_vol) / total  # +1 = strong bids, -1 = strong asks


def detect_sweep(prev_levels, curr_levels, side='bid'):
    """Detect if multiple price levels were consumed (sweep)."""
    if not prev_levels or not curr_levels:
        return False
    prev_prices = {p for p, _ in prev_levels}
    curr_prices = {p for p, _ in curr_levels}
    consumed = prev_prices - curr_prices
    return len(consumed) >= SWEEP_THRESHOLD


def main():
    global running

    log.info("═══ L1 SHIELD v9.2 STARTING ═══")
    log.info(f"  mmap: {ENGINE_MMAP}")
    log.info(f"  Cycle: {CYCLE_MS}ms | Skew factor: {OBI_SKEW_FACTOR}")
    log.info(f"  Max skew: ${MAX_SKEW_USD} | Sweep freeze: {SWEEP_FREEZE_MS}ms")

    if not os.path.exists(ENGINE_MMAP):
        log.error(f"Engine mmap not found: {ENGINE_MMAP}")
        sys.exit(1)

    fd = os.open(ENGINE_MMAP, os.O_RDWR)
    mm = mmap.mmap(fd, 0, access=mmap.ACCESS_WRITE)

    prev_bids = []
    prev_asks = []
    cycle = 0

    log.info("═══ L1 SHIELD ACTIVE ═══")

    while running:
        try:
            t0 = time.monotonic()

            # ── READ ORDERBOOK ──
            bids = read_orderbook_levels(mm, OFF_BIDS, 5)
            asks = read_orderbook_levels(mm, OFF_ASKS, 5)

            # ── OBI MICRO-SKEWING ──
            obi = compute_obi(bids, asks, depth=3)

            # OBI > 0 → strong bids → push asks higher (buyer's market, be greedy)
            # OBI < 0 → strong asks → pull bids lower (seller pressure, be cautious)
            skew_usd = obi * OBI_SKEW_FACTOR * MAX_SKEW_USD
            skew_usd = max(-MAX_SKEW_USD, min(MAX_SKEW_USD, skew_usd))
            skew_scaled = int(skew_usd * PRICE_SCALE)

            write_i64(mm, OFF_L1_SKEW, skew_scaled)

            # ── SWEEP PROTECTION ──
            if cycle > 0:
                bid_sweep = detect_sweep(prev_bids, bids, 'bid')
                ask_sweep = detect_sweep(prev_asks, asks, 'ask')

                if bid_sweep or ask_sweep:
                    side = "BID" if bid_sweep else "ASK"
                    now_ms = int(time.time() * 1000)
                    freeze_until = now_ms + SWEEP_FREEZE_MS
                    write_u64(mm, OFF_SWEEP_FREEZE, freeze_until)

                    # Increment toxic_flow_hits
                    current_toxic = read_u64(mm, OFF_TOXIC)
                    write_u64(mm, OFF_TOXIC, current_toxic + 1)

                    log.warning(f"🚨 SWEEP DETECTED ({side})! Freeze {SWEEP_FREEZE_MS}ms. "
                               f"Toxic hits: {current_toxic + 1}")

            prev_bids = bids
            prev_asks = asks
            cycle += 1

            # ── LOG (every 60s) ──
            if cycle % (1000 // CYCLE_MS * 60) == 0:
                mid = read_u64(mm, OFF_MICRO) / PRICE_SCALE
                toxic = read_u64(mm, OFF_TOXIC)
                log.info(f"L1 status: OBI={obi:+.3f} Skew=${skew_usd:+.2f} "
                        f"Mid=${mid:.0f} Toxic={toxic} Cycle={cycle}")

            # ── SLEEP ──
            elapsed = (time.monotonic() - t0) * 1000
            sleep_ms = max(1, CYCLE_MS - elapsed)
            time.sleep(sleep_ms / 1000)

        except KeyboardInterrupt:
            break
        except Exception as e:
            log.error(f"L1 error: {e}")
            time.sleep(1)

    # Clean shutdown — zero out skew
    write_i64(mm, OFF_L1_SKEW, 0)
    mm.close()
    os.close(fd)
    log.info("═══ L1 SHIELD STOPPED ═══")


if __name__ == "__main__":
    main()
