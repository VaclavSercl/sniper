import json
import os
from pathlib import Path
import sys
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


if __name__=='__main__':unittest.main()
