import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'research'))
import research_accounting_audit as audit
import research_cycle as cycle
import research_protocol as protocol
from test_research_cycle import epoch
from test_research_protocol import bars


class ReferenceLedgerTests(unittest.TestCase):
    def setUp(self):
        self.bars = bars()[:144]
        self.start, self.end = 48, 144
        self.fee, self.slip = '0.0021', '0.001'
        self.result = protocol.simulate(self.bars, self.start, self.end, 'buy_hold', self.fee, self.slip)

    def check(self, result=None, fee=None, slip=None):
        return audit.audit_leg(self.bars, self.start, self.end,
            self.result if result is None else result, fee or self.fee, slip or self.slip, '1000', '0.25')

    def test_independent_cash_inventory_marks_fees_and_daily_equity(self):
        with patch.object(protocol, 'simulate', side_effect=AssertionError('Simulator must not run')), \
             patch.object(protocol, 'daily_returns', side_effect=AssertionError('Shared metrics')), \
             patch.object(protocol, 'sign_tail', side_effect=AssertionError('Shared metrics')):
            result = self.check()
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['hourly_marks'], 96)
        self.assertEqual(result['orders'], 2)
        self.assertEqual(result['complete_days'], 3)
        self.assertFalse(result['qualified'])

    def test_flat_days_stressed_costs_and_all_baselines(self):
        for model in ('cash', 'simple_ma', 'active_volume_continuation'):
            for fee, slip in (('0.0021', '0.001'), ('0.0042', '0.002')):
                with self.subTest(model=model, fee=fee):
                    result = protocol.simulate(self.bars, 48, 144, model, fee, slip)
                    self.assertEqual(self.check(result, fee, slip)['status'], 'PASS')
        result = protocol.simulate(self.bars, 48, 49, 'cash', self.fee, self.slip)
        checked = audit.audit_leg(self.bars, 48, 49, result, self.fee, self.slip, '1000', '0.25')
        self.assertEqual(checked['complete_days'], 0)

    def test_fee_price_size_and_ownership_tampering_rejected(self):
        for index, field, value in ((0, 'fee', '0'), (0, 'price', '1'),
                                   (0, 'quantity', '100'), (1, 'quantity', '1'),
                                   (0, 'side', 'sell'), (1, 'side', 'buy')):
            with self.subTest(field=field, index=index):
                result = copy.deepcopy(self.result)
                result['orders'][index][field] = value
                with self.assertRaises(ValueError):
                    self.check(result)

    def test_fill_chronology_duplicates_and_terminal_inventory_rejected(self):
        variants = [self.result['orders'] * 2, self.result['orders'][::-1],
                    self.result['orders'][:1], self.result['orders'][1:]]
        for orders in variants:
            result = copy.deepcopy(self.result)
            result['orders'] = orders
            with self.assertRaises(ValueError):
                self.check(result)
        for t in (0, self.result['end_ms'], self.result['start_ms'] + 1):
            result = copy.deepcopy(self.result)
            result['orders'][0]['time'] = t
            with self.assertRaises(ValueError):
                self.check(result)

    def test_sparse_changed_or_misaligned_equity_rejected(self):
        for mutation in ('missing', 'value', 'time', 'gap'):
            result = copy.deepcopy(self.result)
            data = copy.deepcopy(self.bars)
            if mutation == 'missing':
                result['equity'].pop(4)
            elif mutation == 'value':
                result['equity'][5]['equity'] = '1000'
            elif mutation == 'time':
                result['equity'][4]['time'] += audit.HOUR
            else:
                data[90]['time'] += audit.HOUR
            with self.assertRaises(ValueError):
                audit.audit_leg(data, 48, 144, result, self.fee, self.slip, '1000', '0.25')

    def test_altered_summary_daily_statistics_and_scope_rejected(self):
        for field, value in (('initial_capital', '999'), ('final_equity', '0'), ('net', '0'),
                             ('fees', '0'), ('maximum_closing_drawdown', '1'),
                             ('round_trips', 0), ('sign_test_p', 0), ('qualified', True),
                             ('execution', 'CALIBRATED'), ('start_ms', 0)):
            result = copy.deepcopy(self.result)
            result[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.check(result)
        result = copy.deepcopy(self.result)
        result['daily_returns'].pop()
        with self.assertRaises(ValueError):
            self.check(result)
        result = copy.deepcopy(self.result)
        result['daily_returns'][0]['return'] = '1'
        with self.assertRaises(ValueError):
            self.check(result)

    def test_unbounded_nonfinite_duplicate_and_boolean_evidence_rejected(self):
        for value in ('NaN', 'Infinity', '1e100', '1e-100000', '', ' 1', True, 1):
            with self.subTest(value=value), self.assertRaises((ValueError, ArithmeticError)):
                audit.number(value)
        for raw in ('{"a":1,"a":2}', '{"p":NaN}'):
            with self.assertRaises(ValueError):
                audit.decoded(raw)
        result = copy.deepcopy(self.result)
        result['round_trips'] = True
        with self.assertRaises(ValueError):
            self.check(result)


class SnapshotAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.epoch = epoch()
        self.now = self.epoch['end_ms'] + 60000

    def initialize(self, spot=True):
        con = cycle.connect(self.root)
        self.addCleanup(con.close)
        cycle.save_epoch(con, self.epoch)
        cycle.register(con, self.epoch, self.now)
        _, digest = cycle.load_epoch(con)
        for _ in range(5 if spot else 4):
            cycle.evaluate_next(con, self.epoch, digest, self.now)
        return con

    def test_actual_frozen_screen_readonly_all_eight_legs_no_simulator(self):
        self.initialize()
        before = (self.root / 'cycle.sqlite3').read_bytes()
        with patch.object(protocol, 'simulate', side_effect=AssertionError('No simulator')), \
             patch.object(protocol, 'signal', side_effect=AssertionError('No signal/holdout evaluation')):
            result = audit.audit(self.root)
        self.assertEqual(before, (self.root / 'cycle.sqlite3').read_bytes())
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['attempts_checked'], 5)
        self.assertEqual(result['completed_accounting_passed'], 1)
        self.assertEqual(result['product_models_not_applicable'], 4)
        self.assertEqual(result['legs_checked'], 8)
        self.assertEqual(result['hourly_marks_checked'], (1728 - 48) * 8)
        self.assertFalse(result['qualified'])
        self.assertEqual(result['reported_stages_sha256'],
                         audit.stage_fingerprint(cycle.status(self.root)['stages']))

    def test_product_blocked_only_is_not_green_accounting_evidence(self):
        self.initialize(spot=False)
        result = audit.audit(self.root)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['completed_accounting_passed'], 0)
        self.assertEqual(result['product_models_not_applicable'], 4)

    def test_state_missing_corrupt_unknown_or_modified_epoch_not_adopted(self):
        self.assertEqual(audit.audit(self.root)['status'], 'BLOCKED')
        self.assertFalse((self.root / 'cycle.sqlite3').exists())
        con = self.initialize()
        con.execute("UPDATE epoch SET sha256='changed'")
        con.commit()
        with self.assertRaises(ValueError):
            audit.audit(self.root)
        absent = self.root / 'other'
        absent.mkdir()
        sqlite3.connect(absent / 'cycle.sqlite3').close()
        with self.assertRaises(sqlite3.Error):
            audit.audit(absent)

    def test_missing_leg_changed_model_epoch_or_holdout_provenance_rejected(self):
        con = self.initialize()
        identity, raw = con.execute("SELECT variant,record FROM attempts WHERE status='SCREEN_REJECTED_TRAINING'").fetchone()
        original = json.loads(raw)
        for mutation in ('model', 'epoch', 'holdout', 'missing_leg', 'qualified', 'cash_trades'):
            record = copy.deepcopy(original)
            if mutation == 'model': record['model_sha256'] = '0' * 64
            elif mutation == 'epoch': record['epoch_sha256'] = '0' * 64
            elif mutation == 'holdout': record['holdout_consumed'] = True
            elif mutation == 'missing_leg': del record['phases']['train']['benchmarks']['cash']
            elif mutation == 'cash_trades':
                record['phases']['train']['benchmarks']['cash'] = record['phases']['train']['benchmarks']['buy_hold']
            else: record['historical_qualified'] = True
            con.execute('UPDATE attempts SET record=? WHERE variant=?', (json.dumps(record), identity))
            con.commit()
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                audit.audit(self.root)

    def test_unfinished_attempt_remains_blocked_not_silently_skipped(self):
        con = self.initialize()
        identity, raw = con.execute("SELECT variant,record FROM attempts WHERE status='SCREEN_REJECTED_TRAINING'").fetchone()
        record = json.loads(raw)
        record.update(status='STARTED', phases={})
        con.execute('UPDATE attempts SET status=?,record=? WHERE variant=?', ('STARTED', json.dumps(record), identity))
        con.commit()
        result = audit.audit(self.root)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['blocked_attempts'], 1)


if __name__ == '__main__':
    unittest.main()
