#!/usr/bin/env python3
"""
💰 Sniper Armada — HFT Market Data Recorder (M5)
Real-time tick capture via WebSockets to SQLite WAL.
Supports HOT-RELOADING of watched pairs via JSON file!
"""

import asyncio
import json
import logging
import os
import signal
import sys
import time
import websockets
import aiosqlite

ARMADA_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ARMADA_ROOT, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "market_recorder.log")),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger("recorder")

DB_DIR = os.path.expanduser("~/.local/share/sniper")
DB_PATH = os.path.join(DB_DIR, "market_data.db")
WATCHED_PAIRS_FILE = os.path.join(ARMADA_ROOT, "state", "watched_pairs.json")

tick_queue = asyncio.Queue(maxsize=500_000)
running = True

# Dynamic state tracking
current_binance_pairs = []
current_bitfinex_pairs = []

# Events to trigger reconnects if pairs change
reconnect_event = asyncio.Event()

def load_watched_pairs():
    global current_binance_pairs, current_bitfinex_pairs
    changed = False
    try:
        if os.path.exists(WATCHED_PAIRS_FILE):
            with open(WATCHED_PAIRS_FILE, 'r') as f:
                data = json.load(f)
                
                binance_config = data.get("binance", [])
                if set(binance_config) != set(current_binance_pairs):
                    current_binance_pairs = binance_config
                    log.info(f"Loaded {len(binance_config)} Binance pairs")
                    changed = True
                    
                bitfinex_config = data.get("bitfinex", [])
                if set(bitfinex_config) != set(current_bitfinex_pairs):
                    current_bitfinex_pairs = bitfinex_config
                    log.info(f"Loaded {len(bitfinex_config)} Bitfinex pairs")
                    changed = True
                    
                if changed:
                    reconnect_event.set()
    except Exception as e:
        log.error(f"Failed to load watched_pairs.json: {e}")

async def config_watcher():
    """Continuously monitors watched_pairs.json for dynamic updates."""
    last_mtime = 0
    while running:
        try:
            if os.path.exists(WATCHED_PAIRS_FILE):
                mtime = os.path.getmtime(WATCHED_PAIRS_FILE)
                if mtime > last_mtime:
                    load_watched_pairs()
                    last_mtime = mtime
        except Exception as e:
            log.error(f"Config watcher error: {e}")
            
        await asyncio.sleep(5)  # Polling interval

async def init_db():
    os.makedirs(DB_DIR, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA temp_store=MEMORY")
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ticks (
                ts_ms     INTEGER NOT NULL,
                exchange  TEXT NOT NULL,
                symbol    TEXT NOT NULL,
                price     REAL NOT NULL,
                qty       REAL NOT NULL,
                side      TEXT NOT NULL,
                trade_id  TEXT
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ticks_ts ON ticks(ts_ms)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ticks_sym ON ticks(symbol, ts_ms)")
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS candles_1s (
                ts INTEGER NOT NULL, exchange TEXT NOT NULL, symbol TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL, volume REAL, trades INTEGER, vwap REAL,
                PRIMARY KEY (exchange, symbol, ts)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS candles_1m (
                ts INTEGER NOT NULL, exchange TEXT NOT NULL, symbol TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL, volume REAL, trades INTEGER, vwap REAL,
                PRIMARY KEY (exchange, symbol, ts)
            )
        """)
        await db.commit()
    log.info(f"Database initialized at {DB_PATH}")

async def db_writer_loop():
    """Reads from tick_queue and writes to SQLite in bulk."""
    log.info("Starting db_writer_loop")
    batch_size = 5000
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        
        while running or not tick_queue.empty():
            batch = []
            try:
                if not running and tick_queue.empty(): break
                item = await asyncio.wait_for(tick_queue.get(), timeout=1.0)
                batch.append(item)
                tick_queue.task_done()
                
                while len(batch) < batch_size and not tick_queue.empty():
                    item = tick_queue.get_nowait()
                    batch.append(item)
                    tick_queue.task_done()
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                log.error(f"Error reading queue: {e}")
                
            if batch:
                st = time.perf_counter()
                try:
                    await db.executemany(
                        "INSERT INTO ticks (ts_ms, exchange, symbol, price, qty, side, trade_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        batch
                    )
                    await db.commit()
                    dur = time.perf_counter() - st
                    log.debug(f"Inserted {len(batch)} ticks in {dur*1000:.1f}ms (queue: {tick_queue.qsize()})")
                except Exception as e:
                    log.error(f"DB Write error: {e}")

async def binance_ws():
    """Connects to Binance WS and streams trades (with hot-reload)."""
    backoff = 1
    while running:
        if not current_binance_pairs:
            await asyncio.sleep(1)
            continue
            
        streams = "/".join([f"{p}@trade" for p in current_binance_pairs])
        uri = f"wss://stream.binance.com:9443/ws/{streams}"
        
        try:
            log.info(f"[Binance] Connecting with {len(current_binance_pairs)} pairs")
            async with websockets.connect(uri, ping_interval=20, ping_timeout=20, max_size=None) as ws:
                log.info("[Binance] Connected")
                backoff = 1
                
                while running and not reconnect_event.is_set():
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        data = json.loads(msg)
                        
                        if "e" in data and data["e"] == "trade":
                            ts_ms = data["T"]
                            symbol = data["s"]
                            price = float(data["p"])
                            qty = float(data["q"])
                            side = "sell" if data["m"] else "buy"
                            trade_id = str(data["t"])
                            
                            try:
                                tick_queue.put_nowait((ts_ms, "binance", symbol, price, qty, side, trade_id))
                            except asyncio.QueueFull:
                                log.error("Tick Queue FULL, dropping data!")
                    except asyncio.TimeoutError:
                        continue
                        
        except Exception as e:
            if not running: break
            log.warning(f"[Binance] WS Error: {e}. Reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
            
        # Reconnect triggered by config_watcher
        await asyncio.sleep(1)

async def bitfinex_ws():
    """Connects to Bitfinex WS and streams trades (with hot-reload)."""
    uri = "wss://api-pub.bitfinex.com/ws/2"
    backoff = 1
    chan_map = {}
    
    while running:
        if not current_bitfinex_pairs:
            await asyncio.sleep(1)
            continue
            
        try:
            log.info(f"[Bitfinex] Connecting with {len(current_bitfinex_pairs)} pairs")
            async with websockets.connect(uri, ping_interval=20, ping_timeout=20, max_size=None) as ws:
                log.info("[Bitfinex] Connected")
                backoff = 1
                chan_map.clear()
                
                # Subscribe to current pairs
                for pair in current_bitfinex_pairs:
                    await ws.send(json.dumps({"event": "subscribe", "channel": "trades", "symbol": pair}))
                
                while running and not reconnect_event.is_set():
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        data = json.loads(msg)
                        
                        if isinstance(data, dict):
                            if data.get("event") == "subscribed":
                                chan_map[data["chanId"]] = data["symbol"]
                            continue
                        
                        if isinstance(data, list):
                            chan_id = data[0]
                            if chan_id not in chan_map: continue
                            
                            event = data[1]
                            if event in ("tu", "te") and len(data) >= 3:
                                trade = data[2]
                                symbol = chan_map[chan_id]
                                
                                # Bitfinex format: [ trade_id, ts, amount, price ]
                                trade_id = str(trade[0])
                                ts_ms = trade[1]
                                raw_amount = float(trade[2])
                                price = float(trade[3])
                                side = "buy" if raw_amount > 0 else "sell"
                                qty = abs(raw_amount)
                                
                                try:
                                    tick_queue.put_nowait((ts_ms, "bitfinex", symbol, price, qty, side, trade_id))
                                except asyncio.QueueFull:
                                    log.error("Tick Queue FULL, dropping data!")
                    except asyncio.TimeoutError:
                        continue
                        
        except Exception as e:
            if not running: break
            log.warning(f"[Bitfinex] WS Error: {e}. Reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
            
        # Clear the global event if we're the last one to process it
        if reconnect_event.is_set():
            # A bit sloppy, but since both break out, it works well enough.
            # We delay clear slightly to let both pick it up.
            await asyncio.sleep(0.5)
            reconnect_event.clear()

async def monitor():
    """Prints stats."""
    while running:
        await asyncio.sleep(60)
        log.info(f"STATUS: Queue size: {tick_queue.qsize()} | Binance pairs: {len(current_binance_pairs)} | Bitfinex pairs: {len(current_bitfinex_pairs)}")

async def db_prune_loop():
    """Delegates archiving and pruning to data_archiver.py for the 1TB Data Lake."""
    await asyncio.sleep(180)  # Start 3 min after boot to not interfere with ws connects
    while running:
        log.info("Triggering Parquet Archiver Dump for the Data Lake...")
        try:
            archiver_path = os.path.join(ARMADA_ROOT, "architect", "data_archiver.py")
            process = await asyncio.create_subprocess_exec(
                "python3", archiver_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            if process.returncode == 0:
                log.info(f"Archiver finished successfully. Output:\\n{stdout.decode().strip()}")
            else:
                log.error(f"Archiver failed: {stderr.decode()}")
        except Exception as e:
            log.error(f"Failed to spawn archiver: {e}")
            
        # Run and check every 6 hours
        await asyncio.sleep(21600)

def handle_sigint():
    global running
    log.info("Shutdown signal received")
    running = False

async def main():
    load_watched_pairs() # Initial load
    await init_db()
    
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, handle_sigint)
    loop.add_signal_handler(signal.SIGTERM, handle_sigint)
    
    tasks = [
        asyncio.create_task(db_writer_loop()),
        asyncio.create_task(config_watcher()),
        asyncio.create_task(binance_ws()),
        asyncio.create_task(bitfinex_ws()),
        asyncio.create_task(monitor()),
        asyncio.create_task(db_prune_loop())
    ]
    
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
