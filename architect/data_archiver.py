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
        
        import pyarrow as pa
        import pyarrow.parquet as pq
        
        # --- 1. Ticks ---
        log.info(f"Querying ticks older than {cutoff_dt}...")
        df_iter = pd.read_sql_query(f"SELECT * FROM ticks WHERE ts_ms < {threshold_ms}", conn, chunksize=500_000)
        
        writer = None
        out_file = os.path.join(LAKE_DIR, "ticks", f"{file_prefix}_ticks.parquet")
        total_rows = 0
        
        for df_ticks in df_iter:
            table = pa.Table.from_pandas(df_ticks, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(out_file, table.schema, compression='snappy')
            writer.write_table(table)
            total_rows += len(df_ticks)
            
        if writer is not None:
            writer.close()
            log.info(f"✅ Archived {total_rows} ticks to {out_file}")
            
            # Prune AFTER safe export in small chunks to prevent SQLite 'database is locked' errors!
            cur = conn.cursor()
            deleted_ticks = 0
            while True:
                cur.execute(f"DELETE FROM ticks WHERE ts_ms < {threshold_ms} LIMIT 100000")
                if cur.rowcount == 0:
                    break
                conn.commit()
                deleted_ticks += cur.rowcount
                time.sleep(0.1) # Yield lock to market_recorder.py
            log.info(f"🗑️ Pruned {deleted_ticks} ticks < {cutoff_dt}")
        else:
            log.info("No old ticks to archive.")
            
        # --- 2. Candles 1s ---
        df_1s_iter = pd.read_sql_query(f"SELECT * FROM candles_1s WHERE ts < {threshold_ms}", conn, chunksize=500_000)
        out_file_1s = os.path.join(LAKE_DIR, "candles_1s", f"{file_prefix}_1s.parquet")
        writer_1s = None
        total_1s = 0
        
        for df_1s in df_1s_iter:
            table = pa.Table.from_pandas(df_1s, preserve_index=False)
            if writer_1s is None:
                writer_1s = pq.ParquetWriter(out_file_1s, table.schema, compression='snappy')
            writer_1s.write_table(table)
            total_1s += len(df_1s)
            
        if writer_1s is not None:
            writer_1s.close()
            cur = conn.cursor()
            deleted_1s = 0
            while True:
                cur.execute(f"DELETE FROM candles_1s WHERE ts < {threshold_ms} LIMIT 100000")
                if cur.rowcount == 0:
                    break
                conn.commit()
                deleted_1s += cur.rowcount
                time.sleep(0.1)
            log.info(f"✅ Archived and pruned {total_1s} (+ {deleted_1s} deleted) 1s candles.")
            
        # SQLite Vacuum is deferred to avoid locking during trading
        conn.close()
        
    except Exception as e:
        log.error(f"Archival failed: {e}")

if __name__ == "__main__":
    log.info("🚀 Starting Parquet Archiver Dump...")
    archive_and_prune()
    log.info("🏁 Archiver Cycle Complete.")
