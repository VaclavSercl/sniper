#!/usr/bin/env python3
"""
BEROUN 1222-Day Declarative Partition & Retention Manager (§9b, §10)
- Natively manages monthly partitions for market_klines and market_funding
- Maintains rolling retention window of exactly 1222 days (3 years + 126d buffer)
- Instant O(1) metadata drops (zero WAL bloat, zero fragmentation, zero locks)
- Prunes raw market_ticks older than 90 days (Tier 2 ILM)
"""

import argparse
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from dateutil.relativedelta import relativedelta
from typing import List, Tuple
from db_client import psql

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [retention_manager] %(message)s"
)
logger = logging.getLogger("retention_manager")

RETENTION_DAYS = 1222
TICK_RETENTION_DAYS = 90
TABLES = ["market_klines", "market_funding"]


def ensure_future_partitions(advance_months: int = 2) -> List[str]:
    """Ensures monthly partitions exist from previous month through advance_months in future."""
    created = []
    now = datetime.now(timezone.utc)
    for offset in range(-1, advance_months + 1):
        target = now + relativedelta(months=offset)
        start_date = target.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_date = start_date + relativedelta(months=1)
        y_m = start_date.strftime("%Y_%m")

        for table in TABLES:
            part_name = f"{table}_y{y_m}"
            sql = f"""
            CREATE TABLE IF NOT EXISTS {part_name}
            PARTITION OF {table}
            FOR VALUES FROM ('{start_date.isoformat()}') TO ('{end_date.isoformat()}');
            """
            psql(sql)
            created.append(part_name)
    logger.info("Verified monthly partitions up to %d months in advance.", advance_months)
    return created


def prune_expired_partitions(retention_days: int = RETENTION_DAYS, dry_run: bool = False) -> List[str]:
    """Finds and drops partitions whose entire date range is older than retention_days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    logger.info("Evaluating retention window: Cutoff = %s (older data is dropped)", cutoff.isoformat())
    dropped = []

    for table in TABLES:
        # Query pg_inherits and pg_class to inspect bounds of all child partitions
        query = f"""
        SELECT c.relname, pg_get_expr(c.relpartbound, c.oid)
        FROM pg_inherits i
        JOIN pg_class c ON c.oid = i.inhrelid
        JOIN pg_class p ON p.oid = i.inhparent
        WHERE p.relname = '{table}'
        ORDER BY c.relname ASC;
        """
        rows = psql(query, check=False).splitlines()
        for r in rows:
            if not r or "|" not in r:
                continue
            part_name, bound_expr = r.split("|", 1)
            # bound_expr example: FOR VALUES FROM ('2022-08-01 00:00:00+00') TO ('2022-09-01 00:00:00+00')
            if "TO ('" not in bound_expr:
                continue
            try:
                to_str = bound_expr.split("TO ('")[1].split("')")[0]
                part_end = datetime.fromisoformat(to_str)

                if part_end < cutoff:
                    logger.warning(
                        "Partition %s EXPIRED (End: %s < Cutoff: %s)",
                        part_name, part_end.date(), cutoff.date()
                    )
                    if not dry_run:
                        # 1. Detach concurrently so running queries don't block
                        psql(f"ALTER TABLE {table} DETACH PARTITION {part_name} CONCURRENTLY;", check=False)
                        # 2. Instant metadata drop (reclaims disk space immediately)
                        psql(f"DROP TABLE IF EXISTS {part_name};")
                        logger.info("Successfully dropped expired partition: %s", part_name)
                    dropped.append(part_name)
                else:
                    logger.debug("Partition %s remains active (End: %s >= Cutoff: %s)", part_name, part_end.date(), cutoff.date())
            except Exception as e:
                logger.error("Failed to parse partition %s bound '%s': %s", part_name, bound_expr, e)

    return dropped


def prune_raw_ticks(tick_days: int = TICK_RETENTION_DAYS, dry_run: bool = False) -> int:
    """Prunes raw market_ticks older than tick_days (Tier 2 ILM)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=tick_days)
    logger.info("Pruning raw market_ticks older than %d days (Cutoff: %s)", tick_days, cutoff.isoformat())
    count_str = psql(f"SELECT count(*) FROM market_ticks WHERE ts < '{cutoff.isoformat()}';", check=False)
    to_delete = int(count_str) if count_str.isdigit() else 0

    if to_delete > 0:
        logger.info("Found %d expired raw ticks to prune.", to_delete)
        if not dry_run:
            psql(f"DELETE FROM market_ticks WHERE ts < '{cutoff.isoformat()}';")
            logger.info("Expired raw ticks deleted.")
    else:
        logger.info("No expired raw ticks to prune.")
    return to_delete


def main() -> int:
    parser = argparse.ArgumentParser(description="BEROUN 1222-Day Partition & Retention Manager")
    parser.add_argument("--dry-run", action="store_true", help="Audit without dropping tables")
    parser.add_argument("--retention-days", type=int, default=RETENTION_DAYS, help="Retention window in days (default 1222)")
    parser.add_argument("--advance-months", type=int, default=2, help="Advance months to pre-create (default 2)")
    args = parser.parse_args()

    logger.info("=== Starting BEROUN Retention & Partition Maintenance ===")
    ensure_future_partitions(advance_months=args.advance_months)
    dropped = prune_expired_partitions(retention_days=args.retention_days, dry_run=args.dry_run)
    ticks_pruned = prune_raw_ticks(dry_run=args.dry_run)
    logger.info("Maintenance complete. Partitions dropped: %d | Raw ticks pruned: %d", len(dropped), ticks_pruned)
    return 0


if __name__ == "__main__":
    sys.exit(main())
