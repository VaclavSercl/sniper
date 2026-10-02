"""Isolated source/target refusal; root permissions are verified on actual install."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

DIRECTORY=Path(__file__).resolve().parents[2]/'infra/beroun'
sys.path.insert(0,str(DIRECTORY))
spec=importlib.util.spec_from_file_location('mandate_install',DIRECTORY/'install_hyperliquid_mandate.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class InstallationTests(unittest.TestCase):
    def test_release_identity_and_source_drift_refused_without_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);release=root/'releases'/('a'*40);release.mkdir(parents=True)
            try:(root/'current').symlink_to(release,target_is_directory=True)
            except OSError as exc:self.skipTest('Requires Linux symlinks: '+type(exc).__name__)
            (release/'RELEASE.json').write_text(json.dumps({'commit':'b'*40,'files':{}}))
            with patch.object(module,'BASE',root),self.assertRaises(ValueError):module.prepare('a'*40)
            (release/'RELEASE.json').write_text(json.dumps({'commit':'a'*40,'files':{'platform/research/hyperliquid_mandate.py':'b'*64}}))
            path=release/'platform/research/hyperliquid_mandate.py';path.parent.mkdir(parents=True);path.write_text('# changed')
            with patch.object(module,'BASE',root),self.assertRaises(ValueError):module.prepare('a'*40)
            self.assertFalse((root/'operations').exists())

    def test_unknown_existing_target_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);target=root/'mandate.json';target.write_text('owner existing bytes')
            with patch.object(module,'CONFIG',target),self.assertRaises(ValueError):module.prepare('a'*40)
            # The path checker rejects symlink escape; no deletion/unlink helper
            # exists in this absent-target installation.
            link=root/'link.json'
            try:link.symlink_to(target)
            except OSError as exc:self.skipTest('Requires Linux symlinks: '+type(exc).__name__)
            with self.assertRaises(ValueError):module.checked(link)
            self.assertEqual(target.read_text(),'owner existing bytes')

    def test_unprivileged_apply_rejected_before_any_operation(self):
        with patch.object(module.os,'geteuid',return_value=1000),self.assertRaises(ValueError):
            module.apply('a'*40)


if __name__=='__main__':unittest.main()
