from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'infra/beroun'))
import install_research as install


@unittest.skipIf(sys.platform=='win32','Actual deployment path/symlink checks require Linux')
class ResearchInstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);self.base=root/'source';self.units=root/'units';self.config=root/'config';self.state=root/'state'
        self.sha='a'*40;self.release=self.base/'releases'/self.sha
        (self.release/'infra/beroun').mkdir(parents=True);(self.base/'operations').mkdir();self.units.mkdir()
        (self.base/'current').symlink_to(self.release)
        files={}
        for name in ['sniper-research.service','sniper-research.timer','candle-research-policy.json']:
            relative='infra/beroun/'+name;raw=('reviewed fixture '+name).encode()
            (self.release/relative).write_bytes(raw);files[relative]=hashlib.sha256(raw).hexdigest()
        (self.release/'RELEASE.json').write_text(json.dumps({'commit':self.sha,'files':files}))
        for key,value in [('BASE',self.base),('UNITS',self.units),('CONFIG',self.config),('STATE',self.state)]:
            p=patch.object(install,key,value);p.start();self.addCleanup(p.stop)

    def test_preview_is_read_only_and_existing_file_is_not_adopted(self):
        result=install.preview(self.sha)
        self.assertEqual(result['status'],'PREVIEW');self.assertFalse(self.config.exists())
        target=self.units/'sniper-research.timer';target.write_bytes(b'foreign')
        with self.assertRaises(ValueError): install.preview(self.sha)
        self.assertEqual(target.read_bytes(),b'foreign')

    def test_source_tamper_blocks_installation(self):
        (self.release/'infra/beroun/sniper-research.service').write_bytes(b'changed')
        with self.assertRaises(ValueError): install.preview(self.sha)

    def test_real_cycle_and_reproduction_precede_timer_enable(self):
        commands=[]
        def run(argv):
            commands.append(argv)
            if argv[:2]==['systemctl','start']:
                self.state.mkdir(exist_ok=True)
                path=self.state/('result-'+datetime.now(timezone.utc).date().isoformat()+'.json')
                if not path.exists():path.write_text(json.dumps({'status':'BASELINE_SCREEN_COMPLETE','live_eligible':False}))
            if argv[0]=='runuser':return json.dumps({'status':'REPRODUCED'})
            return ''
        with patch('os.geteuid',return_value=0),patch.object(install,'run',side_effect=run):
            result=install.install(self.sha)
        self.assertTrue(result['repeat_result_unchanged'])
        replay=next(i for i,x in enumerate(commands) if x[0]=='runuser')
        enable=next(i for i,x in enumerate(commands) if x[:2]==['systemctl','enable'])
        self.assertLess(replay,enable)
        self.assertEqual((self.units/'sniper-research.service').stat().st_mode&0o777,0o644)

    def test_failed_cycle_keeps_evidence_and_disables_only_new_timer(self):
        def run(argv):
            if argv[:2]==['systemctl','start']:raise RuntimeError('fixture failure')
            return ''
        class Result: returncode=0
        with patch('os.geteuid',return_value=0),patch.object(install,'run',side_effect=run),patch.object(install.subprocess,'run',return_value=Result()) as stop:
            with self.assertRaises(RuntimeError): install.install(self.sha)
        self.assertEqual(stop.call_args.args[0],['systemctl','disable','--now','sniper-research.timer'])
        result=json.loads((self.base/'operations'/('research-install-'+self.sha)/'result.json').read_text())
        self.assertEqual(result['status'],'FAILED_TIMER_NOT_QUALIFIED')
        self.assertTrue((self.units/'sniper-research.service').exists())


if __name__=='__main__':unittest.main()
