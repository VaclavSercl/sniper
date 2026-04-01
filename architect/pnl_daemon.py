#!/usr/bin/env python3
"""
💰 Sniper Armada — PnL Daemon v3.0 (Drop Copy Engine)
Reads trade fills via real-time Bitfinex Authenticated WebSocket.
Processes FIFO PnL, writes mmap + SQLite with zero latency.
"""

import os
import sys
import json
import time
import struct
import hashlib
import hmac
import logging
import asyncio
import signal
from collections import defaultdict
from datetime import datetime, timezone

import requests
import websockets

# Add shared to path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "shared"))

from pnl_engine import (
    FIFOEngine, PnlDatabase, PnlMmapWriter,
    BOT_INDEX, format_pnl_short,
)

# ── CONFIG ──────────────────────────────────────────────────
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

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

running = True
# ── BITFINEX REST API (Fallback / Init) ────────────────────

def bfx_authenticated_sync(path, body=None):
    """Synchronous REST v2 request."""
    if not BFX_API_KEY or not BFX_API_SECRET: return None
    nonce = str(int(time.time() * 1_000_000))
    body_json = json.dumps(body) if body else "{}"
    sig_payload = f"/api/{path}{nonce}{body_json}"
    sig = hmac.new(BFX_API_SECRET.encode(), sig_payload.encode(), hashlib.sha384).hexdigest()
    headers = {
        "bfx-nonce": nonce, "bfx-apikey": BFX_API_KEY,
        "bfx-signature": sig, "Content-Type": "application/json",
    }
    try:
        r = requests.post(f"https://api.bitfinex.com/{path}", headers=headers, data=body_json, timeout=15)
        return r.json()
    except Exception as e:
        log.error(f"Bitfinex REST api error: {e}")
        return None

def fetch_wallets_sync():
    """Fetch current wallet balances synchronously."""
    res = bfx_authenticated_sync("v2/auth/r/wallets")
    wallets = {}
    if isinstance(res, list):
        for w in res:
            if isinstance(w, list) and len(w) >= 3 and w[0] == "exchange":
                wallets[w[1]] = float(w[2])
    return wallets

# ── GLOBAL FEE STATE ───────────────────────────────────────
FEE_STATE_PATH = "/dev/shm/beroun/fee_state.bin"

class FeeStateWriter:
    def __init__(self):
        self.mm = None
        try:
            os.makedirs(os.path.dirname(FEE_STATE_PATH), exist_ok=True)
            fd = os.open(FEE_STATE_PATH, os.O_RDWR | os.O_CREAT)
            os.ftruncate(fd, 64)
            import mmap
            self.mm = mmap.mmap(fd, 64)
            os.close(fd)
            if self.mm[:8] == b'\x00' * 8:
                self.write(maker_bps=1000, taker_bps=2000, deriv_maker=200, deriv_taker=650)
            log.info(f"FeeStateWriter: mmap opened at {FEE_STATE_PATH}")
        except Exception as e:
            log.error(f"Fee write init fail: {e}")

    def write(self, maker_bps=None, taker_bps=None, deriv_maker=None, deriv_taker=None, volume_usd=None, tier=None):
        if not self.mm: return
        now_ms = int(time.time() * 1000)
        if maker_bps is not None: struct.pack_into('<Q', self.mm, 0, int(maker_bps))
        if taker_bps is not None: struct.pack_into('<Q', self.mm, 8, int(taker_bps))
        if deriv_maker is not None: struct.pack_into('<Q', self.mm, 16, int(deriv_maker))
        if deriv_taker is not None: struct.pack_into('<Q', self.mm, 24, int(deriv_taker))
        struct.pack_into('<Q', self.mm, 32, now_ms)
        if volume_usd is not None: struct.pack_into('<q', self.mm, 40, int(volume_usd))
        if tier is not None: struct.pack_into('<Q', self.mm, 48, int(tier))
        struct.pack_into('<Q', self.mm, 56, now_ms)
        self.mm.flush()

    def read(self):
        if not self.mm: return {}
        return {"maker_bps": struct.unpack_from('<Q', self.mm, 0)[0] / 100.0,
                "taker_bps": struct.unpack_from('<Q', self.mm, 8)[0] / 100.0}

    def close(self):
        if self.mm: self.mm.close()

def fetch_and_update_fees_sync(fee_writer):
    result = bfx_authenticated_sync("v2/auth/r/summary")
    if result and isinstance(result, list) and len(result) >= 2:
        try:
            vol_30d = int(result[0]) if isinstance(result[0], (int, float)) else 0
            maker_pct, taker_pct = 0.001, 0.002
            if isinstance(result[1], list) and len(result[1]) >= 4:
                maker_pct = abs(float(result[1][0] or maker_pct))
                taker_pct = abs(float(result[1][2] or taker_pct))
            deriv_m, deriv_t = 0.0002, 0.00065
            if len(result) >= 3 and isinstance(result[2], list) and len(result[2]) >= 4:
                deriv_m = abs(float(result[2][0] or deriv_m))
                deriv_t = abs(float(result[2][2] or deriv_t))
            
            fee_writer.write(int(maker_pct * 1e6), int(taker_pct * 1e6), int(deriv_m * 1e6), int(deriv_t * 1e6), vol_30d)
            log.info(f"💰 Fee update: maker={maker_pct*100:.3f}% taker={taker_pct*100:.3f}% vol=${vol_30d:,.0f}")
            return True
        except Exception as e:
            log.error(f"Fee parse err: {e}")
    return False

# ── FILL PROCESSOR ──────────────────────────────────────────
class FillProcessor:
    GID_RANGES = [
        (1000, 1999, "hydra"),
        (2000, 2999, "moonshot"),
        (3000, 3999, "grid"),
        (4000, 4999, "trigon"),
    ]

    def __init__(self):
        self.db = PnlDatabase()
        self.mmap_writer = PnlMmapWriter()
        self.engines = {}
        self.last_trade_ids = defaultdict(set)
        self.order_gid_cache = {}
        self._load_all_engines()
        self._refresh_order_gid_cache_sync()

    def _load_all_engines(self):
        for bot in BOT_INDEX:
            for symbol in self._get_symbols(bot):
                self.engines[(bot, symbol)] = self.db.load_fifo_state(bot, symbol)

    def _get_symbols(self, bot):
        return {"trigon": ["tBTCUSD", "tETHUSD", "tETHBTC"]}.get(bot, ["tBTCUSD"])

    def _gid_to_bot(self, gid):
        for lo, hi, bot in self.GID_RANGES:
            if lo <= gid <= hi: return bot
        return "hydra" # Default fallback for orphan trades

    def _resolve_bot(self, order_id):
        return self.order_gid_cache.get(order_id)

    def _refresh_order_gid_cache_sync(self):
        orders = bfx_authenticated_sync("v2/auth/r/orders/hist", {"limit": 500})
        if orders and isinstance(orders, list):
            for o in orders:
                if len(o) >= 4 and o[1] is not None:
                    self.order_gid_cache[str(o[0])] = self._gid_to_bot(int(o[1]))
        if len(self.order_gid_cache) > 10000:
            keys = sorted(self.order_gid_cache.keys())
            self.order_gid_cache = {k: self.order_gid_cache[k] for k in keys[-5000:]}

    def process_historical_backlog(self):
        """Fetch REST history to fill any gap before WS connection."""
        for bot in BOT_INDEX:
            for symbol in self._get_symbols(bot):
                trades = bfx_authenticated_sync("v2/auth/r/trades/hist", {"symbol": symbol, "limit": 100})
                if trades and isinstance(trades, list):
                    for trade in reversed(trades):
                        self.process_single_trade_payload(trade)
                
    def process_single_trade_payload(self, trade):
        """Processes a single `tu` websocket trade or historical trade payload."""
        if not isinstance(trade, list) or len(trade) < 11: return
        trade_id = str(trade[0])
        symbol = trade[1]
        
        # Determine bot
        order_id = str(trade[3])
        bot = self._resolve_bot(order_id)
        if not bot:
            # We must assume hydra if we completely lost GID cache, but usually WS `ou` will seed it
            bot = "hydra"
            
        if trade_id in self.last_trade_ids[bot]: return
        self.last_trade_ids[bot].add(trade_id)

        ts_ms = int(trade[2])
        exec_amount = float(trade[4])
        exec_price = float(trade[5])
        fee = abs(float(trade[9]))
        fee_currency = trade[10] if len(trade) > 10 else "USD"

        side = "buy" if exec_amount > 0 else "sell"
        qty = abs(exec_amount)

        fee_usd = fee
        if fee_currency == "BTC": fee_usd = fee * self._get_btc_usd_spot()
        elif fee_currency == "ETH": fee_usd = fee * 2000.0

        key = (bot, symbol)
        if key not in self.engines: self.engines[key] = FIFOEngine(bot, symbol)
        
        usd_spot = self._get_btc_usd_spot() if symbol != "tBTCUSD" else 1.0
        
        result = self.engines[key].on_fill(side, qty, exec_price, fee_usd, ts_ms, usd_spot)
        ts_iso = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()
        
        self.db.insert_fill(
            bot=bot, symbol=symbol, side=side, qty=qty, price=exec_price,
            fee=fee_usd, fee_currency="USD", trade_id=trade_id, order_id=order_id,
            ts=ts_iso, ts_ms=ts_ms, is_closer=result["is_closer"],
            matched_price=result.get("matched_entry_price", 0),
            gross_pnl=result.get("gross_pnl", 0), net_pnl=result.get("net_pnl", 0),
            numeraire_rate=usd_spot
        )
        self.db.save_fifo_state(self.engines[key])
        
        log.info(f"💥 ZERO-LATENCY FILL: {bot}/{symbol} {side.upper()} {qty:.6f} @ ${exec_price:.2f} " +
                 (f"→ PnL: {format_pnl_short(result['net_pnl'])}" if result["is_closer"] else "(Open)"))
        self.update_mmap()
        
        if len(self.last_trade_ids[bot]) > 5000:
            self.last_trade_ids[bot] = set(sorted(self.last_trade_ids[bot])[-2000:])

    def _get_btc_usd_spot(self):
        try:
            if os.path.exists("/dev/shm/beroun/mdf.bin"):
                with open("/dev/shm/beroun/mdf.bin", "rb") as f:
                    bid, ask = struct.unpack_from("<dd", f.read(16), 0)
                    if bid > 0 and ask > 0: return (bid + ask) / 2
        except: pass
        return 66000.0

    def update_mmap(self):
        windows = {"1h": 1, "24h": 24, "7d": 168, "30d": 720}
        totals = defaultdict(float)
        total_fills_24h = 0
        for bot, idx in BOT_INDEX.items():
            if idx >= 4: continue
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
                if label == "24h": totals["total_fees_24h"] += w["fees"]

            key = (bot, "tBTCUSD")
            if key in self.engines:
                engine = self.engines[key]
                bot_data["total_closed"] = engine.total_closed
                bot_data["net_position"] = engine.net_position
                bot_data["fifo_front_price"] = engine.fifo_front_price
                bot_data["last_fill_ms"] = int(time.time() * 1000)

                ct = bot_data.get("closed_trades_24h", 0)
                bot_data["avg_pnl_per_trade"] = bot_data.get("realized_24h", 0) / ct if ct > 0 else 0
                bot_data["avg_fee_per_fill"] = bot_data.get("fees_24h", 0) / max(bot_data.get("fills_24h", 0), 1)
            self.mmap_writer.write_bot(idx, bot_data)

        totals["total_fills_24h"] = total_fills_24h
        self.mmap_writer.write_global(totals)

    def record_wallet_snapshot_sync(self):
        w = fetch_wallets_sync()
        if w:
            usd, btc = w.get("USD", 0), w.get("BTC", 0)
            btc_price = self._get_btc_usd_spot()
            self.db.record_wallet(usd, btc, btc_price)
            delta_1h, delta_24h = self.db.get_wallet_delta(1), self.db.get_wallet_delta(24)
            self.mmap_writer.write_global({
                "wallet_usd": usd, "wallet_btc": btc, "wallet_total_usd": usd + btc*btc_price,
                "wallet_delta_1h": delta_1h, "wallet_delta_24h": delta_24h,
            })
            log.info(f"Wallet Total=${usd + btc*btc_price:.2f} Δ24h={format_pnl_short(delta_24h)}")

# ── ASYNC BACKGROUND TASKS ─────────────────────────────────

async def bitfinex_drop_copy_ws(processor):
    """Real-time WS Drop Copy connection"""
    uri = "wss://api.bitfinex.com/ws/2"
    backoff = 1
    
    while running:
        if not BFX_API_KEY or not BFX_API_SECRET:
            log.warning("Drop Copy: Missing API keys in .env! Halting WS daemon.")
            await asyncio.sleep(60)
            continue
            
        try:
            log.info(f"[DropCopy] Authenticating with WebSocket {uri}")
            async with websockets.connect(uri, ping_interval=20, ping_timeout=20) as ws:
                nonce = str(int(time.time() * 1_000_000))
                auth_payload = f"AUTH{nonce}"
                auth_sig = hmac.new(BFX_API_SECRET.encode(), auth_payload.encode(), hashlib.sha384).hexdigest()
                
                await ws.send(json.dumps({
                    "event": "auth",
                    "apiKey": BFX_API_KEY,
                    "authPayload": auth_payload,
                    "authNonce": nonce,
                    "authSig": auth_sig,
                    "filter": ["trading"]
                }))
                
                backoff = 1
                while running:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    except asyncio.TimeoutError:
                        continue  # No message in 30s — stay connected, just retry
                    data = json.loads(msg)
                    
                    if isinstance(data, dict):
                        if data.get("event") == "auth":
                            if data.get("status") == "OK":
                                log.info("[DropCopy] ✅ Authenticated successfully. Listening for trades...")
                            else:
                                log.error(f"[DropCopy] Auth failed: {data}")
                                break
                        continue
                        
                    if isinstance(data, list) and len(data) >= 3:
                        msg_type = data[1]
                        
                        # Catch Order Updates (ou, on) to map IDs to Bots
                        if msg_type in ("on", "ou"):
                            o = data[2]
                            order_id = str(o[0])
                            gid = o[1]
                            if gid is not None:
                                bot = processor._gid_to_bot(int(gid))
                                processor.order_gid_cache[order_id] = bot
                                
                        # Process Trade Updated (tu)
                        # We use 'tu' because it includes the resolved fee + fee_currency
                        elif msg_type == "tu":
                            trade_payload = data[2]
                            # Run synchronously because our processing logic uses fast SQLite WAL
                            processor.process_single_trade_payload(trade_payload)
                            
        except Exception as e:
            if not running: break
            log.warning(f"[DropCopy] WS Error: {e}. Reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

async def periodic_jobs_loop(processor, fee_writer):
    """Runs periodic slow REST tasks via asyncio.to_thread"""
    cycle = 0
    while running:
        await asyncio.sleep(30)
        cycle += 1
        
        try:
            # Every 5 min: ensure GID cache has safety net
            if cycle % 10 == 0:
                await asyncio.to_thread(processor._refresh_order_gid_cache_sync)
                
            # Every 30 min: fee check
            if cycle % 60 == 0:
                await asyncio.to_thread(fetch_and_update_fees_sync, fee_writer)
                
            # Every 1 hr: wallet snapshot and hourly summary
            if cycle % 120 == 0:
                await asyncio.to_thread(processor.record_wallet_snapshot_sync)
                for bot in BOT_INDEX:
                    processor.db.record_hourly(bot)
                    
            # Every 24 hr: db cleanup
            if cycle % 2880 == 0:
                processor.db.cleanup_old(days=90)
                
        except Exception as e:
            log.error(f"Periodic loop error: {e}")

# ══════════════════════════════════════════════════════════
def main():
    log.info("💰 Sovereign PnL Engine (M6 Drop Copy Daemon) Started")
    
    import fcntl
    lock_file = open("/tmp/pnl_daemon.lock", "w")
    try: fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.error("Another pnl_daemon is running. Aborting.")
        sys.exit(1)
        
    processor = FillProcessor()
    fee_writer = FeeStateWriter()
    
    # Init tasks
    processor.record_wallet_snapshot_sync()
    fetch_and_update_fees_sync(fee_writer)
    processor.process_historical_backlog() # fetch any fills that happened while we were offline
    processor.update_mmap()
    
    def handle_sig(sig, frame):
        global running
        running = False
        log.info("Shutting down Drop Copy Engine...")
        
    signal.signal(signal.SIGTERM, handle_sig)
    signal.signal(signal.SIGINT, handle_sig)
    
    async def async_main():
        tasks = [
            asyncio.create_task(bitfinex_drop_copy_ws(processor)),
            asyncio.create_task(periodic_jobs_loop(processor, fee_writer))
        ]
        await asyncio.gather(*tasks)
        
    asyncio.run(async_main())
    
    processor.db.close()
    processor.mmap_writer.close()
    fee_writer.close()

if __name__ == "__main__":
    main()
