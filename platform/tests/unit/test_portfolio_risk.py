"""Adversarial numerical and transaction checks; no network or real funds."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'gateway'))
from portfolio_risk import RiskBook, amount, DAY_MS

NOW = 1790800000000


class PortfolioRisk(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(':memory:', isolation_level=None)
        self.con.execute('CREATE TABLE operations(id TEXT PRIMARY KEY,status TEXT)')
        self.risk = RiskBook(self.con)

    def tearDown(self):
        self.con.close()

    @contextmanager
    def transaction(self):
        self.con.execute('BEGIN')
        try:
            yield
        except BaseException:
            self.con.execute('ROLLBACK')
            raise
        else:
            self.con.execute('COMMIT')

    def observe(self, now=NOW, **kwargs):
        snapshot = dict(time_ms=now, equity='1000', available='1000', inventory='0', pending_buys='0',
                        complete=True, cashflow_free=True, ownership_verified=True)
        snapshot.update(kwargs)
        with self.transaction():
            return self.risk.observe(snapshot, now)

    def reserve(self, operation, notional, now=NOW):
        with self.transaction():
            result = self.risk.reserve(operation, notional, now)
            self.con.execute('INSERT INTO operations VALUES(?,?)', (operation, 'PREPARED'))
        return result

    def test_owner_ceiling_strategy_order_and_minimum(self):
        state = self.observe()
        self.assertEqual(amount(state['budget']), 900)
        self.assertEqual(self.reserve('first', '45')['status'], 'RISK_RESERVED')
        for n in ('45.000001', '9.999', 'NaN', 'Infinity', '-1', '1e-100', '1e100', True, 1.0):
            with self.subTest(n=n), self.assertRaises(ValueError):
                self.reserve('bad', n)

    def test_pending_unresolved_reservations_are_never_released_by_timeout(self):
        self.observe(available='70')
        self.reserve('first', '45')
        self.con.execute("UPDATE operations SET status='UNKNOWN_REQUIRES_RECONCILIATION'")
        with self.assertRaises(ValueError): self.reserve('second', '30')
        with self.assertRaises(sqlite3.IntegrityError): self.reserve('first', '10')

    def test_inventory_and_live_orders_consume_strategy_allocation(self):
        self.observe()
        self.observe(NOW+1, inventory='100', pending_buys='60', available='100')
        with self.assertRaises(ValueError): self.reserve('too-large', '25', NOW+1)
        self.reserve('small', '10', NOW+1)

    def test_loss_and_drawdown_latch_survive_restart_and_recovery(self):
        self.observe()
        state = self.observe(NOW+1, equity='990', available='990')
        self.assertIn('DAILY_LOSS_LIMIT', state['reasons'])
        self.risk = RiskBook(self.con)
        state = self.observe(NOW+2)
        self.assertTrue(state['halted'])
        with self.assertRaises(ValueError): self.reserve('recovered', '10', NOW+2)

    def test_overnight_loss_is_not_erased_by_first_day_observation(self):
        midnight = ((NOW//DAY_MS)+1)*DAY_MS
        self.observe(midnight-1000)
        state = self.observe(midnight+1000, equity='990', available='990')
        self.assertEqual(amount(state['day_anchor']), 1000)
        self.assertTrue(state['halted'])

    def test_stale_future_gap_and_invalidation_block_admission(self):
        self.observe()
        with self.assertRaises(ValueError): self.reserve('stale', '10', NOW+5001)
        with self.assertRaises(ValueError): self.observe(NOW-1)
        with self.assertRaises(ValueError): self.observe(NOW+60001)
        with self.transaction(): self.risk.invalidate()
        with self.assertRaises(ValueError): self.reserve('invalid', '10')

    def test_unknown_cashflows_ownership_and_equity_block(self):
        for data in ({'complete': False}, {'cashflow_free': False}, {'ownership_verified': False},
                     {'equity': '0'}, {'available': '1001'}, {'inventory': '1'}, {'pending_buys': '1'}):
            with self.subTest(data=data), self.assertRaises(ValueError): self.observe(**data)
        self.assertIsNone(self.risk.state())

    def test_atomic_order_and_risk_reservation_rollback(self):
        self.observe()
        with self.assertRaises(RuntimeError), self.transaction():
            self.risk.reserve('interrupted', '20', NOW)
            raise RuntimeError('before order intent')
        self.assertEqual(self.con.execute('SELECT count(*) FROM risk_reservations').fetchone()[0], 0)
        self.reserve('actual', '20')

    def test_profit_never_increases_original_capital_allowance(self):
        self.observe()
        state = self.observe(NOW+1, equity='1200', available='1200')
        self.assertEqual(amount(state['budget']), 900)
        with self.assertRaises(ValueError): self.reserve('scale', '46', NOW+1)

    def test_drawdown_from_chronological_peak_and_fee_budget(self):
        self.observe()
        self.observe(NOW+1, equity='1020', available='1020')
        state = self.observe(NOW+2, equity='992', available='992')
        self.assertIn('DRAWDOWN_LIMIT', state['reasons'])
        self.assertEqual(amount(state['drawdown']), 28)


if __name__ == '__main__': unittest.main()
