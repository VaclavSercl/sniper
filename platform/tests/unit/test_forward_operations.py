"""Real private SQLite WAL backup/restore and fault checks; no production paths."""
from contextlib import closing
import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
import zlib
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'research'))
import forward_operations as ops
import forward_pipeline as pipeline
import forward_worker as worker

NOW=1790879900000


@unittest.skipUnless(os.name=='posix','Private Linux ownership/locking contract')
class Operations(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)/'epoch'
        self.con=pipeline.connect(self.root)
        with self.con:
            self.con.executemany('INSERT INTO health VALUES(?,?,?,?)',[(1,NOW,'PASS','{}'),(2,NOW,'PASS','{}')])
            self.con.executemany('INSERT INTO books VALUES(?,?,?,?,?,?,?)',
                [('hype',NOW,NOW,NOW,'20','hash',b'data'),('btc',NOW,NOW,NOW,'20','hash',b'data')])
    def tearDown(self):
        self.con.close();self.tmp.cleanup()
    def test_complete_maintenance_under_normal_umask_and_repeat(self):
        old=os.umask(0o022)
        try:
            result=ops.maintain(self.root,NOW)
            self.assertEqual(result['status'],'PASS')
            self.assertTrue(result['backup']['restore_verified'])
            self.assertEqual(result['alert']['status'],'TRANSITION_RECORDED')
            for path in (self.root/'operations',self.root/'operations/backups',self.root/'operations/alerts'):
                self.assertEqual(path.stat().st_mode&0o777,0o700)
            folder=Path(result['backup']['path'])
            proof=(folder/'result.json').read_bytes();database=ops.digest(folder/'forward.sqlite3')
            again=ops.maintain(self.root,NOW)
            self.assertEqual(again['status'],'PASS')
            self.assertEqual(again['backup']['status'],'ALREADY_VERIFIED_TODAY')
            self.assertEqual(again['alert']['status'],'UNCHANGED')
            self.assertEqual((folder/'result.json').read_bytes(),proof)
            self.assertEqual(ops.digest(folder/'forward.sqlite3'),database)
            self.assertEqual(self.con.execute('PRAGMA journal_mode').fetchone(),('wal',))
        finally:os.umask(old)
    def test_unsafe_existing_operations_parent_is_never_adopted(self):
        parent=self.root/'operations';parent.mkdir();parent.chmod(0o755)
        marker=parent/'unowned.txt';marker.write_text('preserve')
        for callback in (lambda:ops.backup(self.root,NOW),lambda:ops.maintain(self.root,NOW)):
            with self.assertRaisesRegex(ValueError,'Private owned operation path required'):callback()
            self.assertEqual(parent.stat().st_mode&0o777,0o755)
            self.assertEqual(marker.read_text(),'preserve')
            self.assertEqual({p.name for p in parent.iterdir()},{'unowned.txt'})
    def test_real_research_work_reaches_final_health_under_normal_umask(self):
        from test_execution_evidence import MARKET
        markets=[MARKET,{**MARKET,'id':'spot:OTHER/USDC','coin':'@124','symbol':'OTHER/USDC','asset_id':10124}]
        pipeline.generate(self.con,markets,NOW)
        value={'markets':markets}
        diagnostic={'status':'CANDLE_DIAGNOSTIC_ONLY','source_epoch':'synthetic-fixture',
                    'phases':{'validation':{'stress':{'net_return':'0'}}},'qualified':False}
        with self.con:
            self.con.execute('INSERT INTO metadata VALUES(?,?,?)',
                (worker.ex.sha(value),NOW,zlib.compress(worker.history.encode(value))))
            for identity, in self.con.execute('SELECT id FROM candidates').fetchall():
                self.con.execute('INSERT INTO screens VALUES(?,?,?)',
                    (identity,diagnostic['status'],worker.history.encode(diagnostic).decode()))
        importer=worker.import_calibration;old=os.umask(0o022)
        try:
            with patch.object(worker.time,'time',return_value=NOW/1000),patch.object(worker,'import_calibration',
                    side_effect=lambda con,now:importer(con,now,self.root/'missing-calibration.json')):
                result=worker.research_work(self.con,self.root/'history',self.root/'old',NOW)
            self.assertEqual(result['generation']['status'],'DAILY_QUOTA')
            self.assertEqual(result['operations']['status'],'PASS')
            self.assertTrue(result['operations']['backup']['restore_verified'])
            self.assertEqual(result['operations']['alert']['status'],'TRANSITION_RECORDED')
            self.assertEqual(result['calibration']['status'],'BLOCKED')
            self.assertEqual(self.con.execute('SELECT status FROM health WHERE id=2').fetchone(),('PASS',))
            self.assertEqual([self.con.execute('SELECT count(*) FROM '+table).fetchone()[0]
                              for table in ('trials','paper','orders')],[0,0,0])
        finally:os.umask(old)
    def test_wal_consistent_backup_and_real_restore_without_replacing_writer(self):
        result=ops.backup(self.root,NOW)
        self.assertTrue(result['restore_verified'])
        self.assertEqual(result['snapshot']['counts']['books'],2)
        folder=Path(result['path']);self.assertFalse((folder/'restore-probe').exists())
        self.assertEqual(result['restore_proof']['snapshot'],result['snapshot'])
        with self.con:self.con.execute('INSERT INTO health VALUES(3,?,?,?)',(NOW,'PASS','{}'))
        restored=self.root.parent/'restored';ops.restore(folder,restored)
        with closing(ops.readonly(restored/'forward.sqlite3')) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM health').fetchone()[0],2)
        self.assertEqual(self.con.execute('SELECT count(*) FROM health').fetchone()[0],3)
        with self.assertRaises(ValueError):ops.restore(folder,self.root)
        self.assertEqual(ops.backup(self.root,NOW)['status'],'ALREADY_VERIFIED_TODAY')

    def test_backup_and_restored_copies_are_standalone_while_source_stays_wal(self):
        self.assertEqual(self.con.execute('PRAGMA journal_mode').fetchone(),('wal',))
        result=ops.backup(self.root,NOW);folder=Path(result['path'])
        restored=self.root.parent/'standalone';ops.restore(folder,restored)
        for copy_root in (folder,restored):
            with closing(ops.readonly(copy_root/'forward.sqlite3')) as con:
                self.assertEqual(con.execute('PRAGMA journal_mode').fetchone(),('delete',))
                self.assertEqual(ops.snapshot(con),result['snapshot'])
            self.assertFalse((copy_root/'forward.sqlite3-wal').exists())
            self.assertFalse((copy_root/'forward.sqlite3-shm').exists())
        self.assertEqual(self.con.execute('PRAGMA journal_mode').fetchone(),('wal',))
        with self.con:self.con.execute('INSERT INTO health VALUES(3,?,?,?)',(NOW,'PASS','{}'))
        self.assertEqual(self.con.execute('SELECT count(*) FROM health').fetchone()[0],3)
    def test_corrupted_backup_is_rejected_without_destination(self):
        result=ops.backup(self.root,NOW);folder=Path(result['path'])
        with (folder/'forward.sqlite3').open('ab') as stream:stream.write(b'changed')
        target=self.root.parent/'restore'
        with self.assertRaises(ValueError):ops.restore(folder,target)
        self.assertFalse(target.exists())
    def test_backup_budget_does_not_delete_or_stop_existing_state(self):
        before=self.con.execute('SELECT count(*) FROM books').fetchone()
        with self.assertRaises(ValueError):ops.backup(self.root,NOW,max_backup_bytes=1)
        self.assertEqual(self.con.execute('SELECT count(*) FROM books').fetchone(),before)
    def test_future_and_stale_heartbeats_alert_and_transition_deduplication(self):
        healthy=ops.inspect(self.root,NOW);self.assertEqual(healthy['status'],'PASS')
        self.assertEqual(ops.record_alert(self.root,healthy)['status'],'TRANSITION_RECORDED')
        self.assertEqual(ops.record_alert(self.root,healthy)['status'],'UNCHANGED')
        bad=ops.inspect(self.root,NOW-1);self.assertEqual(bad['status'],'ALERT')
        self.assertEqual(ops.record_alert(self.root,bad)['status'],'TRANSITION_RECORDED')
        self.assertIn('CAPTURE_STALE_FAILED_OR_FUTURE',ops.inspect(self.root,NOW+61000)['reasons'])
    def test_unknown_schema_backup_preserves_failed_intent(self):
        with self.con:self.con.execute('CREATE TABLE unexpected(x)')
        with self.assertRaises(ValueError):ops.backup(self.root,NOW)
        intents=list((self.root/'operations/backups').glob('*/intent.json'))
        self.assertEqual(len(intents),1);self.assertFalse(intents[0].with_name('result.json').exists())
        with self.assertRaises(ValueError):ops.backup(self.root,NOW+86400000)
        self.assertEqual(len(list((self.root/'operations/backups').glob('*/intent.json'))),1)

    def test_three_days_retain_two_verified_databases_and_all_evidence(self):
        copies=[ops.backup(self.root,NOW+day*86400000) for day in range(3)]
        first=Path(copies[0]['path'])
        self.assertFalse((first/'forward.sqlite3').exists())
        self.assertTrue((first/'intent.json').is_file());self.assertTrue((first/'result.json').is_file())
        self.assertTrue((first/'pruned.json').is_file())
        archive=self.root/'operations/backups'
        self.assertEqual(len(list(archive.glob('*/forward.sqlite3'))),2)
        self.assertEqual(len(list(archive.glob('*/result.json'))),3)
        self.assertEqual(ops.backup(self.root,NOW+2*86400000)['status'],'ALREADY_VERIFIED_TODAY')

    def test_changed_old_copy_blocks_pruning_and_new_backup(self):
        result=ops.backup(self.root,NOW);folder=Path(result['path'])
        (folder/'forward.sqlite3').write_bytes(b'changed')
        with self.assertRaises(ValueError):ops.backup(self.root,NOW+86400000)
        self.assertEqual((folder/'forward.sqlite3').read_bytes(),b'changed')
        self.assertEqual(len(list((self.root/'operations/backups').glob('*/intent.json'))),1)

    def test_unknown_archive_artifact_is_preserved(self):
        result=ops.backup(self.root,NOW);folder=Path(result['path'])
        marker=folder/'unowned.txt';marker.write_text('foreign');marker.chmod(0o600)
        with self.assertRaises(ValueError):ops.backup(self.root,NOW+86400000)
        self.assertEqual(marker.read_text(),'foreign')

    def test_interrupted_pruning_never_replays_automatically(self):
        result=ops.backup(self.root,NOW);folder=Path(result['path'])
        ops.durable(folder/'prune-intent.json',{'interrupted':True})
        with self.assertRaises(ValueError):ops.backup(self.root,NOW+86400000)
        self.assertTrue((folder/'forward.sqlite3').exists())

    def test_running_health_is_internal_only_and_never_external_pass(self):
        with self.con:self.con.execute("UPDATE health SET status='RUNNING' WHERE id=2")
        self.assertEqual(ops.inspect(self.root,NOW)['status'],'ALERT')
        self.assertEqual(ops.inspect(self.root,NOW,research_started_ms=NOW)['status'],'PASS')
        self.assertEqual(ops.inspect(self.root,NOW,research_started_ms=NOW-1)['status'],'ALERT')

    def test_abrupt_maintenance_stop_preserves_running_health(self):
        with patch.object(worker,'stored_markets',return_value=[]),patch.object(worker.proposals,'generate',return_value={}),patch.object(
                worker.pipeline,'screen_pending',return_value=0),patch.object(worker,'import_calibration',return_value={}),patch.object(
                worker.operations,'maintain',side_effect=SystemExit()),self.assertRaises(SystemExit):
            worker.research_work(self.con,self.root/'history',self.root/'old',NOW)
        self.assertEqual(self.con.execute('SELECT status FROM health WHERE id=2').fetchone(),('RUNNING',))
        self.assertEqual(ops.inspect(self.root,NOW)['status'],'ALERT')

    def test_once_research_failure_does_not_overwrite_capture_pass(self):
        root=self.root.parent/'once'
        def capture(con,*_):
            with con:con.execute('INSERT OR REPLACE INTO health VALUES(1,?,?,?)',(NOW,'PASS','{}'))
        with patch.object(worker,'capture_work',side_effect=capture),patch.object(worker,'research_work',side_effect=RuntimeError()),patch.object(
                sys,'argv',['worker','once','--state-dir',str(root)]),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(worker.main(),2)
        with closing(pipeline.connect(root)) as con:
            self.assertEqual(dict(con.execute('SELECT id,status FROM health')),{1:'PASS',2:'FAILED'})
    def test_symlink_cannot_redirect_backup_or_alert_state(self):
        outside=self.root.parent/'outside';outside.mkdir()
        (self.root/'operations').symlink_to(outside,target_is_directory=True)
        with self.assertRaises(ValueError):ops.record_alert(self.root,ops.inspect(self.root,NOW))
        with self.assertRaises(ValueError):ops.backup(self.root,NOW)
        self.assertEqual(list(outside.iterdir()),[])


if __name__=='__main__':unittest.main()
