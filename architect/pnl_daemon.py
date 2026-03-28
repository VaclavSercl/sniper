#!/usr/bin/env python3
"""
💰 Sniper Armada — PnL Daemon v2.0
Reads trade fills from bot logs, processes FIFO PnL, writes mmap + SQLite.

Runs as a background daemon alongside the bots.
Reads fills from structured JSON log lines (trade_executed events).

Usage:
    python3 architect/pnl_daemon.py
"""

import os
import sys
import json
import time
import struct
import hashlib
import hmac
import logging
import threading
import signal
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import requests

# Add shared to path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "shared"))

from pnl_engine import (
    FIFOEngine, PnlDatabase, PnlMmapWriter,
    BOT_INDEX, PRICE_SCALE, format_pnl_short,
)

# ── CONFIG ──────────────────────────────────────────────────
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Load .env
def load_dotenv(path):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

BFX_API_KEY = os.environ.get("BITFINEX_API_KEY", "")
BFX_API_SECRET = os.environ.get("BITFINEX_API_SECRET", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [PNL] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(LOG_DIR, "pnl_daemon.log")),
    ],
)
log = logging.getLogger("pnl")

# ── BITFINEX REST API ──────────────────────────────────────

def bfx_authenticated(path, body=None):
    """Authenticated Bitfinex REST v2 request."""
    if not BFX_API_KEY or not BFX_API_SECRET:
        return None
    nonce = str(int(time.time() * 1_000_000))
    body_json = json.dumps(body) if body else "{}"
    sig_payload = f"/api/{path}{nonce}{body_json}"
    sig = hmac.new(
        BFX_API_SECRET.encode(), sig_payload.encode(), hashlib.sha384
    ).hexdigest()
    headers = {
        "bfx-nonce": nonce,
        "bfx-apikey": BFX_API_KEY,
        "bfx-signature": sig,
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(
            f"https://api.bitfinex.com/{path}",
            headers=headers, data=body_json, timeout=15
        )
        return r.json()
    except Exception as e:
        log.error(f"Bitfinex API error: {e}")
        return None


def fetch_recent_trades(symbol: str = "tBTCUSD", limit: int = 250):
    """Fetch recent trades from Bitfinex REST API."""
    result = bfx_authenticated("v2/auth/r/trades/hist", {
        "symbol": symbol,
        "limit": limit,
        "sort": -1,  # newest first
    })
    if not result or not isinstance(result, list):
        return []
    return result


def fetch_wallets():
    """Fetch current wallet balances."""
    result = bfx_authenticated("v2/auth/r/wallets")
    if not result or not isinstance(result, list):
        return {}
    wallets = {}
    for w in result:
        if isinstance(w, list) and len(w) >= 3:
            wtype, currency, balance = w[0], w[1], w[2]
            if wtype == "exchange":
                wallets[currency] = float(balance)
    return wallets


# ── GLOBAL FEE STATE (shared mmap for ALL bots & AI) ────────
# Layout matches Rust GlobalFeeState in fee_types.rs:
#   offset 0:  maker_fee_bps    (u64, bps×100)
#   offset 8:  taker_fee_bps    (u64, bps×100)
#   offset 16: deriv_maker_bps  (u64, bps×100)
#   offset 24: deriv_taker_bps  (u64, bps×100)
#   offset 32: last_updated_ms  (u64, epoch ms)
#   offset 40: monthly_volume   (i64, USD)
#   offset 48: fee_tier         (u64)
#   offset 56: heartbeat_ms     (u64, epoch ms)

FEE_STATE_PATH = "/dev/shm/beroun/fee_state.bin"
FEE_STATE_SIZE = 64  # 1 cache line

class FeeStateWriter:
    """Writes GlobalFeeState to shared mmap. Read by all Rust bots."""

    def __init__(self):
        self.mm = None
        try:
            os.makedirs(os.path.dirname(FEE_STATE_PATH), exist_ok=True)
            fd = os.open(FEE_STATE_PATH, os.O_RDWR | os.O_CREAT)
            os.ftruncate(fd, FEE_STATE_SIZE)
            import mmap
            self.mm = mmap.mmap(fd, FEE_STATE_SIZE)
            os.close(fd)
            # Write conservative defaults if empty
            if self.mm[:8] == b'\x00' * 8:
                self._write_defaults()
            log.info(f"FeeStateWriter: mmap opened at {FEE_STATE_PATH}")
        except Exception as e:
            log.error(f"FeeStateWriter init failed: {e}")

    def _write_defaults(self):
        """Write conservative defaults (Bitfinex standard tier)."""
        self.write(maker_bps=1000, taker_bps=2000,
                   deriv_maker=200, deriv_taker=650)

    def write(self, maker_bps=None, taker_bps=None,
              deriv_maker=None, deriv_taker=None,
              volume_usd=None, tier=None):
        """Write fee values to shared mmap."""
        if not self.mm:
            return
        now_ms = int(time.time() * 1000)
        if maker_bps is not None:
            struct.pack_into('<Q', self.mm, 0, int(maker_bps))
        if taker_bps is not None:
            struct.pack_into('<Q', self.mm, 8, int(taker_bps))
        if deriv_maker is not None:
            struct.pack_into('<Q', self.mm, 16, int(deriv_maker))
        if deriv_taker is not None:
            struct.pack_into('<Q', self.mm, 24, int(deriv_taker))
        struct.pack_into('<Q', self.mm, 32, now_ms)  # last_updated_ms
        if volume_usd is not None:
            struct.pack_into('<q', self.mm, 40, int(volume_usd))
        if tier is not None:
            struct.pack_into('<Q', self.mm, 48, int(tier))
        struct.pack_into('<Q', self.mm, 56, now_ms)  # heartbeat
        self.mm.flush()

    def read(self):
        """Read current fee state (for logging/debug)."""
        if not self.mm:
            return {}
        return {
            "maker_bps": struct.unpack_from('<Q', self.mm, 0)[0] / 100.0,
            "taker_bps": struct.unpack_from('<Q', self.mm, 8)[0] / 100.0,
            "deriv_maker_bps": struct.unpack_from('<Q', self.mm, 16)[0] / 100.0,
            "deriv_taker_bps": struct.unpack_from('<Q', self.mm, 24)[0] / 100.0,
            "last_updated": struct.unpack_from('<Q', self.mm, 32)[0],
            "volume_usd": struct.unpack_from('<q', self.mm, 40)[0],
            "fee_tier": struct.unpack_from('<Q', self.mm, 48)[0],
        }

    def close(self):
        if self.mm:
            self.mm.close()


def fetch_and_update_fees(fee_writer: FeeStateWriter):
    """Fetch fee tier from Bitfinex /v2/auth/r/summary and update shared mmap."""
    result = bfx_authenticated("v2/auth/r/summary")
    if not result:
        log.warning("Fee fetch: API returned None")
        return False

    try:
        # Bitfinex summary response format:
        # [TRADE_VOL_30D, [MAKER_FEE, _, TAKER_FEE, _], [DERIV_MAKER, _, DERIV_TAKER, _], ...]
        # Fees are returned as decimals (e.g., 0.001 = 0.10%)
        if isinstance(result, list) and len(result) >= 2:
            # Trade volume
            vol_30d = 0
            if isinstance(result[0], (int, float)):
                vol_30d = int(result[0])

            # Exchange fees
            maker_pct = 0.001  # 0.10% default
            taker_pct = 0.002  # 0.20% default
            if isinstance(result[1], list) and len(result[1]) >= 4:
                if result[1][0] is not None:
                    maker_pct = abs(float(result[1][0]))
                if result[1][2] is not None:
                    taker_pct = abs(float(result[1][2]))

            # Derivative fees
            deriv_maker = 0.0002  # 0.02% default
            deriv_taker = 0.00065  # 0.065% default
            if isinstance(result, list) and len(result) >= 3:
                if isinstance(result[2], list) and len(result[2]) >= 4:
                    if result[2][0] is not None:
                        deriv_maker = abs(float(result[2][0]))
                    if result[2][2] is not None:
                        deriv_taker = abs(float(result[2][2]))

            # Convert to bps×100: 0.001 (0.10%) = 10 bps = 1000 in our scale
            maker_bps100 = int(maker_pct * 1_000_000)
            taker_bps100 = int(taker_pct * 1_000_000)
            dm_bps100 = int(deriv_maker * 1_000_000)
            dt_bps100 = int(deriv_taker * 1_000_000)

            fee_writer.write(
                maker_bps=maker_bps100,
                taker_bps=taker_bps100,
                deriv_maker=dm_bps100,
                deriv_taker=dt_bps100,
                volume_usd=vol_30d,
            )

            log.info(f"💰 Fee update: maker={maker_pct*100:.3f}% taker={taker_pct*100:.3f}% "
                     f"vol_30d=${vol_30d:,.0f}")
            return True

        log.warning(f"Fee fetch: unexpected format: {str(result)[:200]}")
        return False

    except Exception as e:
        log.error(f"Fee parse error: {e}")
        return False


# ── FILL PROCESSOR ──────────────────────────────────────────

class FillProcessor:
    """
    Main fill processing pipeline.
    Reads fills from Bitfinex REST API, processes FIFO, writes results.
    """

    # v14.0: GID range → bot mapping for trade attribution
    # Rust bots tag every order with BOT_GID_X (1000/2000/3000/4000)
    # Bitfinex order response has GID at index 1
    GID_RANGES = [
        (1000, 1999, "hydra"),
        (2000, 2999, "moonshot"),
        (3000, 3999, "grid"),
        (4000, 4999, "trigon"),
    ]

    def __init__(self):
        self.db = PnlDatabase()
        self.mmap_writer = PnlMmapWriter()
        self.engines: dict = {}  # (bot, symbol) → FIFOEngine
        self.last_trade_ids: dict = defaultdict(set)  # bot → set of seen trade_ids
        self.order_gid_cache: dict = {}  # order_id → bot_name (from GID)
        self._load_all_engines()
        self._refresh_order_gid_cache()
        log.info("FillProcessor initialized")

    def _load_all_engines(self):
        """Load FIFO state from SQLite for all bots."""
        for bot in BOT_INDEX:
            for symbol in self._get_symbols(bot):
                key = (bot, symbol)
                self.engines[key] = self.db.load_fifo_state(bot, symbol)
                log.info(f"  Loaded FIFO: {bot}/{symbol} — "
                         f"queue={len(self.engines[key].queue)}, "
                         f"realized=${self.engines[key].total_realized:.4f}")

    def _get_symbols(self, bot: str) -> list:
        """Get trading symbols for a bot."""
        syms = {
            "hydra": ["tBTCUSD"],
            "moonshot": ["tBTCUSD"],
            "grid": ["tBTCUSD"],
            "trigon": ["tBTCUSD", "tETHUSD", "tETHBTC"],
        }
        return syms.get(bot, ["tBTCUSD"])

    def _refresh_order_gid_cache(self):
        """Fetch recent orders from Bitfinex and cache order_id → bot mapping via GID."""
        orders = bfx_authenticated("v2/auth/r/orders/hist", {"limit": 500})
        if not orders or not isinstance(orders, list):
            return
        cached = 0
        for o in orders:
            if not isinstance(o, list) or len(o) < 4:
                continue
            order_id = str(o[0])
            gid = o[1]  # GID is at index 1 in order response
            if gid is not None:
                bot = self._gid_to_bot(int(gid))
                if bot:
                    self.order_gid_cache[order_id] = bot
                    cached += 1
        # Keep cache bounded
        if len(self.order_gid_cache) > 10000:
            keys = sorted(self.order_gid_cache.keys())
            self.order_gid_cache = {k: self.order_gid_cache[k] for k in keys[-5000:]}
        if cached > 0:
            log.info(f"GID cache: {cached} orders mapped, {len(self.order_gid_cache)} total")

    def _gid_to_bot(self, gid: int) -> str:
        """Map GID range to bot name."""
        for lo, hi, bot in self.GID_RANGES:
            if lo <= gid <= hi:
                return bot
        return ""

    def _resolve_bot(self, order_id: str, default_bot: str) -> str:
        """Resolve bot name from order GID cache, fallback to default."""
        return self.order_gid_cache.get(order_id, default_bot)

    def process_api_fills(self, bot: str, symbol: str = "tBTCUSD"):
        """
        Fetch recent fills from Bitfinex API and process through FIFO.
        This is the main data ingestion method.
        """
        trades = fetch_recent_trades(symbol, limit=250)
        if not trades:
            return 0

        processed = 0
        key = (bot, symbol)
        if key not in self.engines:
            self.engines[key] = FIFOEngine(bot, symbol)

        engine = self.engines[key]

        # Get BTC/USD spot for cross-pair conversion
        usd_spot = 1.0
        if symbol in {"tETHBTC", "tLTCBTC", "tXRPBTC", "tSOLBTC"}:
            # Read BTC/USD from MDF mmap or use last known price
            usd_spot = self._get_btc_usd_spot()

        for trade in reversed(trades):  # Process oldest first
            if not isinstance(trade, list) or len(trade) < 11:
                continue

            trade_id = str(trade[0])

            # Skip already processed
            if trade_id in self.last_trade_ids[bot]:
                continue

            # Bitfinex trade format:
            # [ID, PAIR, MTS_CREATE, ORDER_ID, EXEC_AMOUNT, EXEC_PRICE, 
            #  ORDER_TYPE, ORDER_PRICE, MAKER, FEE, FEE_CURRENCY]
            ts_ms = int(trade[2])
            order_id = str(trade[3])

            # v14.0: Resolve bot from GID cache (order_id → bot)
            resolved_bot = self._resolve_bot(order_id, bot)
            if resolved_bot != bot:
                # This fill belongs to a different bot — skip, it'll be processed in that bot's cycle
                continue

            exec_amount = float(trade[4])
            exec_price = float(trade[5])
            fee = abs(float(trade[9]))  # fees are negative
            fee_currency = trade[10] if len(trade) > 10 else "USD"

            side = "buy" if exec_amount > 0 else "sell"
            qty = abs(exec_amount)

            # Convert fee to USD if needed
            fee_usd = fee
            if fee_currency == "BTC":
                fee_usd = fee * exec_price  # approximate
            elif fee_currency == "ETH":
                fee_usd = fee * self._get_eth_usd_spot()

            # Process through FIFO
            result = engine.on_fill(side, qty, exec_price, fee_usd, ts_ms, usd_spot)

            # Store in SQLite
            ts_iso = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()
            self.db.insert_fill(
                bot=bot, symbol=symbol, side=side, qty=qty,
                price=exec_price, fee=fee_usd, fee_currency="USD",
                trade_id=trade_id, order_id=order_id,
                ts=ts_iso, ts_ms=ts_ms,
                is_closer=result["is_closer"],
                matched_price=result.get("matched_entry_price", 0),
                gross_pnl=result.get("gross_pnl", 0),
                net_pnl=result.get("net_pnl", 0),
                numeraire_rate=usd_spot,
            )

            self.last_trade_ids[bot].add(trade_id)
            processed += 1

            if result["is_closer"]:
                log.info(f"  {bot}/{symbol} SELL {qty:.6f} @ ${exec_price:.2f} "
                         f"→ PnL: {format_pnl_short(result['net_pnl'])} "
                         f"(entry: ${result['matched_entry_price']:.2f})")

        # Save FIFO state
        if processed > 0:
            self.db.save_fifo_state(engine)
            log.info(f"  {bot}/{symbol}: +{processed} fills, "
                     f"total realized: ${engine.total_realized:.4f}")

        # Limit memory
        if len(self.last_trade_ids[bot]) > 10000:
            # Keep only newest 5000
            all_ids = sorted(self.last_trade_ids[bot])
            self.last_trade_ids[bot] = set(all_ids[-5000:])

        return processed

    def _get_btc_usd_spot(self) -> float:
        """Get current BTC/USD price from MDF mmap or API."""
        try:
            mdf_path = "/dev/shm/beroun/mdf.bin"
            if os.path.exists(mdf_path):
                with open(mdf_path, "rb") as f:
                    data = f.read(16)
                    if len(data) >= 16:
                        # First ticker slot: bid(8) + ask(8)
                        bid = struct.unpack_from("<d", data, 0)[0]
                        ask = struct.unpack_from("<d", data, 8)[0]
                        if bid > 0 and ask > 0:
                            return (bid + ask) / 2
        except Exception:
            pass
        # Fallback: REST API
        try:
            r = requests.get("https://api-pub.bitfinex.com/v2/ticker/tBTCUSD", timeout=5)
            data = r.json()
            if isinstance(data, list) and len(data) >= 7:
                return float(data[6])  # last price
        except Exception:
            pass
        return 66000.0  # Safe fallback

    def _get_eth_usd_spot(self) -> float:
        """Get ETH/USD price."""
        try:
            r = requests.get("https://api-pub.bitfinex.com/v2/ticker/tETHUSD", timeout=5)
            data = r.json()
            if isinstance(data, list) and len(data) >= 7:
                return float(data[6])
        except Exception:
            pass
        return 2000.0

    def update_mmap(self):
        """Write all PnL data to mmap for dashboard consumption."""
        windows = {"1h": 1, "24h": 24, "7d": 168, "30d": 720}

        totals = defaultdict(float)
        total_fills_24h = 0

        for bot, idx in BOT_INDEX.items():
            if idx >= 4:
                continue  # Skip unused bot slots

            bot_data = {}
            for label, hours in windows.items():
                w = self.db.get_realized_window(bot, hours)
                bot_data[f"realized_{label}"] = w["realized"]
                if label in ("1h", "24h"):
                    bot_data[f"fees_{label}"] = w["fees"]
                    bot_data[f"fills_{label}"] = w["fills"]
                    if label == "24h":
                        bot_data["closed_trades_24h"] = w["closed_trades"]
                        total_fills_24h += w["fills"]

                totals[f"total_realized_{label}"] += w["realized"]
                if label == "24h":
                    totals["total_fees_24h"] += w["fees"]

            # FIFO state
            key = (bot, "tBTCUSD")
            if key in self.engines:
                engine = self.engines[key]
                bot_data["total_closed"] = engine.total_closed
                bot_data["net_position"] = engine.net_position
                bot_data["fifo_front_price"] = engine.fifo_front_price
                bot_data["last_fill_ms"] = int(time.time() * 1000)

                # Avg per trade
                r24 = bot_data.get("realized_24h", 0)
                ct = bot_data.get("closed_trades_24h", 0)
                bot_data["avg_pnl_per_trade"] = r24 / ct if ct > 0 else 0
                f24 = bot_data.get("fills_24h", 0)
                fees24 = bot_data.get("fees_24h", 0)
                bot_data["avg_fee_per_fill"] = fees24 / f24 if f24 > 0 else 0

            self.mmap_writer.write_bot(idx, bot_data)

        # Global totals
        totals["total_fills_24h"] = total_fills_24h
        self.mmap_writer.write_global(totals)

    def record_wallet_snapshot(self):
        """Fetch and record wallet balances from Bitfinex."""
        wallets = fetch_wallets()
        if not wallets:
            return

        usd = wallets.get("USD", 0)
        btc = wallets.get("BTC", 0)
        btc_price = self._get_btc_usd_spot()

        self.db.record_wallet(usd, btc, btc_price)

        # Update mmap with wallet data
        total_usd = usd + btc * btc_price
        delta_1h = self.db.get_wallet_delta(1)
        delta_24h = self.db.get_wallet_delta(24)

        self.mmap_writer.write_global({
            "wallet_usd": usd,
            "wallet_btc": btc,
            "wallet_total_usd": total_usd,
            "wallet_delta_1h": delta_1h,
            "wallet_delta_24h": delta_24h,
        })

        log.info(f"Wallet: USD={usd:.2f} BTC={btc:.6f} Total=${total_usd:.2f} "
                 f"Δ1h={format_pnl_short(delta_1h)} Δ24h={format_pnl_short(delta_24h)}")

    def run_cycle(self, cycle_num: int = 0):
        """Run one processing cycle: fetch fills → FIFO → mmap."""
        # Refresh GID cache every 10 cycles (~5 min)
        if cycle_num % 10 == 0:
            self._refresh_order_gid_cache()

        # Process fills for running bots
        import subprocess
        for bot in ["hydra", "moonshot", "grid", "trigon"]:
            # Check if bot is running
            try:
                r = subprocess.run(["pgrep", "-f", f"{bot}-core"],
                                   capture_output=True, text=True)
                if r.returncode != 0:
                    continue
            except Exception:
                continue

            for symbol in self._get_symbols(bot):
                try:
                    n = self.process_api_fills(bot, symbol)
                    if n > 0:
                        log.info(f"{bot}/{symbol}: processed {n} new fills")
                except Exception as e:
                    log.error(f"{bot}/{symbol} fill processing error: {e}")

        # Update mmap
        try:
            self.update_mmap()
        except Exception as e:
            log.error(f"mmap update error: {e}")

    def cleanup(self):
        """Periodic cleanup of old data."""
        try:
            self.db.cleanup_old(days=90)
            log.info("Cleanup: removed data older than 90 days")
        except Exception as e:
            log.error(f"Cleanup error: {e}")


# ══════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════

def main():
    log.info("💰 PnL Daemon v2.0 — Volatility-Neutral FIFO Engine")
    log.info(f"   DB: {os.path.expanduser('~/.local/share/sniper/pnl.db')}")
    log.info(f"   mmap: /dev/shm/beroun/pnl_state.bin")
    log.info(f"   fee_state: {FEE_STATE_PATH}")
    log.info(f"   Bots: {', '.join(BOT_INDEX.keys())}")

    processor = FillProcessor()
    fee_writer = FeeStateWriter()

    # Initial wallet snapshot
    processor.record_wallet_snapshot()

    # Initial fee fetch
    log.info("Fetching initial fee tier from Bitfinex...")
    if not fetch_and_update_fees(fee_writer):
        log.warning("Initial fee fetch failed — using conservative defaults")
    fees = fee_writer.read()
    if fees:
        log.info(f"  Maker: {fees['maker_bps']:.1f} bps | Taker: {fees['taker_bps']:.1f} bps")

    cycle = 0
    running = True

    def signal_handler(sig, frame):
        nonlocal running
        running = False
        log.info("Shutting down...")

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    while running:
        try:
            cycle += 1

            # Process fills every 30 seconds
            processor.run_cycle(cycle)

            # Fee refresh every 30 minutes (cycle 60 = 60×30s)
            if cycle % 60 == 0:
                fetch_and_update_fees(fee_writer)

            # Wallet snapshot every hour (cycle 120 = 120×30s = 1 hour)
            if cycle % 120 == 0:
                processor.record_wallet_snapshot()

            # Hourly aggregation
            if cycle % 120 == 0:
                for bot in BOT_INDEX:
                    processor.db.record_hourly(bot)

            # Daily cleanup
            if cycle % (120 * 24) == 0:
                processor.cleanup()

            # Wait 30 seconds between cycles
            for _ in range(30):
                if not running:
                    break
                time.sleep(1)

        except KeyboardInterrupt:
            break
        except Exception as e:
            log.error(f"Cycle error: {e}")
            time.sleep(30)

    log.info("PnL Daemon stopped")
    processor.db.close()
    processor.mmap_writer.close()
    fee_writer.close()


if __name__ == "__main__":
    main()
