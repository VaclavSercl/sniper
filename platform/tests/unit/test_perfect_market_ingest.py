#!/usr/bin/env python3
"""
Unit and Integration Tests for BEROUN Perfect Market Ingest & 1222-Day Retention Engine (§9b, §10)
"""

import shutil
import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import kline_retention_partition_manager as retention_mgr
import perfect_market_ingest as ingest_module
from perfect_market_ingest import PerfectMarketIngest

sys.path.insert(0, str(REPO_ROOT / 'tests'))
from postgres_fixture import FixtureClient


class IsolatedDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Missing fixture is an error, never an implicit production probe/skip.
        cls.client = FixtureClient.from_environment()
        cls.client.sql('''
            CREATE TABLE IF NOT EXISTS market_klines (
                open_time timestamptz NOT NULL, symbol text NOT NULL,
                src text NOT NULL, open numeric, high numeric, low numeric,
                close numeric, volume numeric, quote_volume numeric,
                trades_count bigint, close_time timestamptz,
                PRIMARY KEY(symbol, src, open_time)
            ) PARTITION BY RANGE(open_time);
            CREATE TABLE IF NOT EXISTS market_funding (
                funding_time timestamptz NOT NULL, symbol text NOT NULL,
                src text NOT NULL, rate numeric, mark_price numeric,
                PRIMARY KEY(symbol, src, funding_time)
            ) PARTITION BY RANGE(funding_time);
        ''')
        for module in (retention_mgr, ingest_module):
            patcher = patch.object(module, 'psql', cls.client.sql)
            patcher.start()
            cls.addClassCleanup(patcher.stop)


class TestHardwareAndRetentionMath(unittest.TestCase):
    def test_disk_space_sufficiency(self):
        """Verifies that available disk space exceeds BEROUN envelope requirement (10%)."""
        stat = shutil.disk_usage("/")
        free_pct = (stat.free / stat.total) * 100
        free_gb = stat.free / (1024 ** 3)
        self.assertGreater(free_pct, 10.0, f"Free disk space {free_pct:.1f}% is below 10% threshold")
        self.assertGreater(free_gb, 20.0, f"Free disk space {free_gb:.1f} GB is below 20 GB safety buffer")

    def test_1222_days_retention_math(self):
        """Verifies 1222 days covers 3 full years + leap year + CPCV buffer."""
        three_years_days = 3 * 365 + 1
        buffer_days = retention_mgr.RETENTION_DAYS - three_years_days
        self.assertGreaterEqual(buffer_days, 120, "Retention buffer must be at least 120 days for CPCV embargo")
        self.assertEqual(retention_mgr.RETENTION_DAYS, 1222)


class TestPartitionManager(IsolatedDatabaseTests):
    def test_ensure_future_partitions(self):
        """Verifies that future partitions are safely created."""
        created = retention_mgr.ensure_future_partitions(advance_months=1)
        self.assertGreater(len(created), 0)
        # Check that table exists
        part_name = created[0]
        check = retention_mgr.psql(f"SELECT to_regclass('public.{part_name}');")
        self.assertEqual(check, part_name)

    def test_prune_expired_partitions_dry_run(self):
        """Expired fixture partition is reported and retained by a dry run."""
        self.client.sql('''CREATE TABLE IF NOT EXISTS market_klines_y2020_01
            PARTITION OF market_klines
            FOR VALUES FROM ('2020-01-01') TO ('2020-02-01');''')
        dropped = retention_mgr.prune_expired_partitions(retention_days=1222, dry_run=True)
        self.assertIn('market_klines_y2020_01', dropped)
        self.assertEqual(self.client.sql("SELECT to_regclass('market_klines_y2020_01');"),
                         'market_klines_y2020_01')


class TestPerfectMarketIngest(unittest.TestCase):
    def setUp(self):
        self.ingest = PerfectMarketIngest(dry_run=True)

    def test_dry_run_batch_insert(self):
        sample = [{
            "open_time": "2026-09-20T06:00:00+00:00",
            "symbol": "BTCUSDT",
            "src": "binance_klines",
            "open": 65000.0,
            "high": 65100.0,
            "low": 64950.0,
            "close": 65050.0,
            "volume": 12.5,
            "quote_volume": 813125.0,
            "trades_count": 450,
            "close_time": "2026-09-20T06:00:59.999000+00:00"
        }]
        saved = self.ingest.save_klines_batch(sample)
        self.assertEqual(saved, 1)


class TestGapDetection(IsolatedDatabaseTests):
    def test_live_gap_detection(self):
        """Real SQL detects a known synthetic gap; repeated ingestion is idempotent."""
        retention_mgr.ensure_future_partitions(advance_months=1)
        ingest = PerfectMarketIngest(dry_run=False)
        first = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=10)
        rows = []
        for offset in (0, 3):
            ts = first + timedelta(minutes=offset)
            rows.append({'open_time': ts.isoformat(), 'close_time': (ts + timedelta(seconds=59)).isoformat(),
                         'symbol': 'FIXTURE', 'src': 'synthetic', 'open': 10, 'high': 12,
                         'low': 9, 'close': 11, 'volume': 1})
        self.assertEqual(ingest.save_klines_batch(rows), 2)
        self.assertEqual(ingest.save_klines_batch(rows), 2)
        self.assertEqual(self.client.sql("SELECT count(*) FROM market_klines WHERE symbol='FIXTURE';"), '2')
        gaps = ingest.detect_gaps('FIXTURE', 'synthetic', lookback_days=1)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]['missing_minutes'], 2)


if __name__ == "__main__":
    unittest.main()
