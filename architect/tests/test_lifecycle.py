import json
from pathlib import Path
import sqlite3
import struct
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'architect'))
sys.path.insert(0,str(ROOT/'platform/research'))
sys.path.insert(0,str(ROOT/'platform/scripts'))
import strategy_lifecycle as life
import sync_strategy_registry as exporter
import safe_boot
import orchestration
sys.path.insert(0,str(ROOT/'infra/beroun'))
import update_release

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.now=datetime(2026,9,29,tzinfo=timezone.utc)

    def test_daily_idempotence_and_exhaustion(self):
        with life.database(self.root) as con:
            first=life.propose(con,self.now)
            self.assertEqual(first['id'],life.propose(con,self.now)['id'])
            for i in range(1,12):self.assertEqual(life.propose(con,self.now+timedelta(days=i))['status'],'PROPOSED')
            self.assertEqual(life.propose(con,self.now+timedelta(days=12))['status'],'SEARCH_SPACE_EXHAUSTED')
            self.assertEqual(len(life.report(con)['proposals']),12)

    def test_missing_data_persists_blocked_and_never_retries_as_pass(self):
        with life.database(self.root) as con:
            life.propose(con,self.now)
            result=life.evaluate(con,self.root,self.root/'missing',self.root/'split',self.root/'policy',self.now)
            self.assertEqual(result['status'],'BLOCKED')
            self.assertFalse(result['live_eligible'])
            self.assertEqual(life.evaluate(con,self.root,None,None,None,self.now)['status'],'NO_PENDING_PROPOSALS')

    def test_cost_policy_no_implicit_rebate_or_nan(self):
        p=self.root/'policy'
        for fee in [-0.001,float('nan'),True,1]:
            p.write_text(json.dumps({'maker_fee':fee,'initial_capital':1000,'cost_basis':'fixture'}))
            with self.assertRaises(ValueError):life.policy_values(p)

    def test_actual_simulator_empty_database_cannot_pass(self):
        data=self.root/'market.db'
        with sqlite3.connect(data) as con:
            con.execute('CREATE TABLE candles_1m(exchange,symbol,ts,open,high,low,close,volume)')
            con.execute('CREATE TABLE ticks(exchange,symbol,ts_ms,price,qty,side,trade_id)')
        split=self.root/'split';split.write_text(json.dumps({'is_start_ms':1,'is_end_ms':100,'oos_start_ms':100,'oos_end_ms':200}))
        policy=self.root/'policy';policy.write_text(json.dumps({'maker_fee':0.001,'initial_capital':1000,'cost_basis':'fixture only'}))
        with life.database(self.root) as con:
            life.propose(con,self.now)
            result=life.evaluate(con,self.root,data,split,policy,self.now)
            self.assertEqual(result['status'],'RESEARCH_REJECTED')
            self.assertIn('data_sha256',result)
            self.assertTrue((self.root/result['data_artifact']).is_file())
            self.assertFalse(result['paper_eligible'])

    def test_retry_preserves_failure_and_repair_budget(self):
        with life.database(self.root) as con:
            identity=life.propose(con,self.now)['id']
            for n in range(3):
                life.evaluate(con,self.root,self.root/'data',self.root/'split',self.root/'policy',self.now)
                self.assertEqual(life.retry_blocked(con,identity,self.now)['prior_attempts'],n+1)
            life.evaluate(con,self.root,self.root/'data',self.root/'split',self.root/'policy',self.now)
            with self.assertRaises(ValueError):life.retry_blocked(con,identity,self.now)
            self.assertEqual(con.execute('SELECT count(*) FROM prior_evaluations').fetchone()[0],3)

    def test_exporter_never_adopts_old_t15_or_publishes(self):
        with life.database(self.root) as con:life.propose(con,self.now)
        with patch('subprocess.run',side_effect=AssertionError('External mutation')):
            result=exporter.load_report(self.root)
        self.assertEqual(result['live_eligible'],0)
        self.assertEqual(result['legacy_t15'],'INVALIDATED_NOT_QUALIFICATION_EVIDENCE')
        self.assertEqual(exporter.load_report(self.root/'missing')['status'],'BLOCKED')

    def test_database_lock_prevents_duplicate_writer(self):
        with life.database(self.root) as con:
            con.execute('BEGIN IMMEDIATE')
            other=sqlite3.connect(self.root/'research.sqlite3',timeout=0)
            try:
                with self.assertRaises(sqlite3.OperationalError):life.propose(other,self.now)
            finally:other.close();con.rollback()

    def test_boot_missing_evidence_rejects(self):
        with patch.object(safe_boot,'PROJECT_ROOT',str(self.root)):
            result = safe_boot.SafeBootPipeline('hydra').run_phase2_wfa()
            self.assertFalse(result['ok'])

    def test_boot_pause_enforced_before_state_and_no_spawn(self):
        risk=self.root/'risk';risk.write_bytes(bytes(16))
        state=self.root/'state.json';state.write_text(json.dumps({'hydra':{'mode':'LIVE'}}))
        with patch.object(safe_boot,'BOT_RISK_MAP',{'hydra':(str(risk),0)}),patch.object(safe_boot,'STATE_FILE',str(state)),patch('subprocess.Popen',side_effect=AssertionError('Warmup spawn')):
            safe_boot.SafeBootPipeline('hydra').enforce_paper_state()
        self.assertEqual(struct.unpack('<Q',risk.read_bytes()[:8])[0],1)
        self.assertEqual(json.loads(state.read_text())['hydra']['mode'],'PAPER')

    def test_missing_ipc_is_failure_not_paper_claim(self):
        state=self.root/'state.json'
        with patch.object(safe_boot,'BOT_RISK_MAP',{'hydra':(str(self.root/'missing'),0)}),patch.object(safe_boot,'STATE_FILE',str(state)):
            with self.assertRaises(RuntimeError):safe_boot.SafeBootPipeline('hydra').enforce_paper_state()
        self.assertFalse(state.exists())

    def test_legacy_live_mode_write_rejected(self):
        with self.assertRaises(RuntimeError):orchestration.save_bot_state('hydra','LIVE')

    def test_release_validation_rejects_tamper_and_escape(self):
        p=self.root/'platform/test.py';p.parent.mkdir();p.write_bytes(b'pass\n')
        metadata={'commit':'a'*40,'files':{'platform/test.py':life.file_hash(p)},'modes':{'platform/test.py':0o644}}
        update_release.validate(self.root,metadata)
        p.write_bytes(b'changed\n')
        with self.assertRaises(ValueError):update_release.validate(self.root,metadata)
        metadata['files']={'../escape':'bad'};metadata['modes']={'../escape':0o644}
        with self.assertRaises(ValueError):update_release.validate(self.root,metadata)

    def test_future_split_is_not_evaluated(self):
        policy=self.root/'policy';policy.write_text(json.dumps({'maker_fee':0.001,'initial_capital':1000,'cost_basis':'fixture'}))
        split=self.root/'split';split.write_text(json.dumps({'oos_end_ms':int((self.now+timedelta(days=1)).timestamp()*1000)}))
        with life.database(self.root) as con:
            life.propose(con,self.now)
            result=life.evaluate(con,self.root,self.root/'data',split,policy,self.now)
            self.assertEqual(result['status'],'BLOCKED')
            self.assertNotIn('out_of_sample',result)

    @unittest.skipIf(sys.platform=='win32','Deployment symlinks require Linux')
    def test_source_update_and_health_failure_rollback(self):
        import os
        import subprocess
        for fail in (False,True):
            with tempfile.TemporaryDirectory() as directory:
                base=Path(directory)/'installed';source=Path(directory)/'source'
                previous=base/'releases'/('b'*40);previous.mkdir(parents=True)
                (base/'operations').mkdir();(base/'current').symlink_to(previous)
                p=source/'platform/test.py';p.parent.mkdir(parents=True);p.write_bytes(b'pass\n')
                meta={'commit':'a'*40,'files':{'platform/test.py':life.file_hash(p)},'modes':{'platform/test.py':0o644}}
                raw=json.dumps(meta).encode();(source/'RELEASE.json').write_bytes(raw)
                argv=['update','--source',str(source),'--expected-current','b'*40,'--manifest-sha256',life.sha(raw),'--apply']
                def switch(target):
                    temp=base/'pending';temp.symlink_to(target);os.replace(temp,base/'current')
                def health(*args):
                    if fail:raise subprocess.CalledProcessError(1,args)
                with patch.object(sys,'argv',argv),patch.object(update_release,'BASE',base),patch.object(update_release,'switch',switch),patch.object(update_release,'run',health),patch('os.geteuid',return_value=0):
                    if fail:
                        with self.assertRaises(subprocess.CalledProcessError):update_release.main()
                    else:update_release.main()
                record=json.loads((base/'operations'/('a'*40+'-update.json')).read_text())
                self.assertEqual(record['status'],'ROLLED_BACK' if fail else 'DEPLOYED_SOURCE_ONLY')
                self.assertEqual((base/'current').readlink(),previous if fail else base/'releases'/('a'*40))
                self.assertFalse((base/'operations'/'source-update.lock').exists())

if __name__=='__main__':unittest.main()
