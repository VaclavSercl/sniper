import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'infra/beroun'))
import install_observation as installer


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.base = self.root/'opt'; self.units = self.root/'units'
        self.sha = 'c'*40; self.release = self.base/'releases'/self.sha
        self.release.mkdir(parents=True); self.units.mkdir(); (self.base/'operations').mkdir()
        (self.root/'state').mkdir()
        try: (self.base/'current').symlink_to(self.release, target_is_directory=True)
        except OSError: self.skipTest('Symlink capability unavailable')
        self.drop = self.units/(installer.REPORT+'.d'); self.drop.mkdir()
        self.old_unit = self.units/installer.REPORT; self.old_unit.write_bytes(b'original unit')
        self.old_override = self.drop/'90-sniper-source.conf'; self.old_override.write_bytes(b'original override')
        hashes = {}
        for name in ('sniper-observation.service','sniper-observation.timer','95-sniper-observation.conf'):
            relative = 'infra/beroun/'+name; dest = self.release/relative; dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes((ROOT/relative).read_bytes()); hashes[relative] = installer.digest(dest.read_bytes())
        (self.release/'RELEASE.json').write_text(json.dumps({'commit': self.sha, 'files': hashes}))
        for key,value in (('BASE',self.base),('UNITS',self.units),('STATE',self.root/'state/observations')):
            context = patch.object(installer,key,value); context.start(); self.addCleanup(context.stop)

    def preview(self):
        return installer.preview(self.sha,installer.digest(b'original unit'),installer.digest(b'original override'))

    def test_preview_no_mutation_preserves_original_configuration(self):
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result = self.preview(); self.assertFalse(result['live_activated'])
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertFalse(installer.STATE.exists())

    def test_existing_or_unknown_override_changed_source_and_state_block(self):
        path = self.drop/'unexpected.conf'; path.write_text('owner')
        with self.assertRaises(ValueError): self.preview()
        path.unlink(); self.old_override.write_bytes(b'owner change')
        with self.assertRaises(ValueError): self.preview()
        self.old_override.write_bytes(b'original override')
        installer.STATE.mkdir()
        with self.assertRaises(ValueError): self.preview()
        self.assertTrue(installer.STATE.is_dir())

    def test_install_source_fingerprint_mismatch_blocks(self):
        (self.release/'infra/beroun/sniper-observation.service').write_bytes(b'changed')
        with self.assertRaises(ValueError): self.preview()

    def test_failed_first_report_is_durable_and_never_enables_timer(self):
        calls = []
        def run(argv):
            calls.append(argv)
            if argv[0] == 'runuser': return json.dumps({'status':'PARTIAL','live_eligible':0})
            return ''
        with patch.object(installer.os,'geteuid',return_value=0), patch.object(installer,'run',side_effect=run), \
                patch.object(installer.subprocess,'run') as stop:
            stop.return_value.returncode = 0
            with self.assertRaises(ValueError):
                installer.install(self.sha,installer.digest(b'original unit'),installer.digest(b'original override'))
        result = json.loads((self.base/'operations'/('observation-install-'+self.sha)/'result.json').read_bytes())
        self.assertEqual(result['status'],'FAILED_PRESERVED_FOR_RECONCILIATION')
        self.assertFalse(any('enable' in argv for argv in calls))
        self.assertEqual(self.old_unit.read_bytes(),b'original unit')
        self.assertEqual(self.old_override.read_bytes(),b'original override')


if __name__ == '__main__': unittest.main()
