#!/usr/bin/env python3
"""
💰 Sniper Armada — HFT Market Data Downsampler (M5)
Periodically aggregates raw ticks into 1s and 1m OHLCV candles,
and archives old ticks (>33d) to Parquet.
"""

import sqlite3
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import argparse
import os
import sys
import logging
import time
from datetime import datetime, timedelta, timezone

LOG_DIR = os.path.expanduser("~/.local/share/sniper/logs")
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "market_downsampler.log")),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger("downsampler")

DB_PATH = os.path.expanduser("~/.local/share/sniper/market_data.db")
ARCHIVE_DIR = "/data/sniper/archive/ticks"

def get_db_connection():
    if not os.path.exists(DB_PATH):
        log.error(f"Cannot find DB at {DB_PATH}")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def aggregate_candles(interval_sec: int, table_name: str, lookback_sec: int = 120):
    """
    Reads recent ticks, resamples down to `interval_sec`, and UPSERTs into `table_name`.
    Uses pandas for high-performance vectorized operations.
    """
    conn = get_db_connection()
    now_ms = int(time.time() * 1000)
    lookback_ms = now_ms - (lookback_sec * 1000)
    
    log.info(f"Aggregating ticks into {table_name} (interval={interval_sec}s, lookback={lookback_sec}s)...")
    
    df = pd.read_sql_query(
        "SELECT ts_ms, exchange, symbol, price, qty, trade_id FROM ticks WHERE ts_ms >= ?",
        conn, params=(lookback_ms,)
    )
    
    if df.empty:
        log.info("No ticks found to aggregate.")
        conn.close()
        return

    # Convert ts_ms to proper datetime for Pandas resampling
    df['datetime'] = pd.to_datetime(df['ts_ms'], unit='ms')
    df.set_index('datetime', inplace=True)
    
    # Calculate volume * price for VWAP
    df['vol_price'] = df['price'] * df['qty']
    
    # We group by exchange and symbol, then resample
    freq = f"{interval_sec}s"
    
    upsert_data = []

    for (exchange, symbol), group in df.groupby(['exchange', 'symbol']):
        # Resample the group
        resampled = group.resample(freq).agg(
            open=('price', 'first'),
            high=('price', 'max'),
            low=('price', 'min'),
            close=('price', 'last'),
            volume=('qty', 'sum'),
            trades=('trade_id', 'count'),
            vol_price_sum=('vol_price', 'sum')
        )
        
        # Drop empty intervals
        resampled.dropna(subset=['open'], inplace=True)
        
        # Calculate VWAP
        resampled['vwap'] = resampled['vol_price_sum'] / resampled['volume']
        
        # Extract epoch seconds from the resampled index
        resampled['ts'] = resampled.index.astype('int64') // 10**9
        
        for idx, row in resampled.iterrows():
            upsert_data.append((
                int(row['ts']), exchange, symbol,
                float(row['open']), float(row['high']), float(row['low']), float(row['close']),
                float(row['volume']), int(row['trades']), float(row['vwap'])
            ))
            
    if upsert_data:
        cursor = conn.cursor()
        cursor.execute("BEGIN TRANSACTION")
        cursor.executemany(f"""
            INSERT INTO {table_name} (ts, exchange, symbol, open, high, low, close, volume, trades, vwap)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(exchange, symbol, ts) DO UPDATE SET
                open=excluded.open,
                high=excluded.high,
                low=excluded.low,
                close=excluded.close,
                volume=excluded.volume,
                trades=excluded.trades,
                vwap=excluded.vwap
        """, upsert_data)
        cursor.execute("COMMIT")
        log.info(f"Upserted {len(upsert_data)} {interval_sec}s candles into {table_name}")
    
    conn.close()

def export_and_cleanup(days_retention: int = 33):
    """
    Exports ticks older than `days_retention` to Parquet format, 
    and deletes them from the SQLite database.
    """
    conn = get_db_connection()
    now_ms = int(time.time() * 1000)
    cutoff_ms = now_ms - (days_retention * 24 * 3600 * 1000)
    
    log.info(f"Looking for ticks older than {days_retention} days (cutoff={cutoff_ms})...")
    
    df = pd.read_sql_query(
        "SELECT * FROM ticks WHERE ts_ms < ?",
        conn, params=(cutoff_ms,)
    )
    
    if df.empty:
        log.info("No old data to export.")
        conn.close()
        return
        
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    
    # Split export by day
    df['datetime'] = pd.to_datetime(df['ts_ms'], unit='ms')
    df['day_str'] = df['datetime'].dt.strftime('%Y-%m-%d')
    
    for day, group in df.groupby('day_str'):
        outfile = os.path.join(ARCHIVE_DIR, f"{day}.parquet")
        
        # Drop temporary columns we used for grouping
        export_df = group.drop(columns=['datetime', 'day_str'])
        
        table = pa.Table.from_pandas(export_df)
        
        log.info(f"Exporting {len(export_df)} ticks to {outfile}...")
        
        if os.path.exists(outfile):
            # Read existing, append, write back
            try:
                existing_table = pq.read_table(outfile)
                combined = pa.concat_tables([existing_table, table])
                pq.write_table(combined, outfile, compression='snappy')
            except Exception as e:
                log.error(f"Error appending to {outfile}: {e}")
        else:
            pq.write_table(table, outfile, compression='snappy')
            
    # Delete exported rows from DB
    log.info("Deleting exported ticks from SQLite...")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM ticks WHERE ts_ms < ?", (cutoff_ms,))
    deleted = cursor.rowcount
    
    # Optionally vacuum if a lot was deleted
    if deleted > 100_000:
        log.info("VACUUMing SQLite database...")
        cursor.execute("VACUUM")
        
    conn.close()
    log.info(f"Export and cleanup completed. Deleted {deleted} rows.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Market Data Downsampler")
    parser.add_argument("--aggregate-1s", action="store_true", help="Aggregate recent ticks into 1s candles")
    parser.add_argument("--aggregate-1m", action="store_true", help="Aggregate recent ticks into 1m candles")
    parser.add_argument("--export-parquet", action="store_true", help="Export and delete old ticks")
    parser.add_argument("--cleanup-33d", action="store_true", help="Retention parameter (used with --export)")
    
    args = parser.parse_args()
    
    if args.aggregate_1s:
        # Look back slightly longer than 1 min to ensure overlaps are caught
        aggregate_candles(interval_sec=1, table_name="candles_1s", lookback_sec=120)
        
    if args.aggregate_1m:
        # Look back slightly longer than 1 hour to ensure overlaps are caught
        aggregate_candles(interval_sec=60, table_name="candles_1m", lookback_sec=3600)
        
    if args.export_parquet:
        days = 33 if args.cleanup_33d else 33
        export_and_cleanup(days_retention=days)
        
    if not (args.aggregate_1s or args.aggregate_1m or args.export_parquet):
        parser.print_help()
