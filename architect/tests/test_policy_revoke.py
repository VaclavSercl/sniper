import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'infra/beroun'))
import revoke_service_grant as policy


class PolicyTests(unittest.TestCase):
    def test_preview_and_recoverable_disable(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'fixture.rules';raw=b'// synthetic rule\n';path.write_bytes(raw)
            digest=hashlib.sha256(raw).hexdigest()
            result=policy.revoke(path,digest)
            self.assertEqual(result['status'],'PREVIEW');self.assertTrue(path.exists())
            with patch.object(policy.os,'geteuid',return_value=0,create=True):
                result=policy.revoke(path,digest,True)
            self.assertFalse(path.exists())
            self.assertEqual(Path(result['recovery']).read_bytes(),raw)

    def test_changed_rule_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'fixture.rules';path.write_bytes(b'owner edit')
            with self.assertRaises(ValueError):policy.revoke(path,'0'*64,True)
            self.assertEqual(path.read_bytes(),b'owner edit')


if __name__=='__main__':unittest.main()
