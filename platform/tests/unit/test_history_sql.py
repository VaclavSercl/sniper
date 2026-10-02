"""Actual PostgreSQL tests; fixture is required, never production fallback."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import sys

TESTS=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(TESTS))
from postgres_fixture import FixtureClient, FixtureBlocked
from test_history_recovery import h,bar,T


class HistorySQLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db=FixtureClient.from_environment()
        cls.db.sql('CREATE SCHEMA IF NOT EXISTS history_recovery_test;')

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.bundle=Path(self.tmp.name)/'bundle'
        self.m=h.stage('binance','BTCUSDT',T,T+60000,self.bundle,
                       fetch=lambda s:json.dumps([bar()]).encode(),now=T+120000)
        self.db.sql('''DROP TABLE IF EXISTS history_recovery_test.market_klines;
          CREATE TABLE history_recovery_test.market_klines(
           open_time timestamptz NOT NULL,symbol text NOT NULL,src text NOT NULL,
           open numeric,high numeric,low numeric,close numeric,volume numeric,
           quote_volume numeric,trades_count integer,close_time timestamptz,
           ingested_at timestamptz DEFAULT now(),PRIMARY KEY(symbol,src,open_time));''')

    def sql(self,text):
        return self.db.sql('SET search_path TO history_recovery_test;\n'+text)

    def test_actual_insert_count_and_replay(self):
        script=h.import_sql(self.bundle,self.m['manifest_sha256'])
        first=self.sql(script);second=self.sql(script)
        self.assertIn('"inserted" : 1',first);self.assertIn('"inserted" : 0',second)
        self.assertEqual(self.sql('SELECT count(*) FROM market_klines;').splitlines()[-1],'1')

    def test_conflict_rolls_back_all_inserts(self):
        script=h.import_sql(self.bundle,self.m['manifest_sha256'])
        self.sql(script);self.sql('UPDATE market_klines SET close=10;')
        second=Path(self.tmp.name)/'second'
        m=h.stage('binance','BTCUSDT',T,T+120000,second,
                  fetch=lambda s:json.dumps([bar(),bar(T+60000)]).encode(),now=T+180000)
        with self.assertRaises(FixtureBlocked):self.sql(h.import_sql(second,m['manifest_sha256']))
        self.assertEqual(self.sql('SELECT close FROM market_klines;').splitlines()[-1],'10')
        self.assertEqual(self.sql('SELECT count(*) FROM market_klines;').splitlines()[-1],'1')


if __name__=='__main__':unittest.main()
