#!/usr/bin/env python3
"""Install only absent reviewed data units; preserve all failed capture evidence."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from install_observation import checked, write_new, digest

BASE=Path('/opt/sniper')
UNITS=Path('/etc/systemd/system')
STATE=Path('/var/lib/sniper/hyperliquid-data')


def run(argv,timeout=960):
    result=subprocess.run(argv,capture_output=True,text=True,timeout=timeout)
    if result.returncode:raise RuntimeError('Data installation command failed: '+argv[0])
    return result.stdout


def preview(sha):
    if not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}',sha):raise ValueError('Invalid release')
    release=checked(BASE/'releases'/sha)
    if not (BASE/'current').is_symlink() or (BASE/'current').resolve()!=release:
        raise ValueError('Expected release must be active')
    metadata=json.loads(checked(release/'RELEASE.json').read_bytes())
    if metadata['commit']!=sha:raise ValueError('Release mismatch')
    files={}
    for name in ('sniper-hyperliquid-data.service','sniper-hyperliquid-data.timer'):
        source=checked(release/'infra/beroun'/name);target=checked(UNITS/name)
        sha256=digest(source.read_bytes())
        if metadata['files'][source.relative_to(release).as_posix()]!=sha256:raise ValueError('Source drift')
        if target.exists():raise ValueError('Existing unit requires reconciliation')
        files[str(target)]={'source':str(source),'sha256':sha256}
    if checked(STATE).exists():raise ValueError('Existing archive must be preserved/reconciled')
    checked(BASE/'operations');checked(STATE.parent)
    return {'status':'PREVIEW','release':sha,'files':files,'state':str(STATE),
        'user':'beroun','network_scope':'public info endpoint only','live_activated':False}


def validate_first(data):
    if data['status']!='OBSERVED' or data['qualified'] is not False or data['latest_run']['status']!='COLLECTED':
        raise ValueError('Actual initial capture incomplete')
    if len(data['series'])!=12 or len(data['funding'])!=4:
        raise ValueError('Actual product/interval coverage missing')
    markets={'perpetual:'+c for c in ('BTC','ETH','SOL','HYPE')} | {'spot:UBTC/USDC','spot:HYPE/USDC'}
    if {(s['market'],s['interval']) for s in data['series']} != {(m,i) for m in markets for i in ('1m','1h')}:
        raise ValueError('Duplicate or incorrect data identities')
    if {s['market'] for s in data['funding']} != {'perpetual:'+c for c in ('BTC','ETH','SOL','HYPE')}:
        raise ValueError('Duplicate or incorrect funding identities')
    if any(s['rows']<=0 for s in data['series']) or any(s['events']<=0 for s in data['funding']):
        raise ValueError('Empty initial source')
    return data['latest_run']['run_id']


def install(sha):
    if os.geteuid()!=0:raise ValueError('Approved root installation required')
    import fcntl
    with checked(BASE/'operations/hyperliquid-data-install.lock').open('a') as lock:
        os.chmod(lock.name,0o600);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        observation=preview(sha);operation=checked(BASE/'operations'/('hyperliquid-data-install-'+sha))
        operation.mkdir(mode=0o700);write_new(operation/'intent.json',json.dumps(observation,indent=2).encode())
        if preview(sha)!=observation:raise ValueError('Installation target changed')
        created=[]
        try:
            for target,item in observation['files'].items():
                raw=Path(item['source']).read_bytes()
                if digest(raw)!=item['sha256']:raise ValueError('Source changed')
                write_new(Path(target),raw,0o644);created.append(target)
            run(['systemctl','daemon-reload'],30)
            run(['systemctl','start','sniper-hyperliquid-data.service'])
            script=BASE/'releases'/sha/'platform/research/hyperliquid_history.py'
            command=['runuser','-u','beroun','--','python3','-B',str(script)]
            data=json.loads(run(command+['status','--state-dir',str(STATE)],30))
            run_id=validate_first(data)
            replay=json.loads(run(command+['reproduce','--state-dir',str(STATE),'--run-id',run_id],60))
            if replay['status']!='REPRODUCED' or replay['qualified'] is not False:raise ValueError('Reproduction failed')
            run(['systemctl','start','sniper-observation.service'],60)
            run(['systemctl','start','beroun-strategy-registry-sync.service'],60)
            run(['systemctl','enable','--now','sniper-hyperliquid-data.timer'],30)
            run(['systemctl','is-active','sniper-hyperliquid-data.timer'],30)
            outcome={'status':'INSTALLED_CAPTURE_REPRODUCED_TIMER_ACTIVE','release':sha,'created':created,
                'reproduction':replay,'series':data['series'],'funding':data['funding'],
                'historical_qualification':False,'live_activated':False}
        except BaseException as exc:
            p=subprocess.run(['systemctl','disable','--now','sniper-hyperliquid-data.timer'],capture_output=True,text=True,timeout=30)
            outcome={'status':'FAILED_PRESERVED_FOR_RECONCILIATION','error_type':type(exc).__name__,
                'created':created,'timer_disable_exit':p.returncode,'archive_preserved':True}
            write_new(operation/'result.json',json.dumps(outcome,indent=2).encode());raise
        write_new(operation/'result.json',json.dumps(outcome,indent=2).encode());return outcome


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--release',required=True)
    ap.add_argument('--apply',action='store_true');args=ap.parse_args()
    print(json.dumps((install if args.apply else preview)(args.release),indent=2))


if __name__=='__main__':main()
