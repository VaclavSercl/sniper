import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'platform/research'))
sys.path.insert(0, str(ROOT/'platform/tests'))
from postgres_fixture import FixtureClient
from operational_observation import AUDIT_SQL


class AuditSqlTests(unittest.TestCase):
    def test_real_postgres_gaps_invalid_prices_source_isolation_and_no_changes(self):
        client = FixtureClient.from_environment()
        prefix = """CREATE TEMP TABLE market_klines(open_time timestamptz,close_time timestamptz,
            src text,symbol text,open numeric,high numeric,low numeric,close numeric,volume numeric);
        CREATE TEMP TABLE market_funding(funding_time timestamptz,src text,symbol text,rate numeric);
        INSERT INTO market_klines SELECT date_trunc('minute',now())-interval '5 minutes',
            date_trunc('minute',now())-interval '4 minutes 0.001 seconds',
            'hyperliquid_candles','BTC-PERP',10,12,9,11,3;
        INSERT INTO market_klines SELECT date_trunc('minute',now())-interval '3 minutes',
            date_trunc('minute',now())-interval '2 minutes 0.001 seconds',
            'hyperliquid_candles','BTC-PERP',10,8,9,11,3;
        INSERT INTO market_klines SELECT date_trunc('minute',now())-interval '1 minute',
            date_trunc('minute',now())-interval '0.001 seconds',
            'binance_klines','BTCUSDT',10,12,9,11,3;
        INSERT INTO market_funding VALUES(now()-interval '1 hour','hyperliquid','BTC',0.0001);
        """
        output = client.sql(prefix + AUDIT_SQL + 'SELECT count(*) FROM market_klines;')
        result = next(json.loads(line) for line in output.splitlines() if line.startswith('{'))
        self.assertEqual(len(result['candles']),1)
        self.assertEqual(result['candles'][0]['candles'],2)
        self.assertEqual(result['candles'][0]['gap_intervals'],1)
        self.assertEqual(result['candles'][0]['invalid_candles'],1)
        self.assertEqual(result['funding'][0]['observations'],1)
        self.assertFalse(result['qualified'])
        self.assertEqual(output.splitlines()[-1],'3')


if __name__ == '__main__': unittest.main()
