"""Pure deployment planning regressions; never access systemd or a database."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import contextlib
import io
import json
import sys
import tempfile
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('cutover',ROOT/'infra/beroun/plan_cutover.py')
cutover=importlib.util.module_from_spec(spec)
spec.loader.exec_module(cutover)

class CutoverTests(unittest.TestCase):
    def test_source_changes_but_persistent_state_stays(self):
        value='/usr/bin/python3 /opt/beroun/services/beroun_gateway.py --state /opt/beroun/state'
        self.assertEqual(cutover.rewrite(value),'/usr/bin/python3 /opt/sniper/current/platform/runtime/beroun_gateway.py --state /opt/beroun/state')

    def test_dropin_resets_execstart_and_preserves_arguments(self):
        unit='[Unit]\nDescription=Original\n[Service]\nUser=beroun\nExecStart=/usr/bin/python3 /opt/beroun/repo/scripts/fetch_klines.py --once\n'
        self.assertEqual(cutover.render(unit),'[Service]\nExecStart=\nExecStart=/usr/bin/python3 /opt/sniper/current/platform/legacy/scripts/fetch_klines.py --once\n')

    def test_unrelated_unit_unchanged(self):
        self.assertIsNone(cutover.render('[Service]\nExecStart=/usr/bin/other\n'))

    def test_working_directory_is_mapped(self):
        self.assertIn('WorkingDirectory=/opt/sniper/current/platform',
                      cutover.render('[Service]\nWorkingDirectory=/home/wwwenda/beroun-github\n'))

    def test_multiline_refused(self):
        with self.assertRaises(ValueError):
            cutover.render('[Service]\nExecStart=/opt/beroun/repo/start.sh '+chr(92)+'\n --next\n')

class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        sys.path.insert(0,str(ROOT/'infra/beroun'))
        self.addCleanup(lambda:sys.path.remove(str(ROOT/'infra/beroun')))
        apply_spec=importlib.util.spec_from_file_location('tested_apply',ROOT/'infra/beroun/apply_cutover.py')
        self.app=importlib.util.module_from_spec(apply_spec)
        apply_spec.loader.exec_module(self.app)
        self.source=self.root/'source';(self.source/'platform').mkdir(parents=True)
        (self.source/'platform/SOURCE_MANIFEST.json').write_text('{}')
        data=b'print("fixture")\n'
        (self.source/'platform/app.py').write_bytes(data)
        self.sha='a'*40
        self.release={'commit':self.sha,'files':{
            'platform/SOURCE_MANIFEST.json':self.app.digest(b'{}'),
            'platform/app.py':self.app.digest(data)},
            'modes':{'platform/SOURCE_MANIFEST.json':0o644,'platform/app.py':0o644}}
        (self.source/'RELEASE.json').write_text(json.dumps(self.release))
        self.units=self.root/'units';self.units.mkdir()
        unit=self.units/'beroun-gateway.service'
        unit.write_text('[Service]\nExecStart=/usr/bin/python3 /opt/beroun/services/beroun_gateway.py\n')
        self.plan={'schema_version':1,'canonical_repository':'VaclavSercl/sniper',
                   'enable_funded_trading':False,'source_manifest_sha256':self.app.digest(b'{}'),
                   'units':[{'unit':unit.name,'fragment':str(unit),
                             'fragment_sha256':self.app.digest(unit.read_bytes()),
                             'dropin':self.app.render(unit.read_text())}]}
        self.planpath=self.root/'plan.json';self.planpath.write_text(json.dumps(self.plan))
        self.base=self.root/'installed'
        self.calls=[]
        self.fail_restart=False

    def fake_run(self,*args,check=True):
        self.calls.append(args)
        if args[1]=='is-enabled':return SimpleNamespace(stdout='enabled\n')
        if args[1]=='is-active':return SimpleNamespace(stdout='active\n')
        if args[1]=='show':
            return SimpleNamespace(stdout='/opt/sniper/current/platform/runtime/beroun_gateway.py' if 'ExecStart' in args else '')
        if args[1]=='restart' and self.fail_restart:
            self.fail_restart=False
            raise RuntimeError('fixture restart failure')
        return SimpleNamespace(stdout='')

    def execute(self):
        with patch.object(self.app,'BASE',self.base),patch.object(self.app,'SYSTEMD',self.units), \
             patch.object(self.app,'UNITS',('beroun-gateway.service',)), \
             patch.object(self.app.os,'geteuid',return_value=0,create=True), \
             patch.object(self.app,'run',side_effect=self.fake_run), \
             patch.object(sys,'argv',['apply','--source',str(self.source),'--plan',str(self.planpath),
                                    '--commit',self.sha,'--apply']),contextlib.redirect_stdout(io.StringIO()):
            self.app.main()

    def test_apply_only_changes_managed_paths(self):
        self.execute()
        self.assertTrue((self.base/'current').is_symlink())
        self.assertTrue((self.units/'beroun-gateway.service.d/90-sniper-source.conf').exists())
        self.assertTrue((self.units/'beroun-gateway.service').read_text().endswith('/opt/beroun/services/beroun_gateway.py\n'))
        self.assertIn(('systemctl','disable','--now','beroun-paper-t15.timer'),self.calls)

    def test_restart_failure_restores_previous_paths(self):
        self.fail_restart=True
        with self.assertRaises(RuntimeError):self.execute()
        self.assertFalse((self.base/'current').is_symlink())
        self.assertFalse((self.units/'beroun-gateway.service.d/90-sniper-source.conf').exists())
        result=json.loads((self.base/'operations'/(self.sha+'.json')).read_text())
        self.assertEqual(result['status'],'ROLLED_BACK')

    def test_modified_release_refused_before_mutation(self):
        (self.source/'platform/app.py').write_text('changed')
        with self.assertRaises(ValueError):self.execute()
        self.assertFalse(self.base.exists())

    def test_absolute_release_path_refused(self):
        name=str(self.source/'platform/app.py')
        self.release['files'][name]=self.release['files'].pop('platform/app.py')
        self.release['modes'][name]=0o644
        (self.source/'RELEASE.json').write_text(json.dumps(self.release))
        with self.assertRaises(ValueError):self.execute()
        self.assertFalse(self.base.exists())

if __name__=='__main__':unittest.main()
