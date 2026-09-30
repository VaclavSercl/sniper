import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'platform/research'))
sys.path.insert(0, str(ROOT/'platform/scripts'))
import lifecycle_overview as view
import sync_strategy_registry as exporter
import strategy_lifecycle as life


class OverviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.policy, self.digest = view.load_policy()

    def candle(self, root, runs):
        root.mkdir()
        con = sqlite3.connect(root/'candle-research.sqlite3')
        try:
            con.execute('CREATE TABLE runs(day TEXT, record TEXT)')
            con.executemany('INSERT INTO runs VALUES (?,?)', [(r['day'], json.dumps(r)) for r in runs])
            con.commit()
        finally: con.close()

    def run_record(self, state='BASELINE_SCREEN_COMPLETE', positive=False):
        return {'candidate': 0, 'day': '2026-09-30', 'status': state, 'holdout_used': True,
                'result': {'training': {'net_positive_under_assumptions': positive},
                           'holdout': {'net_positive_under_assumptions': positive}}}

    def test_rejected_baseline_counted_once_no_qualification(self):
        h = self.root/'hydra'; c = self.root/'candle'
        with life.database(h): pass
        self.candle(c, [self.run_record()])
        before = {p: p.read_bytes() for p in (h/'research.sqlite3', c/'candle-research.sqlite3')}
        result = exporter.load_report(h, c)
        self.assertEqual(result['status'], 'OBSERVED')
        counts = result['lifecycle']['counts']
        self.assertEqual(counts['catalog_entries'], 12)
        self.assertEqual(counts['registered_families_with_runs'], 1)
        self.assertEqual(counts['candle_variants_registered'], 1)
        self.assertEqual(counts['candle_screens_negative'], 1)
        self.assertEqual(counts['t1_variants_not_yet_registered'], 17)
        self.assertEqual(result['lifecycle']['candle_stages'][0]['stage'], 'SCREEN_REJECTED')
        self.assertEqual(counts['paper_qualified'], 0)
        self.assertIsNone(counts['legacy_paper_running'])
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_positive_screen_does_not_become_paper_or_live_pass(self):
        c = self.root/'c'; self.candle(c, [self.run_record(positive=True)])
        result = exporter.load_report(self.root/'absent', c)
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertEqual(result['lifecycle']['counts']['candle_screens_complete'], 1)
        self.assertIsNone(result['lifecycle']['counts']['hydra_proposals'])
        self.assertFalse(result['lifecycle']['candle_stages'][0]['live_eligible'])
        self.assertEqual(result['lifecycle']['automatic_live_promotion'], 'NOT_IMPLEMENTED')

    def test_missing_sources_do_not_create_state_or_claim_zero(self):
        result = exporter.load_report(self.root/'missing-h', self.root/'missing-c')
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertIsNone(result['lifecycle']['counts']['candle_screens_complete'])
        self.assertFalse((self.root/'missing-h').exists())
        self.assertFalse((self.root/'missing-c').exists())

    def test_omitted_candle_explicitly_unknown(self):
        with life.database(self.root): pass
        result = exporter.load_report(self.root)
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertEqual(result['lifecycle']['coverage']['candle']['status'], 'NOT_REQUESTED')
        self.assertIsNone(result['lifecycle']['counts']['candle_variants_registered'])

    def test_corrupt_hydra_does_not_hide_candle_result(self):
        h = self.root/'h'; h.mkdir(); (h/'research.sqlite3').write_bytes(b'invalid')
        c = self.root/'c'; self.candle(c, [self.run_record()])
        result = exporter.load_report(h, c)
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertEqual(result['lifecycle']['coverage']['hydra']['status'], 'FAILED')
        self.assertEqual(result['lifecycle']['counts']['candle_variants_registered'], 1)

    def test_permission_failure_not_empty_success(self):
        with life.database(self.root): pass
        with patch('candle_research.status', side_effect=PermissionError('do not expose private path')):
            result = exporter.load_report(self.root, self.root/'c')
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertEqual(result['lifecycle']['coverage']['candle'],
                         {'status': 'FAILED', 'error_type': 'PermissionError'})
        self.assertNotIn('private path', json.dumps(result))

    def test_unknown_duplicate_or_incomplete_runs_fail_closed(self):
        for index, runs in enumerate(([self.run_record(), self.run_record()],
                                      [self.run_record('PASS')],
                                      [{'candidate': 0, 'day': '2026-09-30', 'status': 'BASELINE_SCREEN_COMPLETE'}])):
            c = self.root/str(index); self.candle(c, runs)
            with self.assertRaises((ValueError, KeyError)):
                exporter.load_report(self.root/'absent', c)

    def test_blocked_and_started_are_not_completed(self):
        a = self.run_record('BLOCKED'); b = self.run_record('STARTED')
        b.update(candidate=1, day='2026-10-01')
        c = self.root/'c'; self.candle(c, [a, b])
        counts = exporter.load_report(self.root/'missing', c)['lifecycle']['counts']
        self.assertEqual(counts['candle_screens_complete'], 0)
        self.assertEqual(counts['candle_runs_blocked'], 1)
        self.assertEqual(counts['candle_runs_interrupted'], 1)

    def test_policy_cannot_claim_targets_are_running_or_enable_orders(self):
        mutations = [('capacity_proposal', 'status', 'ENABLED'),
                     ('paper_evidence', 'minimum_calendar_days', 29),
                     ('live', 'implementation', 'ENABLED'),
                     ('live', 'total_capital', 1000),
                     ('historical_evidence', 'preserve_stricter_strategy_rules', False)]
        for section, key, value in mutations:
            p = copy.deepcopy(self.policy); p[section][key] = value
            path = self.root/'policy.json'; path.write_text(json.dumps(p))
            with self.assertRaises(ValueError): view.load_policy(path)

    def test_duplicate_keys_and_nonfinite_policy_refused(self):
        p = self.root/'policy'
        for data in ('{"schema":1,"schema":1}', '{"value":NaN}'):
            p.write_text(data)
            with self.assertRaises(ValueError): view.load_policy(p)

    def test_cli_partial_is_nonzero_without_network(self):
        with life.database(self.root): pass
        with patch.object(sys, 'argv', ['sync', '--state-dir', str(self.root)]), \
             patch('subprocess.run', side_effect=AssertionError('External invocation')), \
             patch('builtins.print'):
            self.assertEqual(exporter.main(), 2)


if __name__ == '__main__': unittest.main()
