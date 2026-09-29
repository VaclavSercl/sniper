import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'infra/beroun'))
import update_release as update


class ReleaseDriftTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); (self.root/'platform').mkdir()
        self.file = self.root/'platform/app.py'; self.file.write_bytes(b'# reviewed\n'); self.file.chmod(0o644)
        (self.root/'RELEASE.json').write_text(json.dumps({'files': {'platform/app.py': update.digest(self.file.read_bytes())},
                                                       'modes': {'platform/app.py': 0o644}}))

    def test_drift_changes_bound_identity(self):
        clean = update.inspect_release(self.root)
        self.assertEqual(clean['drift'], [])
        self.file.write_bytes(b'# production repair\n')
        changed = update.inspect_release(self.root)
        self.assertNotEqual(clean['tree_sha256'], changed['tree_sha256'])
        self.assertEqual(changed['drift'], ['platform/app.py'])
        self.assertEqual(changed, update.inspect_release(self.root))

    def test_unknown_source_and_missing_file_block(self):
        extra=self.root/'platform/unknown.py';extra.write_bytes(b'# unknown\n')
        with self.assertRaises(ValueError): update.inspect_release(self.root)
        extra.unlink();self.file.unlink()
        with self.assertRaises(ValueError): update.inspect_release(self.root)

    def test_generated_cache_is_explicitly_excluded(self):
        before=update.inspect_release(self.root)
        cache=self.root/'platform/__pycache__';cache.mkdir();(cache/'app.pyc').write_bytes(b'fixture')
        self.assertEqual(before, update.inspect_release(self.root))

    def test_known_backup_requires_original_source_bytes(self):
        source=self.root/'platform/scripts/perfect_market_ingest.py'
        source.parent.mkdir();source.write_bytes(b'# original\n');source.chmod(0o644)
        m=json.loads((self.root/'RELEASE.json').read_text())
        m['files']['platform/scripts/perfect_market_ingest.py']=update.digest(source.read_bytes())
        m['modes']['platform/scripts/perfect_market_ingest.py']=0o644
        (self.root/'RELEASE.json').write_text(json.dumps(m))
        backup=source.with_suffix('.py.bak');backup.write_bytes(source.read_bytes())
        result=update.inspect_release(self.root)
        self.assertIn('platform/scripts/perfect_market_ingest.py.bak',result['preserved_backups'])
        backup.write_bytes(b'# unrelated\n')
        with self.assertRaises(ValueError): update.inspect_release(self.root)


if __name__=='__main__': unittest.main()
