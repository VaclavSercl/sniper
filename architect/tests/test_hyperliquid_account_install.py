import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

DIRECTORY=Path(__file__).resolve().parents[2]/'infra/beroun'
sys.path.insert(0,str(DIRECTORY))
spec=importlib.util.spec_from_file_location('account_install',DIRECTORY/'install_hyperliquid_account.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class EvidenceTests(unittest.TestCase):
    def test_existing_shared_root_directory_preserved_without_chmod(self):
        self.assertTrue(module.compatible_directory(0,0,0o755,1001))
        self.assertTrue(module.compatible_directory(0,1001,0o750,1001))
        for uid,gid,mode in ((1000,0,0o755),(0,0,0o777),(0,0,0o775),(0,0,0o750),(0,1001,0o700)):
            self.assertFalse(module.compatible_directory(uid,gid,mode,1001))

    def test_exact_delegation_hash_and_no_secret_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'account-observation.json';role=root/'account-role.json'
            data={'status':'ACTUAL_ACCOUNT_OBSERVED_READ_ONLY','live_enabled':False,'signer':'0x'+'34'*20,
                'account':'0x'+'12'*20,'account_role':{'role':'user'},'irrelevant_field':'never-copied'}
            delegation={'signer':data['signer'],'role':{'role':'agent','data':{'user':data['account']}}}
            source.write_text(json.dumps(data));role.write_text(json.dumps(delegation))
            sha=hashlib.sha256(source.read_bytes()+b'\x00'+role.read_bytes()).hexdigest()
            with patch.object(module,'OPERATIONS',root):
                config=module.config_from_evidence(source,sha)
                self.assertEqual(set(config),{'schema','account','signer','account_role','evidence'})
                with self.assertRaises(ValueError):module.config_from_evidence(source,'b'*64)
                delegation['role']['data']['user']='0x'+'56'*20;role.write_text(json.dumps(delegation))
                sha=hashlib.sha256(source.read_bytes()+b'\x00'+role.read_bytes()).hexdigest()
                with self.assertRaises(ValueError):module.config_from_evidence(source,sha)

    def test_outside_source_refused(self):
        with self.assertRaises(ValueError):module.config_from_evidence(Path('/tmp/account-observation.json'),'a'*64)
