from datetime import datetime, timedelta, timezone
import hashlib
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
import operational_observation as obs
import strategy_lifecycle as life
import sync_strategy_registry as exporter


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.now = datetime.now(timezone.utc)
        self.row = {'candidate': 0, 'day': '2026-09-30', 'status': 'BASELINE_SCREEN_COMPLETE',
            'policy_sha256': 'a'*64, 'model_sha256': 'b'*64, 'holdout_used': True,
            'private_input': 'NEVER_PUBLISH', 'params': {'private': 'NEVER_PUBLISH'},
            'result': {'training': {'net_positive_under_assumptions': False, 'trades': ['NEVER_PUBLISH']},
                       'holdout': {'net_positive_under_assumptions': False}}}
        self.data = {'status': 'OBSERVED', 'runs': [self.row]}
        self.audit = {'status': 'OBSERVED', 'qualified': False, 'candles': [], 'funding': []}

    def observation(self):
        with patch('candle_research.status', return_value=self.data):
            return obs.build(self.root, self.now, lambda: self.audit)

    def save(self, value=None):
        path = self.root/'public.json'; path.write_bytes(obs.encode(value or self.observation())); return path

    def test_private_inputs_never_published_and_no_qualification(self):
        value = self.observation()
        self.assertNotIn(b'NEVER_PUBLISH', obs.encode(value))
        body = obs.load(self.save(value), self.now)
        self.assertEqual(body['candle']['live_eligible'], 0)
        self.assertEqual(body['candle']['runs'][0]['candidate'], 0)

    def test_old_future_changed_and_invalid_observations_rejected(self):
        path = self.save()
        for now in (self.now+timedelta(seconds=601), self.now-timedelta(seconds=31)):
            with self.assertRaises(ValueError): obs.load(path, now)
        value = self.observation(); value['body']['candle']['runs'][0]['candidate'] = 1
        with self.assertRaises(ValueError): obs.load(self.save(value), self.now)
        value['sha256'] = hashlib.sha256(obs.encode(value['body'])).hexdigest()
        value['body']['candle']['runs'][0]['candidate'] = True
        value['sha256'] = hashlib.sha256(obs.encode(value['body'])).hexdigest()
        with self.assertRaises(ValueError): obs.load(self.save(value), self.now)

    def test_report_reads_both_sources_without_writing_private_database(self):
        hydra = self.root/'hydra'
        with life.database(hydra): pass
        private = hydra/'research.sqlite3'; before = private.read_bytes()
        report = exporter.load_report(hydra, candle_observation=self.save())
        self.assertEqual(report['status'], 'OBSERVED')
        self.assertEqual(report['lifecycle']['counts']['candle_screens_negative'], 1)
        self.assertEqual(before, private.read_bytes())
        self.assertEqual(report['paper_qualified'], 0)

    def test_corrupt_or_missing_observation_preserves_independent_source(self):
        with life.database(self.root): pass
        path = self.root/'missing.json'
        report = exporter.load_report(self.root, candle_observation=path)
        self.assertEqual(report['status'], 'PARTIAL'); self.assertFalse(path.exists())
        self.assertIsNone(report['lifecycle']['counts']['candle_screens_complete'])
        path.write_bytes(b'{"body":0,"body":1}')
        report = exporter.load_report(self.root, candle_observation=path)
        self.assertEqual(report['lifecycle']['coverage']['candle']['status'], 'FAILED')

    def test_symlinks_and_oversized_observation_refused(self):
        path = self.save(); link = self.root/'link'
        try: link.symlink_to(path)
        except OSError: self.skipTest('Symlink capability unavailable')
        with self.assertRaises(ValueError): obs.load(link, self.now)
        path.write_bytes(b'x'*(obs.LIMIT+1))
        with self.assertRaises(ValueError): obs.load(path, self.now)

    def test_audit_is_explicitly_read_only_and_errors_not_exposed(self):
        with patch.object(obs.subprocess, 'run') as run:
            run.return_value.returncode = 1; run.return_value.stderr = 'PRIVATE_CONNECTION'
            with self.assertRaisesRegex(RuntimeError, '^Read-only market audit failed$'): obs.audit_market()
            args = run.call_args
            self.assertIn('READ ONLY', args.kwargs['input'])
            self.assertIn('default_transaction_read_only=on', args.kwargs['env']['PGOPTIONS'])


if __name__ == '__main__': unittest.main()
