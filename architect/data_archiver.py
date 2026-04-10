#!/usr/bin/env python3
"""
🌊 Sniper Armada — Data Lake Archiver
Converts SQLite historical data into Apache Parquet format and moves it
to the 1TB Data Lake (/data/sniper_lake).
Prunes the old data from SQLite only AFTER successful export.
"""
import os
import time
import logging
import sqlite3
import pandas as pd
from datetime import datetime, timezone

LAKE_DIR = "/data/sniper_lake"
DB_PATH = os.path.expanduser("~/.local/share/sniper/market_data.db")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("archiver")

def archive_and_prune():
    os.makedirs(os.path.join(LAKE_DIR, "ticks"), exist_ok=True)
    os.makedirs(os.path.join(LAKE_DIR, "candles_1s"), exist_ok=True)
    os.makedirs(os.path.join(LAKE_DIR, "candles_1m"), exist_ok=True)
    
    # 25 hours ago as the cutoff point for Ticks and 1s Candles
    threshold_ms = int(time.time() * 1000) - (25 * 3600 * 1000)
    cutoff_dt = datetime.fromtimestamp(threshold_ms / 1000, tz=timezone.utc)
    file_prefix = cutoff_dt.strftime("%Y-%m-%d_%H%M%S")
    
    try:
        conn = sqlite3.connect(DB_PATH)
        success = True
        
        # --- 1. Ticks ---
        log.info(f"Querying ticks older than {cutoff_dt}...")
        df_ticks = pd.read_sql_query(f"SELECT * FROM ticks WHERE ts_ms < {threshold_ms}", conn)
        if not df_ticks.empty:
            out_file = os.path.join(LAKE_DIR, "ticks", f"{file_prefix}_ticks.parquet")
            df_ticks.to_parquet(out_file, engine='pyarrow', compression='snappy')
            log.info(f"✅ Archived {len(df_ticks)} ticks to {out_file}")
            
            # Prune AFTER safe export
            cur = conn.cursor()
            cur.execute(f"DELETE FROM ticks WHERE ts_ms < {threshold_ms}")
            conn.commit()
            log.info(f"🗑️ Pruned ticks < {cutoff_dt}")
        else:
            log.info("No old ticks to archive.")
            
        # --- 2. Candles 1s ---
        df_1s = pd.read_sql_query(f"SELECT * FROM candles_1s WHERE ts < {threshold_ms}", conn)
        if not df_1s.empty:
            out_file = os.path.join(LAKE_DIR, "candles_1s", f"{file_prefix}_1s.parquet")
            df_1s.to_parquet(out_file, engine='pyarrow', compression='snappy')
            
            cur = conn.cursor()
            cur.execute(f"DELETE FROM candles_1s WHERE ts < {threshold_ms}")
            conn.commit()
            log.info(f"✅ Archived and pruned {len(df_1s)} 1s candles.")
            
        # SQLite Vacuum is deferred to avoid locking during trading
        conn.close()
        
    except Exception as e:
        log.error(f"Archival failed: {e}")

if __name__ == "__main__":
    log.info("🚀 Starting Parquet Archiver Dump...")
    archive_and_prune()
    log.info("🏁 Archiver Cycle Complete.")
