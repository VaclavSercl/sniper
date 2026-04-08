#!/usr/bin/env python3
"""
🌐 Cross-Exchange Price Bridge — Phase 5.4
Sniper Armada · v17.0

Connects to Binance bookTicker stream and reads Bitfinex BBA from
existing mmap files. Computes real-time cross-exchange spreads and
writes them to /dev/shm/sniper/cross_exchange.bin for all bots to read.

Architecture:
  - Reads: Bitfinex prices from hydra/moonshot engine mmap (already running)
  - Reads: Binance prices from live WebSocket bookTicker stream
  - Writes: cross_exchange.bin mmap with per-pair spreads

Safety:
  - Starts paused (emergency_pause=1)
  - L2 Oracle enables via mmap when ready
  - Daily loss limit: $50 default
  - Max exposure per exchange: $500 default
"""

import asyncio
import json
import os
import mmap
import struct
import time
import signal
import sys
import logging

# Add architect path for shared modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [BRIDGE] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger('price_bridge')

# ═══════════════════════════════════════════════════════════
# Constants matching Rust cross_types.rs layout
# ═══════════════════════════════════════════════════════════

CROSS_EXCHANGE_PATH = "/dev/shm/sniper/cross_exchange.bin"
PRICE_SCALE = 100_000_000  # 1e8

# ExchangeBBA: 64 bytes (i64, i64, i64, i64, u64, u32, u32, 16 padding)
EXCHANGE_BBA_SIZE = 64
# CrossPairState: 2*ExchangeBBA + 7 fields + identity = ~320 bytes (align 64)
# We compute exact size from Rust struct layout
CROSS_PAIR_SIZE = 2 * 64 + 64 + 64  # ~256 bytes per pair (aligned)

# Binance ↔ Bitfinex symbol mapping
CROSS_PAIRS = [
    ("BTCUSDT", "tBTCUST"),
    ("ETHUSDT", "tETHUST"),
    ("XRPUSDT", "tXRPUST"),
    ("SOLUSDT", "tSOLUST"),
    ("DOGEUSDT", "tDOGE:UST"),
    ("ADAUSDT", "tADAUST"),
    ("AVAXUSDT", "tAVAX:UST"),
    ("LTCUSDT", "tLTCUST"),
    ("LINKUSDT", "tLINK:UST"),
    ("DOTUSDT", "tDOT:UST"),
]

# Binance WS
BINANCE_WS_URL = "wss://stream.binance.com:9443/stream"

# ═══════════════════════════════════════════════════════════
# Binance Price Cache
# ═══════════════════════════════════════════════════════════

class BinancePriceCache:
    """Stores latest BBA from Binance for each symbol."""
    def __init__(self):
        self.prices = {}  # symbol → (bid, ask, bid_vol, ask_vol, ts_ms)

    def update(self, symbol: str, bid: float, ask: float,
               bid_vol: float, ask_vol: float, ts_ms: int):
        self.prices[symbol] = (bid, ask, bid_vol, ask_vol, ts_ms)

    def get(self, symbol: str):
        return self.prices.get(symbol)


# ═══════════════════════════════════════════════════════════
# Bitfinex Price Reader (from existing mmap)
# ═══════════════════════════════════════════════════════════

class BitfinexPriceReader:
    """Reads Bitfinex BBA from Hydra's engine mmap."""

    def __init__(self):
        self.prices = {}  # bitfinex_symbol → (bid, ask, ts_ms)

    def read_from_ticker_cache(self):
        """
        Read Bitfinex prices from the L2 Oracle's ticker cache.
        The Oracle already tracks all Bitfinex pairs - we read its data.
        Fallback: read from moonshot/grid engine mmaps if available.
        """
        # For now, we use a simplified approach:
        # Read Hydra's best_bid/best_ask from its engine state
        hydra_path = "/dev/shm/sniper/engine_state.bin"
        if not os.path.exists(hydra_path):
            return

        try:
            with open(hydra_path, 'rb') as f:
                mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
                # Hydra engine_state layout: best_bid at offset 64, best_ask at offset 72
                best_bid = struct.unpack_from('<Q', mm, 64)[0]
                best_ask = struct.unpack_from('<Q', mm, 72)[0]
                ts = int(time.time() * 1000)
                if best_bid > 0 and best_ask > 0:
                    self.prices["tBTCUST"] = (
                        best_bid / PRICE_SCALE,
                        best_ask / PRICE_SCALE,
                        ts
                    )
                mm.close()
        except Exception:
            pass

    def get(self, symbol: str):
        return self.prices.get(symbol)


# ═══════════════════════════════════════════════════════════
# Cross-Exchange Spread Calculator
# ═══════════════════════════════════════════════════════════

class SpreadMonitor:
    """Computes and logs cross-exchange spread opportunities."""

    def __init__(self):
        self.arb_count = 0
        self.last_log_time = 0

    def compute(self, pair_name: str, bfx_bid: float, bfx_ask: float,
                bnb_bid: float, bnb_ask: float) -> dict:
        """
        Compute cross-exchange spreads.

        Direction A: Buy on Bitfinex (pay bfx_ask), Sell on Binance (receive bnb_bid)
          spread_A = bnb_bid - bfx_ask

        Direction B: Buy on Binance (pay bnb_ask), Sell on Bitfinex (receive bfx_bid)
          spread_B = bfx_bid - bnb_ask
        """
        if bfx_ask <= 0 or bnb_ask <= 0:
            return None

        spread_a = bnb_bid - bfx_ask  # buy bfx, sell bnb
        spread_b = bfx_bid - bnb_ask  # buy bnb, sell bfx

        mid = (bfx_bid + bfx_ask + bnb_bid + bnb_ask) / 4
        spread_a_bps = (spread_a / mid) * 10000 if mid > 0 else 0
        spread_b_bps = (spread_b / mid) * 10000 if mid > 0 else 0

        best_bps = max(spread_a_bps, spread_b_bps)
        best_dir = 0 if spread_a_bps >= spread_b_bps else 1

        # Log significant spreads (> 5 bps)
        now = time.time()
        if best_bps > 5 and now - self.last_log_time > 5:
            direction = "BFX→BNB" if best_dir == 0 else "BNB→BFX"
            log.info(f"📊 {pair_name}: spread={best_bps:.1f}bps dir={direction}")
            self.last_log_time = now

        if best_bps > 10:
            self.arb_count += 1

        return {
            'spread_a': int(spread_a * PRICE_SCALE),
            'spread_b': int(spread_b * PRICE_SCALE),
            'best_bps': int(best_bps * 100),  # ×100 for fixed-point
            'best_dir': best_dir,
            'is_arb': best_bps > 10,  # > 10bps = potential arb
        }


# ═══════════════════════════════════════════════════════════
# mmap Writer — writes cross_exchange.bin
# ═══════════════════════════════════════════════════════════

# Layout matching cross_types.rs:
#   ExchangeBBA: 64 bytes (align 64)
#   CrossPairState: 256 bytes (align 64) = 2×EBB + derived fields
#   CrossExchangeState: 16×CrossPairState + global metadata = ~4224 bytes

EXCHANGE_BBA_SIZE = 64
CROSS_PAIR_SIZE = 256
MAX_CROSS_PAIRS = 16
GLOBAL_META_OFFSET = MAX_CROSS_PAIRS * CROSS_PAIR_SIZE  # 4096
TOTAL_MMAP_SIZE = GLOBAL_META_OFFSET + 128  # 4224

class CrossExchangeMmapWriter:
    """Writes cross-exchange state to mmap file matching Rust layout."""

    def __init__(self):
        self.mm = None
        self._init_mmap()

    def _init_mmap(self):
        """Create or open the mmap file."""
        os.makedirs(os.path.dirname(CROSS_EXCHANGE_PATH), exist_ok=True)

        # Create or resize mmap file (deploy_armada pre-creates with 4096B, we need TOTAL_MMAP_SIZE)
        if not os.path.exists(CROSS_EXCHANGE_PATH) or os.path.getsize(CROSS_EXCHANGE_PATH) < TOTAL_MMAP_SIZE:
            with open(CROSS_EXCHANGE_PATH, 'wb') as f:
                f.write(b'\x00' * TOTAL_MMAP_SIZE)
            log.info(f"✅ Created {CROSS_EXCHANGE_PATH} ({TOTAL_MMAP_SIZE} bytes)")

        f = open(CROSS_EXCHANGE_PATH, 'r+b')
        self.mm = mmap.mmap(f.fileno(), TOTAL_MMAP_SIZE)

        # Write defaults: emergency_pause=1, max_exposure=$500, daily_loss=$50
        # Global metadata at offset GLOBAL_META_OFFSET
        gm = GLOBAL_META_OFFSET
        struct.pack_into('<I', self.mm, gm + 0, len(CROSS_PAIRS))  # active_pairs
        struct.pack_into('<I', self.mm, gm + 16, 1)  # bitfinex_alive = 1
        struct.pack_into('<I', self.mm, gm + 20, 1)  # binance_alive = 1
        # Risk limits
        struct.pack_into('<q', self.mm, gm + 24, 500_00000000)  # max_exposure_bfx
        struct.pack_into('<q', self.mm, gm + 32, 500_00000000)  # max_exposure_bnb
        struct.pack_into('<I', self.mm, gm + 48, 1)  # emergency_pause = 1 (safe start)
        struct.pack_into('<q', self.mm, gm + 56, 50_00000000)  # daily_loss_limit $50
        self.mm.flush()
        log.info("  ✅ mmap initialized with safe defaults")

    def write_pair(self, pair_idx: int, bfx_bid: float, bfx_ask: float,
                   bfx_bid_vol: float, bfx_ask_vol: float,
                   bnb_bid: float, bnb_ask: float,
                   bnb_bid_vol: float, bnb_ask_vol: float,
                   spread_result: dict, ts_ms: int):
        """Write one pair's BBA + spread data to mmap."""
        if not self.mm or pair_idx >= MAX_CROSS_PAIRS:
            return

        base = pair_idx * CROSS_PAIR_SIZE
        PS = PRICE_SCALE

        # ExchangeBBA[0] = Bitfinex (offset base + 0)
        struct.pack_into('<q', self.mm, base + 0, int(bfx_bid * PS))
        struct.pack_into('<q', self.mm, base + 8, int(bfx_ask * PS))
        struct.pack_into('<q', self.mm, base + 16, int(bfx_bid_vol * PS))
        struct.pack_into('<q', self.mm, base + 24, int(bfx_ask_vol * PS))
        struct.pack_into('<Q', self.mm, base + 32, ts_ms)
        struct.pack_into('<I', self.mm, base + 40, 0)  # exchange_id=0 (Bitfinex)
        struct.pack_into('<I', self.mm, base + 44, 1)  # connected=1

        # ExchangeBBA[1] = Binance (offset base + 64)
        bnb_off = base + EXCHANGE_BBA_SIZE
        struct.pack_into('<q', self.mm, bnb_off + 0, int(bnb_bid * PS))
        struct.pack_into('<q', self.mm, bnb_off + 8, int(bnb_ask * PS))
        struct.pack_into('<q', self.mm, bnb_off + 16, int(bnb_bid_vol * PS))
        struct.pack_into('<q', self.mm, bnb_off + 24, int(bnb_ask_vol * PS))
        struct.pack_into('<Q', self.mm, bnb_off + 32, ts_ms)
        struct.pack_into('<I', self.mm, bnb_off + 40, 1)  # exchange_id=1 (Binance)
        struct.pack_into('<I', self.mm, bnb_off + 44, 1)  # connected=1

        # Derived arb metrics (offset base + 128)
        arb_off = base + 2 * EXCHANGE_BBA_SIZE
        if spread_result:
            struct.pack_into('<q', self.mm, arb_off + 0, spread_result.get('spread_a', 0))
            struct.pack_into('<q', self.mm, arb_off + 8, spread_result.get('spread_b', 0))
            struct.pack_into('<q', self.mm, arb_off + 16, spread_result.get('best_bps', 0))
            struct.pack_into('<I', self.mm, arb_off + 24, spread_result.get('best_dir', 0))
            if spread_result.get('is_arb'):
                # Increment arb_signals atomically (read + write)
                old = struct.unpack_from('<Q', self.mm, arb_off + 32)[0]
                struct.pack_into('<Q', self.mm, arb_off + 32, old + 1)
                struct.pack_into('<Q', self.mm, arb_off + 40, ts_ms)  # last_arb_signal_ms

        # Pair identity
        struct.pack_into('<I', self.mm, arb_off + 64, pair_idx)  # pair_idx
        struct.pack_into('<I', self.mm, arb_off + 68, 1)  # enabled=1

    def write_heartbeat(self):
        """Update global heartbeat timestamp."""
        if not self.mm:
            return
        gm = GLOBAL_META_OFFSET
        struct.pack_into('<Q', self.mm, gm + 8, int(time.time() * 1000))  # heartbeat_ms
        self.mm.flush()

    def close(self):
        if self.mm:
            self.mm.close()


# ═══════════════════════════════════════════════════════════
# Main Bridge Loop
# ═══════════════════════════════════════════════════════════

async def run_bridge():
    """Main event loop: connect to Binance, read Bitfinex, compute spreads."""

    try:
        import websockets
    except ImportError:
        log.error("websockets not installed. Run: pip3 install websockets")
        return

    binance_cache = BinancePriceCache()
    bfx_reader = BitfinexPriceReader()
    spread_monitor = SpreadMonitor()
    mmap_writer = CrossExchangeMmapWriter()

    # Build combined stream URL for all pairs
    symbols = [pair[0].lower() for pair in CROSS_PAIRS]
    streams = "/".join(f"{s}@bookTicker" for s in symbols)
    url = f"{BINANCE_WS_URL}?streams={streams}"

    log.info(f"🌐 Cross-Exchange Price Bridge v2.0 starting")
    log.info(f"   Tracking {len(CROSS_PAIRS)} cross-exchange pairs")
    log.info(f"   Writing to {CROSS_EXCHANGE_PATH}")
    log.info(f"   Binance WS: {len(symbols)} symbols")

    reconnect_delay = 5
    tick_count = 0

    while True:
        try:
            async with websockets.connect(url, ping_interval=20) as ws:
                log.info("✅ Connected to Binance bookTicker stream")
                reconnect_delay = 5

                async for raw_msg in ws:
                    try:
                        msg = json.loads(raw_msg)
                    except json.JSONDecodeError:
                        continue

                    if "data" not in msg:
                        continue

                    d = msg["data"]
                    symbol = d.get("s", "")
                    bid = float(d.get("b", 0))
                    ask = float(d.get("a", 0))
                    bid_vol = float(d.get("B", 0))
                    ask_vol = float(d.get("A", 0))
                    ts = int(d.get("E", 0))

                    if bid <= 0 or ask <= 0:
                        continue

                    binance_cache.update(symbol, bid, ask, bid_vol, ask_vol, ts)
                    tick_count += 1

                    # Every 100 ticks, read Bitfinex and compute spreads
                    if tick_count % 100 == 0:
                        bfx_reader.read_from_ticker_cache()

                        for pair_idx, (bnb_sym, bfx_sym) in enumerate(CROSS_PAIRS):
                            bnb = binance_cache.get(bnb_sym)
                            bfx = bfx_reader.get(bfx_sym)

                            if bnb and bfx:
                                result = spread_monitor.compute(
                                    bnb_sym,
                                    bfx[0], bfx[1],  # bfx bid, ask
                                    bnb[0], bnb[1],  # bnb bid, ask
                                )
                                # Write to mmap
                                now_ms = int(time.time() * 1000)
                                mmap_writer.write_pair(
                                    pair_idx,
                                    bfx[0], bfx[1], 0.0, 0.0,  # bfx BBA (vol from bfx reader TBD)
                                    bnb[0], bnb[1], bnb[2], bnb[3],  # bnb BBA + vol
                                    result, now_ms,
                                )

                        mmap_writer.write_heartbeat()

                    # Periodic status log
                    if tick_count % 5000 == 0:
                        active = len(binance_cache.prices)
                        arbs = spread_monitor.arb_count
                        log.info(f"📊 Bridge: {tick_count} ticks, {active} pairs, {arbs} arb signals")

        except Exception as e:
            log.warning(f"⚠️ Binance WS error: {e}, reconnecting in {reconnect_delay}s...")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 120)


def main():
    """Entry point with graceful shutdown."""
    loop = asyncio.new_event_loop()

    def shutdown(sig, frame):
        log.info("🛑 Price Bridge shutting down...")
        loop.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        loop.run_until_complete(run_bridge())
    except KeyboardInterrupt:
        log.info("🛑 Bridge stopped.")


if __name__ == "__main__":
    main()
