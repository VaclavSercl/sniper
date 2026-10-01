#!/usr/bin/env python3
"""Absent-target-only public research installation; never enable financial trading."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

from install_observation import checked, write_new, digest
from install_hyperliquid_data import run

BASE=Path('/opt/sniper')
UNIT=Path('/etc/systemd/system/sniper-forward-research.service')
NAMES=('sniper-forward-research.service','sniper-forward-screen.service','sniper-forward-screen.timer')
STATE=Path('/var/lib/sniper/forward-research-v1')


def preview(sha):
    if not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}',sha): raise ValueError('Invalid release')
    release=checked(BASE/'releases'/sha)
    if not (BASE/'current').is_symlink() or (BASE/'current').resolve()!=release: raise ValueError('Verified source release must be active')
    manifest=json.loads(checked(release/'RELEASE.json').read_bytes())
    if manifest['commit']!=sha: raise ValueError('Release identity differs')
    sources={}
    for name in tuple('infra/beroun/'+name for name in NAMES)+(
                 'platform/research/forward_worker.py','platform/research/forward_pipeline.py',
                 'platform/research/forward_reference.py','platform/research/execution_evidence.py',
                 'platform/research/strategy_recipes.py'):
        source=checked(release/name); fingerprint=digest(source.read_bytes())
        if manifest['files'][name]!=fingerprint: raise ValueError('Reviewed source changed')
        sources[name]=fingerprint
    for name in NAMES:
        target=UNIT.parent/name
        if checked(target).exists() or checked(Path(str(target)+'.d')).exists(): raise ValueError('Existing unit/overrides must be preserved')
        if run(['systemctl','show',name,'-p','FragmentPath','--value'],30).strip(): raise ValueError('Another loaded unit must be preserved')
    if checked(STATE).exists(): raise ValueError('Existing forward epoch must be preserved')
    for root,name in (('/var/lib/sniper/hyperliquid-data','history.sqlite3'),('/var/lib/sniper/hyperliquid-research','cycle.sqlite3')):
        if not checked(Path(root)/name).is_file(): raise ValueError('Existing source archive required')
    return {'status':'PREVIEW','release':sha,'units':[str(UNIT.parent/name) for name in NAMES],'state':str(STATE),'sources':sources,
            'user':'beroun','poll_seconds':10,'mechanisms_per_day':2,'declared_mechanisms':336,
            'maximum_market_variants':1848,'maximum_test_bundles_per_day':12,
            'live_activated':False,'exchange_writes':False,'paid_provider_calls':False}


def install(sha):
    if os.geteuid()!=0: raise ValueError('Owner-authorized root installation required')
    import fcntl
    with checked(BASE/'operations/forward-research-install.lock').open('a') as stream:
        os.chmod(stream.name,0o600); fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        data=preview(sha); operation=checked(BASE/'operations'/('forward-research-install-'+sha))
        operation.mkdir(mode=0o700); write_new(operation/'intent.json',json.dumps(data,indent=2).encode())
        if preview(sha)!=data: raise ValueError('Installation target changed')
        created=[]
        try:
            for name in NAMES:
                source=BASE/'releases'/sha/'infra/beroun'/name
                write_new(UNIT.parent/name,source.read_bytes(),0o644); created.append(name)
            run(['systemctl','daemon-reload'],30)
            # Start without enabling persistence. Enable only after actual
            # metadata, two public books and twelve known-data screens are read.
            run(['systemctl','start',UNIT.name],30)
            command=['runuser','-u','beroun','--','python3','-B',str(BASE/'releases'/sha/'platform/research/forward_worker.py'),'status']
            observed=None
            import time
            for _ in range(10):
                p=subprocess.run(command,capture_output=True,text=True,timeout=30)
                if p.returncode in (0,2) and p.stdout:
                    observed=json.loads(p.stdout)
                    if observed['counts']['books']>=2 and observed['last_health'] and observed['last_health']['status']=='PASS': break
                time.sleep(2)
            if not observed or observed['counts']['books']<2: raise ValueError('Initial collector incomplete')
            run(['systemctl','start','sniper-forward-screen.service'],300)
            observed=json.loads(run(command,30))
            if (not observed or observed['status']!='OBSERVED' or observed['counts']['families']!=2 or
                observed['counts']['candidates']!=12 or observed['counts']['screens']!=12 or
                observed['counts']['books']<2 or observed['counts']['paper']!=0 or observed['live_eligible']!=0):
                raise ValueError('Actual initial public research cycle incomplete')
            run(['systemctl','enable',UNIT.name,'sniper-forward-screen.timer'],30)
            run(['systemctl','start','sniper-forward-screen.timer'],30)
            run(['systemctl','is-active',UNIT.name,'sniper-forward-screen.timer'],30)
            result={'status':'INSTALLED_PUBLIC_CAPTURE_GENERATION_SCREENS_ACTIVE','release':sha,
                    'observed':observed,'live_activated':False}
        except BaseException as exc:
            stop=subprocess.run(['systemctl','disable','--now',UNIT.name,'sniper-forward-screen.timer'],capture_output=True,text=True,timeout=30) if created else None
            screen_stop=subprocess.run(['systemctl','stop','sniper-forward-screen.service'],capture_output=True,text=True,timeout=30) if 'sniper-forward-screen.service' in created else None
            result={'status':'FAILED_PRESERVED_FOR_RECONCILIATION','error_type':type(exc).__name__,
                    'units_created':created,'stop_exit':stop.returncode if stop else None,
                    'screen_stop_exit':screen_stop.returncode if screen_stop else None,'state_preserved':True}
            write_new(operation/'result.json',json.dumps(result,indent=2).encode()); raise
        write_new(operation/'result.json',json.dumps(result,indent=2).encode()); return result


def main():
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('--release',required=True)
    ap.add_argument('--apply',action='store_true'); args=ap.parse_args()
    print(json.dumps((install if args.apply else preview)(args.release),indent=2))


if __name__=='__main__': main()
