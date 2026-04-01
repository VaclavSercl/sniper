#!/usr/bin/env python3
"""
📊 Sniper Armada — Historical Data Ingester (SBP v3.1 Phase 1)

Fetches 8 days of historical data from Bitfinex + Binance REST APIs.
Walk-Forward Split: Days 1-6 = In-Sample, Days 7-8 = Out-of-Sample.

Data stored in:
  - market_data.db (candles_1m) for L2 Oracle (Gemini macro context)
  - /tmp/sniper_ticks_{exchange}_{symbol}.parquet for L1 counterfactual backtest

Usage:
  python3 historical_ingester.py                # Fetch all defaults
  python3 historical_ingester.py --days 8       # Custom lookback
  python3 historical_ingester.py --symbol tBTCUSD --exchange bitfinex
"""

import os
import sys
import json
import time
import sqlite3
import logging
import argparse
from datetime import datetime, timezone, timedelta

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

DB_DIR = os.path.expanduser("~/.local/share/sniper")
DB_PATH = os.path.join(DB_DIR, "market_data.db")
os.makedirs(DB_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [INGEST] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(LOG_DIR, "historical_ingester.log")),
    ],
)
log = logging.getLogger("ingester")

# ═══════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════

DEFAULT_LOOKBACK_DAYS = 8

# Walk-Forward Split (SBP v3.1: Strict anti-leakage)
IN_SAMPLE_DAYS = 6   # Days 1-6: AI Parameter Tuning (Phase 3)
OOS_DAYS = 2          # Days 7-8: Validation (Phase 2)

# Symbols to fetch
BITFINEX_SYMBOLS = ["tBTCUSD"]
BINANCE_SYMBOLS = ["BTCUSDT"]

# API rate limiting
BFX_RATE_LIMIT_S = 1.5   # Bitfinex: max ~30 req/min
BNB_RATE_LIMIT_S = 0.2   # Binance: generous limits

# ═══════════════════════════════════════════════════════════
# Database
# ═══════════════════════════════════════════════════════════

def init_db():
    """Initialize SQLite WAL database for candles."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS candles_1m (
            ts INTEGER NOT NULL,
            exchange TEXT NOT NULL,
            symbol TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL,
            volume REAL, trades INTEGER, vwap REAL,
            PRIMARY KEY (exchange, symbol, ts)
        )
    """)
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS historical_trades (
            ts_ms INTEGER NOT NULL,
            exchange TEXT NOT NULL,
            symbol TEXT NOT NULL,
            price REAL NOT NULL,
            qty REAL NOT NULL,
            side TEXT NOT NULL,
            trade_id TEXT,
            PRIMARY KEY (exchange, symbol, trade_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_htrades_ts ON historical_trades(exchange, symbol, ts_ms)")
    
    conn.commit()
    return conn

# ═══════════════════════════════════════════════════════════
# Bitfinex REST API
# ═══════════════════════════════════════════════════════════

def fetch_bitfinex_candles(symbol="tBTCUSD", timeframe="1m", days=8):
    """Fetch historical 1m candles from Bitfinex public API.
    
    Bitfinex /v2/candles endpoint returns up to 10000 candles per request.
    8 days × 1440 min = 11,520 candles → needs 2 requests.
    """
    log.info(f"📊 [BFX] Fetching {days}d of {timeframe} candles for {symbol}...")
    
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - (days * 86400 * 1000)
    
    all_candles = []
    cursor_start = start_ms
    
    while cursor_start < end_ms:
        url = (
            f"https://api-pub.bitfinex.com/v2/candles/trade:{timeframe}:{symbol}/hist"
            f"?start={cursor_start}&end={end_ms}&limit=10000&sort=1"
        )
        
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            
            if not data:
                break
            
            all_candles.extend(data)
            log.info(f"  [BFX] Fetched {len(data)} candles (total: {len(all_candles)})")
            
            # Move cursor past last candle
            last_ts = data[-1][0]
            cursor_start = last_ts + 60000  # +1 minute
            
            time.sleep(BFX_RATE_LIMIT_S)
        except Exception as e:
            log.error(f"  [BFX] Candle fetch error: {e}")
            time.sleep(5)
            break
    
    log.info(f"✅ [BFX] Total candles fetched: {len(all_candles)}")
    return all_candles


def fetch_bitfinex_trades(symbol="tBTCUSD", days=8):
    """Fetch historical trades from Bitfinex public API.
    
    Bitfinex /v2/trades endpoint returns up to 10000 trades per request.
    8 days of BTC/USD trades can be millions — we paginate aggressively.
    """
    log.info(f"📊 [BFX] Fetching {days}d of trades for {symbol}...")
    
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - (days * 86400 * 1000)
    
    all_trades = []
    cursor_start = start_ms
    batch = 0
    MAX_TRADES = 2_000_000  # Safety cap
    backoff_s = BFX_RATE_LIMIT_S
    consecutive_errors = 0
    
    while cursor_start < end_ms and len(all_trades) < MAX_TRADES:
        url = (
            f"https://api-pub.bitfinex.com/v2/trades/{symbol}/hist"
            f"?start={cursor_start}&end={end_ms}&limit=10000&sort=1"
        )
        
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code == 429:
                wait = min(backoff_s * 2, 120)
                backoff_s = wait
                log.warning(f"  [BFX] Rate limited (429). Waiting {wait}s...")
                time.sleep(wait)
                consecutive_errors += 1
                if consecutive_errors > 10:
                    log.error(f"  [BFX] 10 consecutive 429s. Stopping trade fetch.")
                    break
                continue
            resp.raise_for_status()
            data = resp.json()
            consecutive_errors = 0
            backoff_s = BFX_RATE_LIMIT_S
            
            if not data:
                break
            
            all_trades.extend(data)
            batch += 1
            
            if batch % 10 == 0:
                log.info(f"  [BFX] Trades batch {batch}: {len(all_trades):,} total")
            
            # Move cursor past last trade
            last_ts = data[-1][1]  # trades: [ID, MTS, AMOUNT, PRICE]
            cursor_start = last_ts + 1
            
            time.sleep(BFX_RATE_LIMIT_S)
        except Exception as e:
            log.error(f"  [BFX] Trade fetch error at batch {batch}: {e}")
            time.sleep(5)
            continue
    
    log.info(f"✅ [BFX] Total trades fetched: {len(all_trades):,}")
    return all_trades


# ═══════════════════════════════════════════════════════════
# Binance REST API
# ═══════════════════════════════════════════════════════════

def fetch_binance_candles(symbol="BTCUSDT", interval="1m", days=8):
    """Fetch historical 1m candles from Binance public API.
    
    Binance /api/v3/klines returns up to 1000 per request.
    8 days × 1440 = 11,520 → needs ~12 requests.
    """
    log.info(f"📊 [BNB] Fetching {days}d of {interval} candles for {symbol}...")
    
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - (days * 86400 * 1000)
    
    all_candles = []
    cursor_start = start_ms
    
    while cursor_start < end_ms:
        url = (
            f"https://api.binance.com/api/v3/klines"
            f"?symbol={symbol}&interval={interval}&startTime={cursor_start}"
            f"&endTime={end_ms}&limit=1000"
        )
        
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            
            if not data:
                break
            
            all_candles.extend(data)
            log.info(f"  [BNB] Fetched {len(data)} candles (total: {len(all_candles)})")
            
            # Move cursor past last candle
            last_ts = data[-1][0]
            cursor_start = last_ts + 60000
            
            time.sleep(BNB_RATE_LIMIT_S)
        except Exception as e:
            log.error(f"  [BNB] Candle fetch error: {e}")
            time.sleep(5)
            break
    
    log.info(f"✅ [BNB] Total candles fetched: {len(all_candles)}")
    return all_candles


def fetch_binance_trades(symbol="BTCUSDT", days=8):
    """Fetch historical trades from Binance public API.
    
    Binance /api/v3/aggTrades returns up to 1000 per request.
    Uses startTime/endTime pagination.
    """
    log.info(f"📊 [BNB] Fetching {days}d of trades for {symbol}...")
    
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - (days * 86400 * 1000)
    
    all_trades = []
    cursor_start = start_ms
    batch = 0
    MAX_TRADES = 2_000_000
    
    # Binance aggTrades: max 1h window per request when using startTime/endTime
    HOUR_MS = 3600 * 1000
    
    while cursor_start < end_ms and len(all_trades) < MAX_TRADES:
        chunk_end = min(cursor_start + HOUR_MS, end_ms)
        url = (
            f"https://api.binance.com/api/v3/aggTrades"
            f"?symbol={symbol}&startTime={cursor_start}&endTime={chunk_end}&limit=1000"
        )
        
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            
            if data:
                all_trades.extend(data)
            
            batch += 1
            if batch % 50 == 0:
                log.info(f"  [BNB] Trades batch {batch}: {len(all_trades):,} total")
            
            cursor_start = chunk_end + 1
            time.sleep(BNB_RATE_LIMIT_S)
        except Exception as e:
            log.error(f"  [BNB] Trade fetch error at batch {batch}: {e}")
            time.sleep(2)
            cursor_start = chunk_end + 1
            continue
    
    log.info(f"✅ [BNB] Total trades fetched: {len(all_trades):,}")
    return all_trades


# ═══════════════════════════════════════════════════════════
# Data Storage
# ═══════════════════════════════════════════════════════════

def store_bitfinex_candles(conn, candles, symbol):
    """Store BFX candles. Format: [MTS, OPEN, CLOSE, HIGH, LOW, VOLUME]"""
    rows = []
    for c in candles:
        if len(c) >= 6:
            rows.append((c[0], "bitfinex", symbol, c[1], c[3], c[4], c[2], c[5], 0, 0))
    
    conn.executemany(
        "INSERT OR IGNORE INTO candles_1m (ts, exchange, symbol, open, high, low, close, volume, trades, vwap) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows
    )
    conn.commit()
    log.info(f"💾 Stored {len(rows)} BFX candles for {symbol}")


def store_binance_candles(conn, candles, symbol):
    """Store BNB candles. Format: [OpenTime, O, H, L, C, Vol, CloseTime, ...]"""
    rows = []
    for c in candles:
        if len(c) >= 6:
            rows.append((c[0], "binance", symbol, float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5]), int(c[8]) if len(c) > 8 else 0, 0))
    
    conn.executemany(
        "INSERT OR IGNORE INTO candles_1m (ts, exchange, symbol, open, high, low, close, volume, trades, vwap) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows
    )
    conn.commit()
    log.info(f"💾 Stored {len(rows)} BNB candles for {symbol}")


def store_bitfinex_trades(conn, trades, symbol):
    """Store BFX trades. Format: [ID, MTS, AMOUNT, PRICE]"""
    rows = []
    for t in trades:
        if len(t) >= 4:
            side = "buy" if t[2] > 0 else "sell"
            rows.append((t[1], "bitfinex", symbol, float(t[3]), abs(float(t[2])), side, str(t[0])))
    
    conn.executemany(
        "INSERT OR IGNORE INTO historical_trades (ts_ms, exchange, symbol, price, qty, side, trade_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows
    )
    conn.commit()
    log.info(f"💾 Stored {len(rows)} BFX trades for {symbol}")


def store_binance_trades(conn, trades, symbol):
    """Store BNB aggTrades. Format: {a, p, q, f, l, T, m, M}"""
    rows = []
    for t in trades:
        side = "sell" if t.get("m") else "buy"
        rows.append((t["T"], "binance", symbol, float(t["p"]), float(t["q"]), side, str(t["a"])))
    
    conn.executemany(
        "INSERT OR IGNORE INTO historical_trades (ts_ms, exchange, symbol, price, qty, side, trade_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows
    )
    conn.commit()
    log.info(f"💾 Stored {len(rows)} BNB trades for {symbol}")


# ═══════════════════════════════════════════════════════════
# Walk-Forward Split Metadata
# ═══════════════════════════════════════════════════════════

def compute_walk_forward_split(days=8):
    """Compute Walk-Forward split timestamps.
    
    SBP v3.1: Strict anti-leakage.
    In-Sample (Days 1-6): Phase 3 AI tuning
    Out-of-Sample (Days 7-8): Phase 2 validation (never seen during tuning)
    
    Returns dict with timestamps for each partition.
    """
    now = datetime.now(timezone.utc)
    
    total_start = now - timedelta(days=days)
    oos_start = now - timedelta(days=OOS_DAYS)
    
    split = {
        "total_start_ms": int(total_start.timestamp() * 1000),
        "is_start_ms": int(total_start.timestamp() * 1000),         # Day 1
        "is_end_ms": int(oos_start.timestamp() * 1000),             # Day 6 end
        "oos_start_ms": int(oos_start.timestamp() * 1000),          # Day 7
        "oos_end_ms": int(now.timestamp() * 1000),                  # Day 8 (now)
        "is_days": IN_SAMPLE_DAYS,
        "oos_days": OOS_DAYS,
    }
    
    log.info(f"📐 Walk-Forward Split:")
    log.info(f"  In-Sample:  {total_start:%Y-%m-%d %H:%M} → {oos_start:%Y-%m-%d %H:%M} ({IN_SAMPLE_DAYS}d)")
    log.info(f"  OOS:        {oos_start:%Y-%m-%d %H:%M} → {now:%Y-%m-%d %H:%M} ({OOS_DAYS}d)")
    
    # Save split metadata for Phase 2/3 consumers
    split_path = os.path.join(PROJECT_ROOT, "state", "wf_split.json")
    os.makedirs(os.path.dirname(split_path), exist_ok=True)
    with open(split_path, "w") as f:
        json.dump(split, f, indent=2)
    log.info(f"  Saved split metadata → {split_path}")
    
    return split


def get_data_summary(conn):
    """Return summary of ingested data."""
    summary = {}
    for tbl in ["candles_1m", "historical_trades"]:
        try:
            count = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            if count > 0:
                col = "ts" if tbl == "candles_1m" else "ts_ms"
                mn, mx = conn.execute(f"SELECT MIN({col}), MAX({col}) FROM {tbl}").fetchone()
                days = (mx - mn) / (1000 * 86400) if mn and mx else 0
                summary[tbl] = {"count": count, "days": round(days, 1)}
            else:
                summary[tbl] = {"count": 0, "days": 0}
        except Exception:
            summary[tbl] = {"count": 0, "days": 0}
    return summary


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════

def run_ingestion(days=DEFAULT_LOOKBACK_DAYS, fetch_trades=True):
    """Run full historical data ingestion pipeline.
    
    Returns True if sufficient data was ingested for SBP Phase 2/3.
    """
    log.info(f"📊 ═══ HISTORICAL INGESTER v1.0 (SBP v3.1 Phase 1) ═══")
    log.info(f"   Lookback: {days} days")
    log.info(f"   Walk-Forward: {IN_SAMPLE_DAYS}d IS + {OOS_DAYS}d OOS")
    
    conn = init_db()
    split = compute_walk_forward_split(days)
    
    start_time = time.time()
    
    # ── 1m Candles (for L2 Oracle / Gemini macro context) ──
    for sym in BITFINEX_SYMBOLS:
        candles = fetch_bitfinex_candles(sym, "1m", days)
        if candles:
            store_bitfinex_candles(conn, candles, sym)
    
    for sym in BINANCE_SYMBOLS:
        candles = fetch_binance_candles(sym, "1m", days)
        if candles:
            store_binance_candles(conn, candles, sym)
    
    # ── Raw Trades (for L1 counterfactual backtest — tick-level precision) ──
    if fetch_trades:
        for sym in BITFINEX_SYMBOLS:
            trades = fetch_bitfinex_trades(sym, days)
            if trades:
                store_bitfinex_trades(conn, trades, sym)
        
        for sym in BINANCE_SYMBOLS:
            trades = fetch_binance_trades(sym, days)
            if trades:
                store_binance_trades(conn, trades, sym)
    
    elapsed = time.time() - start_time
    summary = get_data_summary(conn)
    
    log.info(f"")
    log.info(f"═══ INGESTION COMPLETE ({elapsed:.1f}s) ═══")
    for tbl, info in summary.items():
        log.info(f"  {tbl}: {info['count']:,} rows ({info['days']}d)")
    
    # Validate: enough data for SBP?
    candle_count = summary.get("candles_1m", {}).get("count", 0)
    has_enough = candle_count >= 5000  # ~3.5 days minimum
    
    if has_enough:
        log.info(f"✅ Sufficient data for SBP Phase 2/3")
    else:
        log.warning(f"⚠️ Insufficient data ({candle_count} candles). SBP may bypass backtest.")
    
    conn.close()
    return has_enough


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SBP v3.1 Historical Data Ingester")
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS, help="Lookback days (default: 8)")
    parser.add_argument("--no-trades", action="store_true", help="Skip trade-level data (candles only)")
    args = parser.parse_args()
    
    run_ingestion(days=args.days, fetch_trades=not args.no_trades)
