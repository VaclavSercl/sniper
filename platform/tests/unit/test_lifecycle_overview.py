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
from research_accounting_audit import stage_fingerprint


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

    def test_forward_runtime_missing_or_corrupt_cannot_claim_admission(self):
        result=exporter.load_report(self.root/'absent',forward_state_dir=self.root/'forward')
        self.assertEqual(result['forward_research']['status'],'MISSING')
        self.assertEqual(result['integration_health']['status'],'PARTIAL')
        self.assertEqual(result['lifecycle']['automatic_paper_admission'],'NOT_IMPLEMENTED')

    def test_observed_forward_counts_remain_separate_from_legacy_screens(self):
        from forward_pipeline import connect, generate
        from contextlib import closing
        from test_execution_evidence import MARKET
        import time
        root=self.root/'forward';now=int(time.time()*1000)
        with closing(connect(root)) as con:
            generate(con,[MARKET,{**MARKET,'id':'spot:OTHER/USDC','coin':'@124'}],now)
            with con:
                con.execute('INSERT INTO health VALUES(1,?,?,?)',(now,'PASS','{}'))
                con.execute('INSERT INTO health VALUES(2,?,?,?)',(now,'PASS','{}'))
        result=exporter.load_report(self.root/'absent',forward_state_dir=root)
        self.assertEqual(result['lifecycle']['counts']['forward_economic_mechanisms'],2)
        self.assertEqual(result['lifecycle']['counts']['forward_market_variants'],12)
        self.assertEqual(result['paper_qualified'],0)
        self.assertEqual(result['lifecycle']['automatic_paper_admission'],'IMPLEMENTED_MEASURED_BOOK_HISTORICAL_GATES')
        self.assertEqual(result['lifecycle']['automatic_live_promotion'],'NOT_IMPLEMENTED')

    def test_cli_partial_is_nonzero_without_network(self):
        with life.database(self.root): pass
        with patch.object(sys, 'argv', ['sync', '--state-dir', str(self.root)]), \
             patch('subprocess.run', side_effect=AssertionError('External invocation')), \
             patch('builtins.print'):
            self.assertEqual(exporter.main(), 2)

    def test_new_economic_blueprints_and_variants_counted_without_qualification(self):
        h=self.root/'h';c=self.root/'c'
        with life.database(h):pass
        self.candle(c,[self.run_record()])
        data={'status':'RESEARCH_OBSERVED','blueprints_registered':2,'registered_market_variants':12,
            'completed_screens':4,'blocked_product_models':8,'generator':'FINITE_OFFLINE_BLUEPRINT_LIBRARY_NO_PAID_MODEL',
            'new_blueprints_per_utc_day':2,'max_variants_per_blueprint':6,'max_primary_test_bundles_per_utc_day':12,
            'blueprints_remaining':4,'exhaustion_policy':'NEED_NEW_BLUEPRINTS_OR_PROVIDER_BUDGET',
            'epoch_sha256':'a'*64,'stages':[{'variant':str(i)} for i in range(12)]}
        with patch('research_cycle.status',return_value=data), \
             patch('research_accounting_audit.audit',return_value={'status':'PASS','qualified':False,
                'epoch_sha256':'a'*64,'attempts_checked':12,
                'reported_stages_sha256':stage_fingerprint(data['stages'])}):
            result=exporter.load_report(h,c,research_state_dir=self.root/'r')
        counts=result['lifecycle']['counts']
        self.assertEqual(counts['registered_families_with_runs'],3)
        self.assertEqual(counts['new_economic_blueprints_registered'],2)
        self.assertEqual(counts['new_market_variants'],12);self.assertEqual(counts['live_eligible'],0)
        self.assertEqual(result['integration_health']['status'],'OBSERVED')

    def test_broken_new_registry_keeps_older_observations_and_marks_partial(self):
        h=self.root/'h';c=self.root/'c'
        with life.database(h):pass
        self.candle(c,[self.run_record()])
        with patch('research_cycle.status',side_effect=ValueError('private runtime detail')):
            result=exporter.load_report(h,c,research_state_dir=self.root/'r')
        self.assertEqual(result['lifecycle']['counts']['candle_variants_registered'],1)
        self.assertEqual(result['integration_health']['status'],'PARTIAL')
        self.assertNotIn('private runtime detail',json.dumps(result))

    def test_accounting_failure_blocks_integration_without_hiding_research(self):
        data={'status':'RESEARCH_OBSERVED','blueprints_registered':2,'registered_market_variants':12,
            'completed_screens':4,'blocked_product_models':8,'generator':'FINITE_OFFLINE_BLUEPRINT_LIBRARY_NO_PAID_MODEL',
            'new_blueprints_per_utc_day':2,'max_variants_per_blueprint':6,'max_primary_test_bundles_per_utc_day':12,
            'blueprints_remaining':4,'exhaustion_policy':'NEED_NEW_BLUEPRINTS_OR_PROVIDER_BUDGET',
            'epoch_sha256':'a'*64,'stages':[{'variant':str(i)} for i in range(12)]}
        with patch('research_cycle.status',return_value=data), \
             patch('research_accounting_audit.audit',side_effect=ValueError('private evidence contents')):
            result=exporter.load_report(self.root/'h',research_state_dir=self.root/'r')
        self.assertEqual(result['hyperliquid_research'],data)
        self.assertEqual(result['integration_health']['status'],'PARTIAL')
        self.assertIn('research_accounting',result['integration_health']['unavailable_or_stale'])
        self.assertEqual(result['research_accounting']['qualified'],False)
        self.assertNotIn('private evidence contents',json.dumps(result))

    def test_accounting_different_snapshot_cannot_attest_report(self):
        data={'status':'RESEARCH_OBSERVED','blueprints_registered':0,'registered_market_variants':0,
            'completed_screens':0,'blocked_product_models':0,'generator':'FINITE_OFFLINE_BLUEPRINT_LIBRARY_NO_PAID_MODEL',
            'new_blueprints_per_utc_day':2,'max_variants_per_blueprint':6,'max_primary_test_bundles_per_utc_day':12,
            'blueprints_remaining':6,'exhaustion_policy':'NEED_NEW_BLUEPRINTS_OR_PROVIDER_BUDGET',
            'epoch_sha256':'a'*64,'stages':[]}
        for audit in ({'status':'PASS','epoch_sha256':'b'*64,'attempts_checked':0},
                      {'status':'PASS','epoch_sha256':'a'*64,'attempts_checked':1},
                      {'status':'PASS','epoch_sha256':'a'*64,'attempts_checked':0,
                       'reported_stages_sha256':'0'*64},
                      {'status':'BLOCKED','epoch_sha256':'a'*64,'attempts_checked':0,
                       'reported_stages_sha256':stage_fingerprint([])}):
            with patch('research_cycle.status',return_value=data), \
                 patch('research_accounting_audit.audit',return_value=audit):
                result=exporter.load_report(self.root/'h',research_state_dir=self.root/'r')
            self.assertEqual(result['integration_health']['status'],'PARTIAL')


if __name__ == '__main__': unittest.main()
