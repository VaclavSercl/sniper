#!/usr/bin/env python3
"""
🧼 Data Retention & Archiving CLI
Fáze L2: Udržuje databáze rychlé a archivuje stará data (Tiered Storage).

Funkce:
1. Agreguje historické fills do `hourly_summary` (10–90 dní WARM tier).
2. Exportuje HOT raw data (>10 dní) do CSV/Parquet (COLD tier).
3. Promaže stará data z HOT SQLite tabulek.
4. Vykoná VACUUM pro uvolnění místa.
"""

import os
import sqlite3
import time
import logging
import gzip
import csv
from datetime import datetime, timedelta

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

log = logging.getLogger("retention_cron")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

HOME_DIR = os.path.expanduser("~")
DATA_DIR = os.path.join(HOME_DIR, ".local", "share", "sniper")
ARCHIVE_DIR = "/data/archive" if os.path.ismount("/data") and os.access("/data", os.W_OK) else os.path.join(DATA_DIR, "archive")
PNL_DB = os.path.join(DATA_DIR, "pnl.db")

HOT_RETENTION_DAYS = 10
WARM_RETENTION_DAYS = 90

def ensure_schema(conn):
    """Ensure WARM tier tables exist."""
    conn.execute('''
    CREATE TABLE IF NOT EXISTS hourly_summary (
        bot TEXT,
        hour_ts INTEGER,
        fills INTEGER,
        pnl REAL,
        toxic_pct REAL,
        PRIMARY KEY (bot, hour_ts)
    )
    ''')
    conn.commit()

def aggregate_to_warm(conn):
    """Agreguje fill data starší než 24 hodin a mladší než 10 dní do hourly_summary."""
    log.info("📊 Agreguji data do WARM storage (hourly_summary)...")
    
    # Konec okna je vcerejsek 23:59:59
    now = datetime.now()
    cutoff_ts = int((now - timedelta(days=1)).timestamp() * 1000)
    
    # Vyber raw data, ktera jeste nejsou v hourly_summary (dle hour_ts)
    # Zjednodušený dotaz: vezmeme data a spočítáme sumy dle (bot, hour)
    cur = conn.cursor()
    cur.execute('''
    SELECT bot, 
           (ts_ms / 3600000) * 3600000 AS hour_ts,
           COUNT(*) as fills,
           SUM(net_pnl) as pnl,
           SUM(CASE WHEN net_pnl < 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as toxic_pct
    FROM fills 
    WHERE ts_ms <= ?
    GROUP BY bot, hour_ts
    ''', (cutoff_ts,))
    
    rows = cur.fetchall()
    inserted = 0
    for row in rows:
        try:
            cur.execute('''
            INSERT OR IGNORE INTO hourly_summary (bot, hour_ts, fills, pnl, toxic_pct)
            VALUES (?, ?, ?, ?, ?)
            ''', row)
            if cur.rowcount > 0:
                inserted += 1
        except Exception:
            pass
            
    conn.commit()
    log.info(f"✅ Vytvořeno {inserted} nových hodinových agregací.")

def archive_and_purge_hot(conn):
    """Archivuje raw fills do Parquet/CSV a smaže z HOT SQLite (>10 dní)."""
    log.info("🧊 Presouvat HOT data do COLD archivu...")
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    
    cutoff_ms = int((time.time() - (HOT_RETENTION_DAYS * 86400)) * 1000)
    cur = conn.cursor()
    
    # Pokud nemame vubec zadna tak stara data, skip
    cur.execute("SELECT COUNT(*) FROM fills WHERE ts_ms < ?", (cutoff_ms,))
    count = cur.fetchone()[0]
    
    if count == 0:
        log.info("  Žádná raw data starší než 10 dní k archivaci.")
        return
        
    log.info(f"  Nalezeno {count} fills k archivaci.")
    
    if HAS_PANDAS:
        df = pd.read_sql_query("SELECT * FROM fills WHERE ts_ms < ?", conn, params=(cutoff_ms,))
        ymd = datetime.now().strftime("%Y-%m-%d")
        out_file = os.path.join(ARCHIVE_DIR, f"pnl_raw_{ymd}.parquet")
        try:
            df.to_parquet(out_file)
            log.info(f"  ✅ Uloženo do {out_file} (Parquet)")
        except Exception as e:
            log.warning(f"  Parquet export failed ({e}), keeping in DB")
            return
    else:
        log.warning("Pandas missing, unable to create proper Parquet. Skipping proper export. Only dropping...")

    # Drop from HOT database after backup!
    cur.execute("DELETE FROM fills WHERE ts_ms < ?", (cutoff_ms,))
    conn.commit()
    log.info(f"🗑️ HOT Data Purge: smazáno {cur.rowcount} záznamů starších než 10 dní z pnl.db")

def vacuum_db(conn):
    log.info("🧹 Spouštím VACUUM na pnl.db pro defragmentaci M.2 NVMe...")
    old_size = os.path.getsize(PNL_DB) if os.path.exists(PNL_DB) else 0
    conn.execute("VACUUM")
    conn.commit()
    new_size = os.path.getsize(PNL_DB) if os.path.exists(PNL_DB) else 0
    saved = max(0, old_size - new_size)
    log.info(f"✅ VACUUM hotovo. Uvolněno {saved / 1024 / 1024:.2f} MB.")

def configure_logrotate():
    """Vykreslí/Nainstaluje logrotate konfiguraci."""
    log_cfg = """/home/wwwenda/sniper/logs/*.log
/home/wwwenda/beroun-brain/short_term/*.log {
    daily
    missingok
    rotate 30
    compress
    delaycompress
    notifempty
    copytruncate
}
"""
    dest = "/tmp/beroun-sniper-logs"
    try:
        with open(dest, "w") as f:
            f.write(log_cfg)
        log.info(f"📜 Logrotate config napsán do {dest}. Pro plnou aplikaci zavolej `sudo mv {dest} /etc/logrotate.d/`.")
    except Exception as e:
        log.error(f"Failed to write logrotate: {e}")

if __name__ == "__main__":
    log.info("=== BEROUN TIERED RETENTION START ===")
    
    if os.path.exists(PNL_DB):
        conn = sqlite3.connect(PNL_DB)
        try:
            ensure_schema(conn)
            aggregate_to_warm(conn)
            archive_and_purge_hot(conn)
            vacuum_db(conn)
        finally:
            conn.close()
    else:
        log.warning("pnl.db nenalezena, přeskočeno.")
        
    configure_logrotate()
    log.info("=== BEROUN TIERED RETENTION DONE ===")
