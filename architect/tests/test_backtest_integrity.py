import importlib.util
import logging
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'counterfactual_backtest.py'
spec = importlib.util.spec_from_file_location('tested_backtest', SOURCE)
bt = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bt
# Permits testing the original import-time FileHandler defect without writing logs.
with patch.object(logging, 'FileHandler', return_value=logging.NullHandler()):
    spec.loader.exec_module(bt)


class IntegrityTests(unittest.TestCase):
    def result(self, curve, fills=()):
        return bt._build_result('hydra', list(fills), curve, 10, 0, 0, 100,
                                100, 0, 10, {}, 1)

    def test_drawdown_is_chronological(self):
        result = self.result({2: 2, 1: 10, 3: 12})
        self.assertEqual(result.max_drawdown, 8)
        self.assertFalse(result.passed)

    def test_rebates_keep_sign(self):
        result = self.result({0: 0, 1: 10}, [{'side': 'buy', 'fee': -2}])
        self.assertEqual(result.total_fees, -2)
        self.assertEqual(result.gross_pnl, 8)

    def test_uncalibrated_model_cannot_pass(self):
        result = self.result({i: i for i in range(100)},
                             [{'side': 'buy', 'fee': 0}] * 100)
        self.assertFalse(result.passed)
        self.assertIn('UNVERIFIED_EXECUTION_MODEL', result.fail_reasons)

    def test_import_does_not_open_log(self):
        with patch.object(logging, 'FileHandler') as handler:
            spec.loader.exec_module(bt)
        handler.assert_not_called()

    def test_nonfinite_pnl_rejected(self):
        with self.assertRaises(ValueError):
            self.result({0: float('nan')})

    def test_unsupported_bot_rejected(self):
        with self.assertRaises(ValueError):
            bt.run_counterfactual_validation('unknown', {})

    def test_unsupported_partition_rejected(self):
        with self.assertRaises(ValueError):
            bt.run_counterfactual_validation('hydra', {}, 'typo')

    def test_overlapping_split_rejected(self):
        with patch.object(bt, 'load_walk_forward_split', return_value={
            'is_start_ms': 0, 'is_end_ms': 20, 'oos_start_ms': 10, 'oos_end_ms': 30
        }), self.assertRaises(ValueError):
            bt.run_counterfactual_validation('hydra', {})


class DataTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE ticks(ts_ms,exchange,symbol,price,qty,side,trade_id)')
        self.db.execute('CREATE TABLE candles_1m(ts,exchange,symbol,open,high,low,close,volume)')
        self.addCleanup(self.db.close)

    def trade(self, ts, identity, price=100):
        self.db.execute('INSERT INTO ticks VALUES (?,?,?,?,?,?,?)',
                        (ts, 'bitfinex', 'tBTCUSD', price, 1, 'sell', identity))

    def load(self, start=0, end=20):
        # No lake filesystem or pandas dependency in these SQLite tests.
        with patch('glob.glob', return_value=[]):
            return bt.load_trades(self.db, 'bitfinex', 'tBTCUSD', start, end)

    def test_same_timestamp_distinct_trades_preserved(self):
        self.trade(10, 'a'); self.trade(10, 'b')
        self.assertEqual(len(self.load()), 2)

    def test_partition_boundary_exclusive(self):
        self.trade(10, 'a'); self.trade(20, 'b')
        self.assertEqual(len(self.load()), 1)

    def test_duplicate_identity_deduplicates(self):
        self.trade(10, 'a'); self.trade(10, 'a')
        self.assertEqual(len(self.load()), 1)

    def test_conflicting_identity_rejected(self):
        self.trade(10, 'a'); self.trade(11, 'a', 101)
        with self.assertRaises(ValueError): self.load()

    def test_missing_identity_rejected(self):
        self.trade(10, None)
        with self.assertRaises(ValueError): self.load()

    def test_invalid_price_rejected(self):
        self.trade(10, 'a', -1)
        with self.assertRaises(ValueError): self.load()

    def test_sql_failure_not_silenced(self):
        self.db.execute('DROP TABLE ticks')
        with self.assertRaises(sqlite3.Error): self.load()

    def test_lake_filters_instrument_and_window(self):
        rows = [dict(ts_ms=10, exchange=e, symbol=s, price=100, qty=1,
                     side='sell', trade_id=i) for e, s, i in
                [('bitfinex', 'tBTCUSD', 'a'), ('binance', 'tBTCUSD', 'b'),
                 ('bitfinex', 'tETHUSD', 'c')]]
        rows.append(dict(rows[0], ts_ms=20, trade_id='d'))
        with patch.object(bt, '_lake_rows', return_value=rows):
            result = self.load()
        self.assertEqual([row['trade_id'] for row in result], ['a'])

    def test_lake_missing_identity_rejected(self):
        with patch.object(bt, '_lake_rows', return_value=[{'ts_ms': 10}]), self.assertRaises(ValueError):
            self.load()

    def test_conflicting_candles_rejected(self):
        for close in (100, 101):
            self.db.execute('INSERT INTO candles_1m VALUES (?,?,?,?,?,?,?,?)',
                            (10, 'bitfinex', 'tBTCUSD', 100, 102, 99, close, 1))
        with patch.object(bt, '_lake_rows', return_value=[]), self.assertRaises(ValueError):
            bt.load_candles(self.db, 'bitfinex', 'tBTCUSD', 0, 20)


class SimulationTests(unittest.TestCase):
    def trade(self, ts, price, side='sell', qty=1):
        return dict(ts_ms=ts, price=price, side=side, qty=qty)

    def simulate(self, rows):
        queue = unittest.mock.Mock()
        queue.will_fill.return_value = True
        return bt._simulate_hydra_tick_level(rows, 4, .0005, .1, queue)

    def test_quote_precedes_current_trade(self):
        # The prior observation places bid=98; consuming 97.99 first moves it below the trade.
        result = self.simulate([self.trade(0, 100), self.trade(1, 97.99)])
        self.assertEqual(result.buy_fills, 1)

    def test_wrong_aggressor_cannot_fill_bid(self):
        result = self.simulate([self.trade(0, 100), self.trade(1, 90, 'buy')])
        self.assertEqual(result.total_fills, 0)

    def test_fill_capped_by_trade_volume_and_position(self):
        with patch.object(bt, '_build_result', wraps=bt._build_result) as build:
            self.simulate([self.trade(0, 100), self.trade(1, 90, qty=.0001)])
        self.assertEqual(build.call_args.args[1][0]['qty'], .0001)

    def test_intraminute_drawdown_retained(self):
        result = self.simulate([self.trade(0, 100), self.trade(1, 90),
                                self.trade(2, 50), self.trade(3, 100)])
        self.assertGreater(result.max_drawdown, .02)

    def test_fee_must_be_explicit(self):
        result = bt.simulate_hydra([], [self.trade(0, 100)], {})
        self.assertIn('EXPLICIT_MAKER_FEE_REQUIRED', result.fail_reasons)

    def test_daily_returns_use_capital_and_calendar(self):
        import math
        import statistics
        day = 86400000
        curve = {0: 0, day: 10, 2*day: 5, 3*day: 100}
        result = bt._build_result('hydra', [], curve, 100, 0, 0, 0, 0, 0, 0,
                                  {'initial_capital': 100}, 72)
        returns = [.1, 105/110-1]
        self.assertAlmostEqual(result.sharpe_ratio,
                               statistics.mean(returns)/statistics.stdev(returns)*math.sqrt(365))
        self.assertNotIn('DAILY_RETURN_EVIDENCE_MISSING', result.fail_reasons)
        self.assertFalse(result.passed)

    def test_gapped_daily_returns_not_annualized(self):
        result = bt._build_result('hydra', [], {0:0, 86400000:1, 259200000:3, 345600000:4},
                                  4, 0, 0, 0, 0, 0, 0, {'initial_capital':100}, 96)
        self.assertIn('DAILY_RETURN_EVIDENCE_MISSING', result.fail_reasons)


if __name__ == '__main__':
    unittest.main()
