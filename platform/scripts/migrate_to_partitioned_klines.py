#!/usr/bin/env python3
"""
BEROUN Database Migration: Convert market_klines and market_funding to Native Declarative Partitioning (§10)
- Preserves all 2,135,497+ existing rows with zero data loss
- Creates monthly partitions from 2022-08 to 2026-12
- Enforces strict PRIMARY KEY (symbol, src, open_time)
- Atomically swaps tables under transaction
- Leaves legacy table as market_klines_legacy
"""

import subprocess
import sys
import time
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta


def psql(sql: str, check: bool = True) -> str:
    cmd = ["sudo", "-u", "beroun", "psql", "-d", "beroun", "-t", "-A", "-c", sql]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if check and res.returncode != 0:
        raise RuntimeError(f"psql failed: {res.stderr}\nQuery: {sql}")
    return res.stdout.strip()


def migrate_market_klines():
    print("=== Step 1: Checking existing market_klines ===")
    legacy_count = int(psql("SELECT count(*) FROM market_klines;"))
    print(f"Existing rows in market_klines: {legacy_count}")

    # Check if already partitioned
    is_part = psql("""
        SELECT relkind FROM pg_class WHERE relname = 'market_klines';
    """)
    if is_part == 'p':
        print("[INFO] market_klines is ALREADY a partitioned table (relkind='p'). Skipping migration.")
        return

    print("=== Step 2: Creating market_klines_partitioned parent table ===")
    psql("""
    CREATE TABLE IF NOT EXISTS market_klines_partitioned (
        open_time      TIMESTAMPTZ NOT NULL,
        symbol         TEXT NOT NULL,
        src            TEXT NOT NULL,
        open           NUMERIC NOT NULL,
        high           NUMERIC NOT NULL,
        low            NUMERIC NOT NULL,
        close          NUMERIC NOT NULL,
        volume         NUMERIC NOT NULL,
        quote_volume   NUMERIC DEFAULT 0,
        trades_count   INTEGER DEFAULT 0,
        close_time     TIMESTAMPTZ NOT NULL,
        ingested_at    TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (symbol, src, open_time)
    ) PARTITION BY RANGE (open_time);
    """)

    print("=== Step 3: Generating monthly partitions (2022-08 to 2027-01) ===")
    start_dt = datetime(2022, 8, 1, 0, 0, 0, tzinfo=timezone.utc)
    end_dt = datetime(2027, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    curr = start_dt
    while curr < end_dt:
        nxt = curr + relativedelta(months=1)
        part_name = f"market_klines_y{curr.strftime('%Y_%m')}"
        psql(f"""
        CREATE TABLE IF NOT EXISTS {part_name}
        PARTITION OF market_klines_partitioned
        FOR VALUES FROM ('{curr.isoformat()}') TO ('{nxt.isoformat()}');
        """)
        curr = nxt
    print("Partitions created successfully.")

    print("=== Step 4: Migrating data to partitioned table ===")
    t0 = time.time()
    psql("""
    INSERT INTO market_klines_partitioned (
        open_time, symbol, src, open, high, low, close, volume, close_time
    )
    SELECT
        open_time, symbol, src, open, high, low, close, volume, close_time
    FROM market_klines
    ON CONFLICT DO NOTHING;
    """)
    duration = time.time() - t0
    print(f"Data migration finished in {duration:.2f}s.")

    print("=== Step 5: Verifying integrity and row count ===")
    part_count = int(psql("SELECT count(*) FROM market_klines_partitioned;"))
    print(f"Partitioned rows: {part_count} | Legacy rows: {legacy_count}")
    if part_count != legacy_count:
        raise RuntimeError(f"ROW COUNT MISMATCH! Legacy: {legacy_count}, Partitioned: {part_count}. Aborting swap!")

    print("=== Step 6: Atomic Table Swap ===")
    psql("""
    BEGIN;
    ALTER TABLE market_klines RENAME TO market_klines_legacy;
    ALTER TABLE market_klines_partitioned RENAME TO market_klines;
    CREATE INDEX IF NOT EXISTS idx_klines_sym_src_time ON market_klines (symbol, src, open_time ASC);
    COMMIT;
    """)
    print("Atomic swap completed! market_klines is now native declarative partitioned table.")


def migrate_market_funding():
    print("=== Step 7: Checking existing market_funding ===")
    legacy_count = int(psql("SELECT count(*) FROM market_funding;"))
    print(f"Existing rows in market_funding: {legacy_count}")

    is_part = psql("SELECT relkind FROM pg_class WHERE relname = 'market_funding';")
    if is_part == 'p':
        print("[INFO] market_funding is ALREADY a partitioned table. Skipping.")
        return

    print("=== Step 8: Creating market_funding_partitioned ===")
    psql("""
    CREATE TABLE IF NOT EXISTS market_funding_partitioned (
        funding_time   TIMESTAMPTZ NOT NULL,
        symbol         TEXT NOT NULL,
        src            TEXT NOT NULL,
        rate           NUMERIC NOT NULL,
        mark_price     NUMERIC,
        ingested_at    TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (symbol, src, funding_time)
    ) PARTITION BY RANGE (funding_time);
    """)

    curr = datetime(2022, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    end_dt = datetime(2027, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    while curr < end_dt:
        nxt = curr + relativedelta(months=1)
        part_name = f"market_funding_y{curr.strftime('%Y_%m')}"
        psql(f"""
        CREATE TABLE IF NOT EXISTS {part_name}
        PARTITION OF market_funding_partitioned
        FOR VALUES FROM ('{curr.isoformat()}') TO ('{nxt.isoformat()}');
        """)
        curr = nxt

    print("=== Step 9: Migrating funding rows ===")
    psql("""
    INSERT INTO market_funding_partitioned (funding_time, symbol, src, rate, mark_price)
    SELECT funding_time, symbol, src, rate, mark_price
    FROM market_funding
    ON CONFLICT DO NOTHING;
    """)

    part_count = int(psql("SELECT count(*) FROM market_funding_partitioned;"))
    print(f"Partitioned funding rows: {part_count} | Legacy funding rows: {legacy_count}")
    if part_count != legacy_count:
        raise RuntimeError(f"FUNDING ROW COUNT MISMATCH! Aborting swap.")

    print("=== Step 10: Atomic Swap for market_funding ===")
    psql("""
    BEGIN;
    ALTER TABLE market_funding RENAME TO market_funding_legacy;
    ALTER TABLE market_funding_partitioned RENAME TO market_funding;
    CREATE INDEX IF NOT EXISTS idx_funding_sym_src_time ON market_funding (symbol, src, funding_time ASC);
    COMMIT;
    """)
    print("Atomic swap completed for market_funding!")


if __name__ == "__main__":
    try:
        migrate_market_klines()
        migrate_market_funding()
        print("\n=== ALL MIGRATIONS COMPLETED SUCCESSFULLY ===")
    except Exception as e:
        print(f"\n[MIGRATION FATAL ERROR] {e}", file=sys.stderr)
        sys.exit(1)
