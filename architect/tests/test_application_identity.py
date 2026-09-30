"""Linux temporary-filesystem installer tests; systemd/root ownership are mocked."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'infra/beroun'))
if hasattr(os,'geteuid'):
    import install_application_identity as app


@unittest.skipUnless(hasattr(os,'geteuid'),'Requires Linux ownership/symlink capability')
class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.base=self.root/'base';self.units=self.root/'units';self.state=self.root/'state'
        self.sha='d'*40;self.release=self.base/'releases'/self.sha
        self.release.mkdir(parents=True);self.units.mkdir();self.state.mkdir();(self.base/'operations').mkdir()
        (self.state/'research.sqlite3').write_bytes(b'private registry fixture')
        (self.base/'current').symlink_to(self.release,target_is_directory=True)
        for name in app.SERVICES:
            (self.units/(name+'.service')).write_bytes(b'[Service]\nUser=wwwenda\n')
            directory=self.units/(name+'.service.d');directory.mkdir()
            (directory/'90-sniper-source.conf').write_bytes(b'previous source override')
            if name=='beroun-strategy-registry-sync':(directory/'95-sniper-observation.conf').write_bytes(b'previous observation')
        hashes={}
        for name in (app.OVERRIDE,'96-sniper-application-report.conf'):
            relative='infra/beroun/'+name;p=self.release/relative;p.parent.mkdir(parents=True,exist_ok=True)
            p.write_bytes((ROOT/relative).read_bytes());hashes[relative]=app.digest(p.read_bytes())
        (self.release/'RELEASE.json').write_text(json.dumps({'commit':self.sha,'files':hashes}))
        for name,value in (('BASE',self.base),('UNITS',self.units),('STATE',self.state)):
            context=patch.object(app,name,value);context.start();self.addCleanup(context.stop)
        account=SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid())
        context=patch.object(app.pwd,'getpwnam',return_value=account);context.start();self.addCleanup(context.stop)
        real_entry=app.entry
        def entry(path):
            result=real_entry(path)
            if path.is_relative_to(self.units):result['uid']=0
            return result
        context=patch.object(app,'entry',side_effect=entry);context.start();self.addCleanup(context.stop)

    def test_preview_is_read_only_and_protects_guardian_and_administration(self):
        before={p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        preview=app.preview(self.sha)
        self.assertFalse(preview['live_activated'])
        self.assertNotIn('beroun-risk_kernel',preview['services'])
        self.assertNotIn('beroun-watchdog',preview['services'])
        self.assertEqual(before,{p:p.read_bytes() for p in before})

    def test_unknown_overrides_symlink_and_source_change_refused(self):
        extra=self.units/(app.SERVICES[0]+'.service.d')/'owner.conf';extra.write_bytes(b'owner change')
        with self.assertRaises(ValueError):app.preview(self.sha)
        extra.unlink();target=self.state/'link';target.symlink_to(self.root/'units')
        with self.assertRaises(ValueError):app.preview(self.sha)
        target.unlink();(self.release/'infra/beroun'/app.OVERRIDE).write_bytes(b'changed')
        with self.assertRaises(ValueError):app.preview(self.sha)

    def test_changed_preview_no_side_effects(self):
        preview=app.preview(self.sha);expected=app.digest(app.encode(preview))
        (self.state/'research.sqlite3').write_bytes(b'owner newer state')
        with patch.object(app.os,'geteuid',return_value=0),patch.object(app,'run') as run:
            with self.assertRaises(ValueError):app.install(self.sha,expected)
        run.assert_not_called()
        self.assertFalse((self.base/'operations'/('application-identity-'+self.sha)).exists())

    def test_success_preserves_private_bytes_inactive_t15_and_prior_units(self):
        preview=app.preview(self.sha);calls=[]
        def run(argv):
            calls.append(argv)
            if argv[0]=='runuser':return 'beroun'
            if argv[:2]==['systemctl','show']:return 'beroun'
            return ''
        with patch.object(app.os,'geteuid',return_value=0),patch.object(app,'active',return_value=False), \
                patch.object(app,'run',side_effect=run):
            result=app.install(self.sha,app.digest(app.encode(preview)))
        self.assertEqual(result['status'],'APPLICATION_WORKERS_CONSOLIDATED')
        self.assertTrue(result['disabled_t15_preserved'])
        self.assertEqual((self.state/'research.sqlite3').read_bytes(),b'private registry fixture')
        self.assertEqual((self.state/'research.sqlite3').stat().st_mode & 0o777,0o600)
        self.assertFalse(any(argv==['systemctl','start','beroun-paper-t15.service'] for argv in calls))
        for name in app.SERVICES:
            self.assertEqual((self.units/(name+'.service')).read_bytes(),b'[Service]\nUser=wwwenda\n')
        op=self.base/'operations'/('application-identity-'+self.sha)
        self.assertTrue((op/'intent.json').is_file());self.assertTrue((op/'result.json').is_file())
        self.assertEqual(len(list((op/'recovery').iterdir())),len(preview['before']['configuration'])+1)


if __name__=='__main__':unittest.main()
