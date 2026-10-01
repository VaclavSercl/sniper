import json
import os
from pathlib import Path
import sys
import subprocess
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'infra/beroun'))
import install_forward_research as installer


@unittest.skipUnless(os.name=='posix','Real Linux installation/symlink fixtures are verified on Linux')
class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.sha='a'*40;self.base=self.root/'opt'
        release=self.base/'releases'/self.sha;release.mkdir(parents=True)
        (self.base/'current').symlink_to(release,target_is_directory=True)
        files={}
        for name in tuple('infra/beroun/'+n for n in installer.NAMES)+tuple('platform/research/'+n for n in ('forward_worker.py','forward_pipeline.py','forward_reference.py','execution_evidence.py','strategy_recipes.py')):
            p=release/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((ROOT/name).read_bytes());files[name]=installer.digest(p.read_bytes())
        (release/'RELEASE.json').write_text(json.dumps({'commit':self.sha,'files':files}))
        self.units=self.root/'units';self.units.mkdir();self.state=self.root/'state'
        self.addCleanup(patch.stopall)
        patch.object(installer,'BASE',self.base).start();patch.object(installer,'UNIT',self.units/installer.UNIT.name).start();patch.object(installer,'STATE',self.state).start()
        # Existing private archives belong to the runtime integration check;
        # this fixture isolates preview target/source ownership behavior.
        self.actual_checked=installer.checked
        def checked(p):
            if str(p).startswith('/var/lib/sniper/'):
                f=self.root/Path(p).name
                if not f.exists():f.write_bytes(b'fixture')
                return f
            return self.actual_checked(p)
        patch.object(installer,'checked',side_effect=checked).start();patch.object(installer,'run',return_value='').start()

    def test_preview_only_and_no_unit_or_state_creation(self):
        data=installer.preview(self.sha)
        self.assertEqual(len(data['units']),3);self.assertFalse(data['exchange_writes'])
        self.assertEqual(list(self.units.iterdir()),[]);self.assertFalse(self.state.exists())

    def test_existing_unit_override_or_epoch_is_preserved(self):
        for name in installer.NAMES:
            p=self.units/name;p.write_text('foreign')
            with self.assertRaises(ValueError):installer.preview(self.sha)
            self.assertEqual(p.read_text(),'foreign');p.unlink()
        override=self.units/(installer.NAMES[0]+'.d');override.mkdir()
        with self.assertRaises(ValueError):installer.preview(self.sha)
        override.rmdir();self.state.mkdir()
        with self.assertRaises(ValueError):installer.preview(self.sha)

    def test_release_source_drift_is_blocked(self):
        p=self.base/'releases'/self.sha/'platform/research/forward_worker.py';p.write_text('changed')
        with self.assertRaises(ValueError):installer.preview(self.sha)

    def test_symlink_target_escape_is_blocked(self):
        outside=self.root/'outside';outside.write_text('foreign')
        (self.units/installer.NAMES[0]).symlink_to(outside)
        with self.assertRaises(ValueError):installer.preview(self.sha)
        self.assertEqual(outside.read_text(),'foreign')


class StartupReadinessTests(unittest.TestCase):
    def setUp(self):
        self.clock=0.0
        self.addCleanup(patch.stopall)
        patch.object(installer.time,'monotonic',side_effect=lambda:self.clock).start()
        patch.object(installer.time,'time',return_value=1000).start()
        def sleep(seconds):self.clock+=seconds
        self.sleep=patch.object(installer.time,'sleep',side_effect=sleep).start()
        self.command=['fixture-worker','status']

    def result(self,body,code=2):
        return subprocess.CompletedProcess(self.command,code,json.dumps(body),'')

    def healthy(self):
        return {'status':'STALE_OR_FAILED','counts':{'books':2},
                'last_health':{'status':'PASS','time_ms':1000000}}

    def test_initial_missing_and_initial_empty_store_wait_for_real_capture(self):
        sequence=[self.result({'status':'MISSING','qualified':False}),
                  self.result({'status':'STALE_OR_FAILED','counts':{'books':0},'last_health':None}),
                  self.result(self.healthy())]
        with patch.object(installer.subprocess,'run',side_effect=sequence) as run:
            data=installer.wait_for_capture(self.command)
        self.assertEqual(data['counts']['books'],2)
        self.assertEqual(run.call_count,3);self.assertEqual(self.clock,4)
        self.assertTrue(all(call.kwargs['timeout']<=20 for call in run.call_args_list))

    def test_missing_deadline_fails_and_never_becomes_ready(self):
        with patch.object(installer.subprocess,'run',return_value=self.result({'status':'MISSING','qualified':False})) as run:
            with self.assertRaisesRegex(ValueError,'deadline exhausted'):
                installer.wait_for_capture(self.command)
        self.assertEqual(self.clock,20);self.assertEqual(run.call_count,10)

    def test_malformed_failed_stale_and_false_success_reports_fail_immediately(self):
        reports=[({},2),([],2),({'status':'MISSING','qualified':False},0),
                 ({'status':'STALE_OR_FAILED','counts':{'books':True},'last_health':None},2),
                 ({'status':'STALE_OR_FAILED','counts':{'books':2},'last_health':{'status':'FAILED','time_ms':1000000}},2),
                 ({'status':'STALE_OR_FAILED','counts':{'books':2},'last_health':{'status':'PASS','time_ms':900000}},2)]
        for body,code in reports:
            with self.subTest(body=body),patch.object(installer.subprocess,'run',return_value=self.result(body,code)) as run:
                with self.assertRaises(ValueError):installer.wait_for_capture(self.command)
                self.assertEqual(run.call_count,1)
        self.sleep.assert_not_called()

    def test_command_failure_invalid_json_and_timeout_are_not_hidden(self):
        for result in (self.result(self.healthy(),1),subprocess.CompletedProcess(self.command,2,'invalid json','')):
            with patch.object(installer.subprocess,'run',return_value=result),self.assertRaises(ValueError):
                installer.wait_for_capture(self.command)
        with patch.object(installer.subprocess,'run',side_effect=subprocess.TimeoutExpired(self.command,20)):
            with self.assertRaises(subprocess.TimeoutExpired):installer.wait_for_capture(self.command)
        self.sleep.assert_not_called()


@unittest.skipUnless(os.name=='posix','Real Linux filesystem recovery fixtures')
class RecoveryTests(unittest.TestCase):
    def setUp(self):
        import pwd
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.sha='a'*40;self.previous='b'*40
        self.base=self.root/'opt';self.units=self.root/'units';self.units.mkdir()
        self.state=self.root/'state';self.state.mkdir(mode=0o700)
        release=self.base/'releases'/self.sha;release.mkdir(parents=True)
        (self.base/'current').symlink_to(release,target_is_directory=True)
        self.sources={}
        for name in tuple('infra/beroun/'+n for n in installer.NAMES)+tuple('platform/research/'+n for n in ('forward_worker.py','forward_pipeline.py','forward_reference.py','execution_evidence.py','strategy_recipes.py')):
            p=release/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((ROOT/name).read_bytes())
            self.sources[name]=installer.digest(p.read_bytes())
        (release/'RELEASE.json').write_text(json.dumps({'commit':self.sha,'files':self.sources}))
        for name in installer.NAMES:
            p=self.units/name;p.write_bytes((ROOT/'infra/beroun'/name).read_bytes());p.chmod(0o644)
        operations=self.base/'operations';operations.mkdir()
        lock=operations/'forward-research-install.lock';lock.touch();lock.chmod(0o600)
        self.old=operations/('forward-research-install-'+self.previous);self.old.mkdir(mode=0o700)
        self.intent={'release':self.previous,'state':str(self.state),'user':'beroun',
            'units':[str(self.units/n) for n in installer.NAMES],'sources':self.sources,
            'live_activated':False,'exchange_writes':False}
        self.failure={'status':'FAILED_PRESERVED_FOR_RECONCILIATION','error_type':'KeyError',
            'units_created':list(installer.NAMES),'stop_exit':0,'screen_stop_exit':0,'state_preserved':True}
        for name,value in (('intent.json',self.intent),('result.json',self.failure)):
            p=self.old/name;p.write_text(json.dumps(value));p.chmod(0o600)
        self.db=self.state/'forward.sqlite3';self.db.write_bytes(b'preserved epoch fixture');self.db.chmod(0o600)
        self.original_db=self.db.read_bytes();self.original_failure=(self.old/'result.json').read_bytes()
        self.counts={'families':0,'candidates':0,'screens':0,'trials':0,'paper':0,'orders':0,'books':2}
        self.initial={'status':'STALE_OR_FAILED','counts':self.counts,'source_sha256':'fixture-model',
            'last_health':{'status':'PASS','time_ms':999000},'live_eligible':0}
        self.addCleanup(patch.stopall)
        patch.object(installer,'BASE',self.base).start();patch.object(installer,'UNIT',self.units/installer.NAMES[0]).start()
        patch.object(installer,'STATE',self.state).start();patch.object(installer.os,'geteuid',return_value=0).start()
        patch.object(pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=os.getuid())).start()
        # The temporary filesystem is real; only root ownership is simulated.
        original_stat=Path.stat
        def stat(path,*args,**kwargs):
            value=original_stat(path,*args,**kwargs)
            if path.is_relative_to(operations) or path.is_relative_to(self.units):
                fields=list(value);fields[4]=0;return os.stat_result(fields)
            return value
        patch.object(Path,'stat',stat).start()
        def run(argv,timeout=30):
            if argv[:2]==['systemctl','show']:
                name=argv[2]
                return 'FragmentPath='+str(self.units/name)+'\nDropInPaths=\nActiveState=inactive\nUnitFileState='+('static' if name==installer.NAMES[1] else 'disabled')+'\n'
            return ''
        self.run=patch.object(installer,'run',side_effect=run).start()
        self.report=patch.object(installer,'status_report',return_value=self.initial).start()
        self.clock=0.0
        patch.object(installer.time,'monotonic',side_effect=lambda:self.clock).start()
        patch.object(installer.time,'time',return_value=1000).start()
        def sleep(seconds):self.clock+=seconds
        patch.object(installer.time,'sleep',side_effect=sleep).start()
        patch.object(installer.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'','')).start()

    def assert_preserved(self):
        self.assertEqual(self.db.read_bytes(),self.original_db)
        self.assertEqual((self.old/'result.json').read_bytes(),self.original_failure)
        self.assertTrue(all((self.units/n).read_bytes()==(ROOT/'infra/beroun'/n).read_bytes() for n in installer.NAMES))

    def test_read_only_recovery_preview_retains_failed_operation_and_epoch(self):
        value=installer.recovery_preview(self.sha,self.previous)
        self.assertEqual(value['counts']['books'],2);self.assertTrue(value['state_preserved'])
        self.assertFalse(value['exchange_writes']);self.assert_preserved()
        self.assertFalse((self.base/'operations'/('forward-research-recovery-'+self.sha)).exists())
        self.assertTrue(all(call.args[0][:2]==['systemctl','show'] for call in self.run.call_args_list))

    def test_unknown_unit_bytes_overrides_and_state_files_are_blocked(self):
        unit=self.units/installer.NAMES[0];raw=unit.read_bytes();unit.write_bytes(b'foreign')
        with self.assertRaises(ValueError):installer.recovery_preview(self.sha,self.previous)
        self.assertEqual(unit.read_bytes(),b'foreign');unit.write_bytes(raw)
        override=self.units/(installer.NAMES[0]+'.d');override.mkdir()
        with self.assertRaises(ValueError):installer.recovery_preview(self.sha,self.previous)
        override.rmdir();unknown=self.state/'unknown';unknown.write_text('foreign')
        with self.assertRaises(ValueError):installer.recovery_preview(self.sha,self.previous)
        self.assertEqual(unknown.read_text(),'foreign');self.assert_preserved()

    def test_altered_failure_record_model_or_epoch_cannot_be_adopted(self):
        failure=self.old/'result.json';failure.write_text(json.dumps({**self.failure,'error_type':'Other'}))
        with self.assertRaises(ValueError):installer.recovery_preview(self.sha,self.previous)
        failure.write_bytes(self.original_failure)
        model=self.base/'releases'/self.sha/'platform/research/forward_worker.py';raw=model.read_bytes();model.write_bytes(b'changed')
        with self.assertRaises(ValueError):installer.recovery_preview(self.sha,self.previous)
        model.write_bytes(raw);self.report.return_value={**self.initial,'counts':{**self.counts,'paper':1}}
        with self.assertRaises(ValueError):installer.recovery_preview(self.sha,self.previous)
        self.assert_preserved()

    def test_success_requires_fresh_capture_and_screen_before_enable(self):
        fresh={**self.initial,'counts':{**self.counts,'books':4},'last_health':{'status':'PASS','time_ms':1000000}}
        observed={**fresh,'status':'OBSERVED','counts':{**fresh['counts'],'families':2,'candidates':12,'screens':12}}
        self.report.side_effect=[self.initial,self.initial,self.initial,fresh,observed]
        result=installer.recover_install(self.sha,self.previous)
        self.assertEqual(result['status'],'RECOVERED_PUBLIC_CAPTURE_GENERATION_SCREENS_ACTIVE')
        commands=[call.args[0] for call in self.run.call_args_list]
        screen=commands.index(['systemctl','start',installer.NAMES[1]])
        enable=commands.index(['systemctl','enable',installer.NAMES[0],installer.NAMES[2]])
        self.assertLess(screen,enable);self.assertEqual(self.clock,1);self.assert_preserved()
        self.assertEqual(json.loads((self.base/'operations'/('forward-research-recovery-'+self.sha)/'result.json').read_bytes()),result)

    def test_failed_capture_stops_only_owned_units_and_preserves_data(self):
        failed={**self.initial,'last_health':{'status':'FAILED','time_ms':1000000}}
        self.report.side_effect=[self.initial,self.initial,failed]
        result=installer.recover_install(self.sha,self.previous)
        self.assertEqual(result['status'],'FAILED_RECOVERY_PRESERVED_FOR_RECONCILIATION')
        self.assertFalse(any(call.args[0][:2]==['systemctl','enable'] for call in self.run.call_args_list))
        calls=installer.subprocess.run.call_args_list
        self.assertEqual(calls[0].args[0],['systemctl','disable','--now',installer.NAMES[0],installer.NAMES[2]])
        self.assertEqual(calls[1].args[0],['systemctl','stop',installer.NAMES[1]])
        self.assert_preserved()

    def test_old_books_and_heartbeat_never_count_as_fresh_readiness(self):
        result=installer.recover_install(self.sha,self.previous)
        self.assertEqual(result['status'],'FAILED_RECOVERY_PRESERVED_FOR_RECONCILIATION')
        self.assertEqual(self.clock,20);self.assert_preserved()
        self.assertFalse(any(call.args[0][:2]==['systemctl','enable'] for call in self.run.call_args_list))


if __name__=='__main__':unittest.main()
