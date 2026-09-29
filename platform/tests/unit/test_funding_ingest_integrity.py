import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import ingest_tri_venue as tri
spec = importlib.util.spec_from_file_location('funding_fetch_under_test', ROOT / 'legacy/scripts/fetch_funding.py')
funding = importlib.util.module_from_spec(spec)
spec.loader.exec_module(funding)


class FundingIntegrityTests(unittest.TestCase):
    def row(self, **changes):
        return dict(symbol='BTCUSDT', fundingTime=1_000_000_000,
                    fundingRate='0.0001', markPrice='65000', **changes)

    def run_fetch(self, rows):
        with tempfile.TemporaryDirectory() as temp, patch.object(funding, 'LOG', str(Path(temp)/'log')), \
             patch.object(sys, 'argv', ['fetch', 'BTCUSDT', '2']), \
             patch.object(funding.time, 'time', return_value=1_000_001), \
             patch.object(funding, 'fetch', return_value=rows), \
             patch.object(funding, 'psql') as db:
            result = funding.main()
            return result, [call.args[0] for call in db.call_args_list]

    def test_composite_key_and_no_schema_side_effect(self):
        result, sql = self.run_fetch([self.row()])
        self.assertEqual(result, 0)
        self.assertEqual(len(sql), 1)
        self.assertIn('ON CONFLICT (symbol, src, funding_time)', sql[0])
        self.assertNotIn('CREATE', sql[0])

    def test_unavailable_fx_is_explicit_not_synthetic(self):
        self.assertNotIn('tEURUSD', tri.BITFINEX_TARGETS)
        self.assertIn('tEURUSD', tri.UNAVAILABLE_MARKETS['bitfinex'])

    def test_empty_or_wrong_response_is_failure(self):
        for rows in ([], {}, {'code': -1}):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.run_fetch(rows)

    def test_invalid_identity_values_and_chronology_fail(self):
        for change in ({'symbol': "BAD'"}, {'fundingRate': 'nan'}, {'markPrice': 'inf'},
                       {'markPrice': 0}, {'fundingRate': True}, {'fundingTime': 1_000_002_000}):
            row = self.row(); row.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.run_fetch([row])
        with self.assertRaises(ValueError):
            self.run_fetch([self.row(), self.row()])

    def test_database_failure_never_tries_sudo(self):
        for module in (funding, tri):
            with patch.object(module.subprocess, 'run') as run:
                run.return_value.returncode = 2
                with self.assertRaises(RuntimeError): module.psql('SELECT 1')
                self.assertEqual(run.call_count, 1)
                self.assertEqual(run.call_args.args[0][0], 'psql')
                self.assertIn('-w', run.call_args.args[0])

    def test_unexpected_remote_symbol_cannot_reach_sql(self):
        runner = tri.TriVenueIngest(dry_run=True)
        with patch.object(runner.bitfinex, 'tickers', return_value={"BAD'); DROP TABLE x;--": {'mid': 1}}):
            self.assertEqual(runner.fetch_bitfinex(), [])
            self.assertTrue(runner.errors)

    def test_missing_coverage_and_cli_fail(self):
        runner = tri.TriVenueIngest(dry_run=True)
        with patch.object(runner, 'fetch_binance', return_value=[]), \
             patch.object(runner, 'fetch_bitfinex', return_value=[]), \
             patch.object(runner, 'fetch_hyperliquid', return_value=([], [])):
            result = runner.ingest_cycle()
        self.assertEqual(len(result['errors']), 4)
        self.assertEqual(result['funding_kind'], 'SNAPSHOT_NOT_SETTLEMENT')
        with patch.object(sys, 'argv', ['tri']), patch.object(tri.TriVenueIngest, 'ingest_cycle', return_value=result):
            self.assertEqual(tri.main(), 1)

    def test_persistence_failure_is_visible(self):
        runner = tri.TriVenueIngest()
        with patch.object(tri, 'BINANCE_TARGETS', ['BTCUSDT']), patch.object(tri, 'BITFINEX_TARGETS', []), \
             patch.object(tri, 'HYPERLIQUID_TARGETS', []), \
             patch.object(runner, 'fetch_binance', return_value=[{'symbol': 'BTCUSDT', 'price': 42., 'src': 'binance_ticker'}]), \
             patch.object(runner, 'fetch_bitfinex', return_value=[]), \
             patch.object(runner, 'fetch_hyperliquid', return_value=([], [])), \
             patch.object(tri, 'psql', side_effect=RuntimeError('fixture')):
            result = runner.ingest_cycle()
        self.assertEqual(result['ticks_written'], 0)
        self.assertEqual(result['errors'][0]['source'], 'Tick insert')


class FundingSQLTests(unittest.TestCase):
    def test_actual_composite_key_accepts_two_symbols_and_deduplicates(self):
        sys.path.insert(0, str(ROOT/'tests'))
        from postgres_fixture import FixtureClient
        client = FixtureClient.from_environment()
        _, statements = FundingIntegrityTests().run_fetch([FundingIntegrityTests().row()])
        statement = statements[0]
        result = client.sql('''BEGIN; CREATE TEMP TABLE market_funding (
            funding_time timestamptz NOT NULL, symbol text NOT NULL, src text NOT NULL,
            rate numeric, mark_price numeric, PRIMARY KEY(symbol,src,funding_time));
            ''' + statement + ';' + statement + ';' + statement.replace('BTCUSDT','ETHUSDT') +
            '; SELECT count(*) FROM market_funding; ROLLBACK;')
        self.assertEqual(result.strip().splitlines(),
                         ['BEGIN', 'CREATE TABLE', 'INSERT 0 1', 'INSERT 0 0', 'INSERT 0 1', '2', 'ROLLBACK'])


if __name__ == '__main__': unittest.main()
