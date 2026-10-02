#!/usr/bin/env python3
"""Absent-target-only public research installation; never enable financial trading."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time

from install_observation import checked, write_new, digest
from install_hyperliquid_data import run

BASE=Path('/opt/sniper')
UNIT=Path('/etc/systemd/system/sniper-forward-research.service')
NAMES=('sniper-forward-research.service','sniper-forward-screen.service','sniper-forward-screen.timer')
STATE=Path('/var/lib/sniper/forward-research-v1')


def wait_for_capture(command):
    """Initial MISSING is readiness, not an installation or research pass."""
    deadline=time.monotonic()+20
    while True:
        remaining=deadline-time.monotonic()
        if remaining<=0: break
        p=subprocess.run(command,capture_output=True,text=True,timeout=min(30,remaining))
        if p.returncode not in (0,2): raise ValueError('Collector status command failed')
        observed=json.loads(p.stdout)
        if not isinstance(observed,dict): raise ValueError('Malformed collector report')
        if observed=={'status':'MISSING','qualified':False}:
            if p.returncode!=2: raise ValueError('Missing collector reported success')
        else:
            if observed.get('status') not in ('STALE_OR_FAILED','OBSERVED'):
                raise ValueError('Unexpected collector status')
            if p.returncode!=(0 if observed['status']=='OBSERVED' else 2) or 'last_health' not in observed:
                raise ValueError('Inconsistent collector status report')
            counts=observed.get('counts'); heartbeat=observed.get('last_health')
            if not isinstance(counts,dict) or type(counts.get('books')) is not int or counts['books']<0:
                raise ValueError('Malformed collector counts')
            if heartbeat is not None:
                if (not isinstance(heartbeat,dict) or heartbeat.get('status')!='PASS' or
                    type(heartbeat.get('time_ms')) is not int or
                    not 0<=int(time.time()*1000)-heartbeat['time_ms']<=60000):
                    raise ValueError('Unhealthy collector heartbeat')
                if counts['books']>=2: return observed
        remaining=deadline-time.monotonic()
        if remaining>0: time.sleep(min(2,remaining))
    raise ValueError('Initial collector readiness deadline exhausted')


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


def status_report(sha,timeout=30):
    command=['runuser','-u','beroun','--','python3','-B',str(BASE/'releases'/sha/'platform/research/forward_worker.py'),'status']
    p=subprocess.run(command,capture_output=True,text=True,timeout=timeout)
    if p.returncode not in (0,2): raise ValueError('Preserved epoch cannot be validated')
    data=json.loads(p.stdout)
    if not isinstance(data,dict) or data.get('status') not in ('OBSERVED','STALE_OR_FAILED'):
        raise ValueError('Unknown preserved epoch report')
    return data


def recovery_preview(sha,previous_sha):
    """Only recover the proven incomplete initial capture, never adopt other work."""
    import pwd
    if os.geteuid()!=0: raise ValueError('Approved root recovery inspection required')
    if not all(re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}',s) for s in (sha,previous_sha)):
        raise ValueError('Invalid recovery release')
    release=checked(BASE/'releases'/sha)
    if not (BASE/'current').is_symlink() or (BASE/'current').resolve()!=release:
        raise ValueError('Verified recovery source must be active')
    manifest=json.loads(checked(release/'RELEASE.json').read_bytes())
    if manifest['commit']!=sha: raise ValueError('Recovery release identity differs')
    previous=checked(BASE/'operations'/('forward-research-install-'+previous_sha))
    intent_path=checked(previous/'intent.json'); result_path=checked(previous/'result.json')
    for path in (previous,intent_path,result_path):
        if path.stat().st_uid!=0 or path.stat().st_mode & 0o077:
            raise ValueError('Untrusted original installation record')
    intent=json.loads(intent_path.read_bytes()); result=json.loads(result_path.read_bytes())
    if (intent['release']!=previous_sha or intent['state']!=str(STATE) or intent['user']!='beroun' or
        intent['units']!=[str(UNIT.parent/n) for n in NAMES] or intent['live_activated'] is not False or
        intent['exchange_writes'] is not False or result['status']!='FAILED_PRESERVED_FOR_RECONCILIATION' or
        result['error_type']!='KeyError' or result['units_created']!=list(NAMES) or
        result['stop_exit']!=0 or result['screen_stop_exit']!=0 or result['state_preserved'] is not True):
        raise ValueError('Original failed installation ownership differs')
    expected_sources={*(('infra/beroun/'+n) for n in NAMES),
        *(('platform/research/'+n) for n in ('forward_worker.py','forward_pipeline.py','forward_reference.py','execution_evidence.py','strategy_recipes.py'))}
    if set(intent['sources'])!=expected_sources: raise ValueError('Original model scope differs')
    for name,fingerprint in intent['sources'].items():
        if digest(checked(release/name).read_bytes())!=fingerprint or manifest['files'][name]!=fingerprint:
            raise ValueError('Original public model changed')
    units={}
    for name in NAMES:
        path=checked(UNIT.parent/name)
        if checked(Path(str(path)+'.d')).exists(): raise ValueError('Unknown recovery unit overrides')
        properties=dict(row.split('=',1) for row in run(['systemctl','show',name,'-p','FragmentPath','-p','DropInPaths','-p','ActiveState','-p','UnitFileState'],30).splitlines() if '=' in row)
        if (properties['FragmentPath']!=str(path) or properties['DropInPaths'] or properties['ActiveState']!='inactive' or
            properties['UnitFileState']!=('static' if name==NAMES[1] else 'disabled')):
            raise ValueError('Unexpected loaded recovery unit')
        fingerprint=digest(path.read_bytes())
        if path.stat().st_uid!=0 or path.stat().st_mode & 0o777!=0o644 or fingerprint!=intent['sources']['infra/beroun/'+name]:
            raise ValueError('Existing recovery unit ownership or bytes differ')
        units[name]=fingerprint
    account=pwd.getpwnam('beroun'); checked(STATE)
    if STATE.stat().st_uid!=account.pw_uid or STATE.stat().st_mode & 0o777!=0o700:
        raise ValueError('Preserved epoch ownership differs')
    allowed={'capture.lock','forward.sqlite3','forward.sqlite3-wal','forward.sqlite3-shm'}
    for path in STATE.iterdir():
        checked(path)
        if (path.name not in allowed or not path.is_file() or path.stat().st_uid!=account.pw_uid or
            path.stat().st_mode & 0o777!=0o600): raise ValueError('Unknown preserved epoch artifact')
    observed=status_report(sha); counts=observed['counts']
    if any(counts[n]!=0 for n in ('families','candidates','screens','trials','paper','orders')) or counts['books']!=2:
        raise ValueError('Unexpected preserved epoch content')
    return {'status':'RECOVERY_PREVIEW','release':sha,'previous_release':previous_sha,'units':units,
        'state':str(STATE),'database_sha256':digest(checked(STATE/'forward.sqlite3').read_bytes()),
        'model_sha256':observed['source_sha256'],'counts':counts,'state_preserved':True,
        'prior_intent_sha256':digest(intent_path.read_bytes()),'prior_result_sha256':digest(result_path.read_bytes()),
        'live_activated':False,'exchange_writes':False}


def recover_install(sha,previous_sha):
    if os.geteuid()!=0: raise ValueError('Owner-authorized root recovery required')
    import fcntl
    lock_path=checked(BASE/'operations/forward-research-install.lock')
    if lock_path.stat().st_uid!=0 or lock_path.stat().st_mode & 0o777!=0o600:
        raise ValueError('Untrusted recovery lock')
    with lock_path.open('r+') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        data=recovery_preview(sha,previous_sha)
        operation=checked(BASE/'operations'/('forward-research-recovery-'+sha))
        operation.mkdir(mode=0o700); write_new(operation/'intent.json',json.dumps(data,indent=2).encode())
        if recovery_preview(sha,previous_sha)!=data: raise ValueError('Recovery target changed')
        try:
            started=int(time.time()*1000); deadline=time.monotonic()+20
            run(['systemctl','start',NAMES[0]],30)
            fresh=None
            while time.monotonic()<deadline:
                current=status_report(sha,min(30,deadline-time.monotonic()))
                heartbeat=current['last_health']
                if heartbeat and heartbeat['status']!='PASS': raise ValueError('Fresh capture failed')
                if (heartbeat and started<=heartbeat['time_ms']<=int(time.time()*1000) and
                    current['counts']['books']>data['counts']['books']):
                    fresh=current; break
                time.sleep(min(1,max(0,deadline-time.monotonic())))
            if not fresh: raise ValueError('Preserved collector did not produce fresh books')
            run(['systemctl','start',NAMES[1]],300)
            observed=status_report(sha)
            if (observed['status']!='OBSERVED' or observed['counts']['families']!=2 or observed['counts']['candidates']!=12 or
                observed['counts']['screens']!=12 or observed['counts']['paper']!=0 or observed['counts']['orders']!=0 or
                observed['live_eligible']!=0 or observed['source_sha256']!=data['model_sha256']):
                raise ValueError('Recovered research counts or identity differ')
            run(['systemctl','enable',NAMES[0],NAMES[2]],30); run(['systemctl','start',NAMES[2]],30)
            run(['systemctl','is-active',NAMES[0],NAMES[2]],30)
            result={'status':'RECOVERED_PUBLIC_CAPTURE_GENERATION_SCREENS_ACTIVE','release':sha,
                'observed':observed,'prior_operation_preserved':True,'state_preserved':True,'live_activated':False}
        except BaseException as exc:
            stop=subprocess.run(['systemctl','disable','--now',NAMES[0],NAMES[2]],capture_output=True,text=True,timeout=30)
            screen=subprocess.run(['systemctl','stop',NAMES[1]],capture_output=True,text=True,timeout=30)
            result={'status':'FAILED_RECOVERY_PRESERVED_FOR_RECONCILIATION','error_type':type(exc).__name__,
                'stop_exit':stop.returncode,'screen_stop_exit':screen.returncode,'state_preserved':True,'live_activated':False}
        write_new(operation/'result.json',json.dumps(result,indent=2).encode()); return result


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
            observed=wait_for_capture(command)
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
    ap.add_argument('--apply',action='store_true'); ap.add_argument('--recover-install-from')
    args=ap.parse_args()
    if args.recover_install_from:
        result=(recover_install if args.apply else recovery_preview)(args.release,args.recover_install_from)
    else: result=(install if args.apply else preview)(args.release)
    print(json.dumps(result,indent=2))
    if result['status'].startswith('FAILED_'): raise SystemExit(2)


if __name__=='__main__': main()
