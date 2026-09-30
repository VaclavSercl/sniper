#!/usr/bin/env python3
"""Install only the absent, reviewed Sniper research units and fixed policy.

Preview by default. Keep durable operation evidence and all failed-run data.
The existing updater handles source release verification separately.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

BASE = Path('/opt/sniper')
UNITS = Path('/etc/systemd/system')
CONFIG = Path('/etc/sniper')
STATE = Path('/var/lib/sniper/candle-research')


def checked(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Unexpected symlink: '+str(path))
    return path


def write_new(path, raw, mode):
    checked(path)
    with path.open('xb') as stream:
        os.chmod(path, mode); stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def run(argv):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=330)
    if result.returncode:
        raise RuntimeError('Command failed: '+argv[0]+' '+argv[1]+' exit='+str(result.returncode))
    return result.stdout


def preview(sha):
    if len(sha) not in (40,64) or any(c not in '0123456789abcdef' for c in sha):
        raise ValueError('Invalid release SHA')
    release = checked(BASE/'releases'/sha)
    current = BASE/'current'
    if not current.is_symlink() or current.resolve()!=release:
        raise ValueError('Expected source release is not active')
    metadata = json.loads(checked(release/'RELEASE.json').read_bytes())
    if metadata['commit'] != sha: raise ValueError('Release identity mismatch')
    files = {}
    for relative, target in (
        ('infra/beroun/sniper-research.service', UNITS/'sniper-research.service'),
        ('infra/beroun/sniper-research.timer', UNITS/'sniper-research.timer'),
        ('infra/beroun/candle-research-policy.json', CONFIG/'candle-research-policy.json')):
        raw = checked(release/relative).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=metadata['files'][relative]:
            raise ValueError('Source changed since release verification')
        checked(target)
        if target.exists(): raise ValueError('Existing target requires reconciliation: '+str(target))
        files[str(target)]={'source':str(release/relative),'sha256':hashlib.sha256(raw).hexdigest()}
    checked(STATE)
    if STATE.exists(): raise ValueError('Existing research state must be preserved/reconciled')
    checked(CONFIG); checked(UNITS); checked(BASE/'operations')
    return {'status':'PREVIEW','release':sha,'files':files,'prior_state':'ABSENT',
            'trade_activation':False,'service_user':'beroun'}


def install(sha):
    if os.geteuid()!=0: raise ValueError('Approved root installation required')
    observation = preview(sha)
    operation = checked(BASE/'operations'/('research-install-'+sha))
    operation.mkdir(mode=0o700)
    write_new(operation/'intent.json',json.dumps(observation,indent=2).encode(),0o600)
    # Operation creation is exclusive. Revalidate targets before any change.
    if preview(sha)!=observation: raise ValueError('Installation targets changed')
    created=[]
    try:
        CONFIG.mkdir(mode=0o755,exist_ok=True)
        for name, item in observation['files'].items():
            raw=Path(item['source']).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=item['sha256']: raise ValueError('Source changed')
            write_new(Path(name),raw,0o644); created.append(name)
        run(['systemctl','daemon-reload'])
        run(['systemctl','start','sniper-research.service'])
        # Starting a unit is not enough: validate the durable, independently
        # reproducible result before enabling periodic execution.
        day=datetime.now(timezone.utc).date().isoformat()
        result=json.loads(checked(STATE/('result-'+day+'.json')).read_bytes())
        if result['status']!='BASELINE_SCREEN_COMPLETE' or result['live_eligible'] is not False:
            raise ValueError('First real baseline cycle incomplete')
        script=BASE/'releases'/sha/'platform/research/candle_research.py'
        replay=json.loads(run(['runuser','-u','beroun','--','python3','-B',str(script),
            'reproduce','--state-dir',str(STATE),'--day',day]))
        if replay['status']!='REPRODUCED': raise ValueError('Actual result not reproduced')
        result_path=STATE/('result-'+day+'.json')
        before=result_path.read_bytes()
        run(['systemctl','start','sniper-research.service'])
        if result_path.read_bytes()!=before: raise ValueError('Repeated cycle changed evidence')
        run(['systemctl','enable','--now','sniper-research.timer'])
        run(['systemctl','is-active','sniper-research.timer'])
        outcome={'status':'INSTALLED_CYCLE_REPRODUCED_TIMER_ACTIVE','release':sha,
            'created':created,'result_day':day,'reproduction':replay,
            'repeat_result_unchanged':True,'live_activated':False}
    except BaseException as exc:
        stopped=subprocess.run(['systemctl','disable','--now','sniper-research.timer'],capture_output=True,text=True,timeout=30)
        outcome={'status':'FAILED_TIMER_NOT_QUALIFIED','error_type':type(exc).__name__,
            'created':created,'timer_disable_exit':stopped.returncode,'state_preserved':True}
        write_new(operation/'result.json',json.dumps(outcome,indent=2).encode(),0o600)
        raise
    write_new(operation/'result.json',json.dumps(outcome,indent=2).encode(),0o600)
    return outcome


def main():
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('--release',required=True)
    ap.add_argument('--apply',action='store_true');args=ap.parse_args()
    print(json.dumps(install(args.release) if args.apply else preview(args.release),indent=2))


if __name__=='__main__':main()
