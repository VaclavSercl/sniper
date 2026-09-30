#!/usr/bin/env python3
"""Guarded installation of absent bounded offline research schedules."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from install_observation import checked,write_new,digest
from install_hyperliquid_data import run

BASE=Path('/opt/sniper')
UNITS=Path('/etc/systemd/system')
STATE=Path('/var/lib/sniper/hyperliquid-research')
NAMES=('sniper-research-cycle.service','sniper-research-cycle.timer',
    'sniper-research-compare.service','sniper-research-compare.timer')


def preview(sha):
    if not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}',sha):raise ValueError('Invalid release')
    release=checked(BASE/'releases'/sha)
    if not (BASE/'current').is_symlink() or (BASE/'current').resolve()!=release:raise ValueError('Expected release must be active')
    metadata=json.loads(checked(release/'RELEASE.json').read_bytes())
    if metadata['commit']!=sha:raise ValueError('Release mismatch')
    files={}
    for name in NAMES:
        original=checked(release/'infra/beroun'/name);target=checked(UNITS/name)
        sha256=digest(original.read_bytes())
        if metadata['files'][original.relative_to(release).as_posix()]!=sha256:raise ValueError('Source drift')
        if target.exists():raise ValueError('Existing research unit requires reconciliation')
        files[str(target)]={'source':str(original),'sha256':sha256}
    if checked(STATE).exists():raise ValueError('Existing frozen research state must be preserved')
    return {'status':'PREVIEW','release':sha,'files':files,'state':str(STATE),'user':'beroun',
        'daily_utc':'03:15','weekly_utc':'Sunday 03:45','new_economic_blueprints_per_utc_day':2,
        'maximum_primary_test_bundles_per_utc_day':12,'finite_blueprint_library':6,
        'paid_models':False,'live_activated':False}


def validate_first(data):
    if (data['status']!='RESEARCH_OBSERVED' or data['historical_qualified']!=0 or
            data['paper_qualified']!=0 or data['live_eligible']!=0 or data['interrupted']!=0 or
            data['blueprints_registered']!=2 or data['registered_market_variants']!=12 or
            data['completed_screens']+data['blocked_product_models']!=12):
        raise ValueError('First actual bounded research cycle incomplete')
    stages=data['stages']
    if len(stages)!=12 or len({s['variant'] for s in stages})!=12:
        raise ValueError('First cycle variants duplicated or missing')
    if any(s['paper_eligible'] is not False or s['live_eligible'] is not False for s in stages):
        raise ValueError('Exploratory screens cannot grant qualification')
    return [s['variant'] for s in stages if s['status'].startswith('SCREEN_') or s['status']=='EXPLORATORY_POSITIVE_UNQUALIFIED']


def first_cycle(sha):
    run(['systemctl','start','sniper-hyperliquid-account.service'],200)
    run(['systemctl','start','sniper-hyperliquid-data.service'],950)
    prefix=['runuser','-u','beroun','--','python3','-B']
    data_script=BASE/'releases'/sha/'platform/research/hyperliquid_history.py'
    source=json.loads(run(prefix+[str(data_script),'status'],60))
    if source['latest_run']['status']!='COLLECTED':raise ValueError('Actual repaired capture incomplete')
    replay=json.loads(run(prefix+[str(data_script),'reproduce','--run-id',source['latest_run']['run_id']],90))
    if replay['status']!='REPRODUCED' or replay['qualified'] is not False:raise ValueError('Actual source replay failed')
    run(['systemctl','start','sniper-research-cycle.service'],650)
    script=BASE/'releases'/sha/'platform/research/research_cycle.py'
    command=prefix+[str(script)]
    data=json.loads(run(command+['status'],90));variants=validate_first(data);replays=[]
    for variant in variants:
        reproduction=json.loads(run(command+['reproduce','--variant',variant],90))
        if reproduction['status']!='REPRODUCED' or reproduction['qualified'] is not False:raise ValueError('Screen reproduction failed')
        replays.append(reproduction)
    run(['systemctl','start','sniper-research-compare.service'],150)
    run(['systemctl','start','sniper-observation.service'],60)
    run(['systemctl','start','beroun-strategy-registry-sync.service'],60)
    for name in ('sniper-research-cycle.timer','sniper-research-compare.timer'):
        run(['systemctl','enable','--now',name],30);run(['systemctl','is-active',name],30)
    # OnBootSec can fire immediately on restart. Freeze/reproduce the first
    # epoch before allowing another capture to create a concurrent STARTED row.
    run(['systemctl','start','sniper-hyperliquid-data.timer'],30)
    run(['systemctl','is-active','sniper-hyperliquid-data.timer'],30)
    return {'status':'INSTALLED_RESEARCH_REPRODUCED_TIMERS_ACTIVE','release':sha,
        'actual_blueprints':data['blueprints_registered'],'actual_variants':data['registered_market_variants'],
        'completed_screens':data['completed_screens'],'blocked_product_models':data['blocked_product_models'],
        'source_reproduction':replay,'reproductions':replays,
        'historical_qualified':0,'paper_qualified':0,'live_eligible':0}


def pause_new_timers():
    outcomes={}
    for name in ('sniper-research-cycle.timer','sniper-research-compare.timer'):
        p=subprocess.run(['systemctl','disable','--now',name],capture_output=True,text=True,timeout=30)
        outcomes[name]=p.returncode
    return outcomes


def install(sha):
    if os.geteuid()!=0:raise ValueError('Approved root installation required')
    import fcntl
    with checked(BASE/'operations/research-cycle-install.lock').open('a') as lock:
        os.chmod(lock.name,0o600);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        observation=preview(sha);operation=checked(BASE/'operations'/('research-cycle-install-'+sha))
        operation.mkdir(mode=0o700);write_new(operation/'intent.json',json.dumps(observation,indent=2).encode())
        if preview(sha)!=observation:raise ValueError('Installation target changed')
        created=[]
        try:
            for target,item in observation['files'].items():
                raw=Path(item['source']).read_bytes()
                if digest(raw)!=item['sha256']:raise ValueError('Source changed')
                write_new(Path(target),raw,0o644);created.append(target)
            run(['systemctl','daemon-reload'],30)
            result=first_cycle(sha)|{'created':created}
        except BaseException as exc:
            result={'status':'FAILED_PRESERVED_FOR_RECONCILIATION','created':created,
                'error_type':type(exc).__name__,'timer_disable_exits':pause_new_timers(),'state_preserved':True}
            write_new(operation/'result.json',json.dumps(result,indent=2).encode());raise
        write_new(operation/'result.json',json.dumps(result,indent=2).encode());return result


def failure_contract(intent,result,actual_hashes,state_exists):
    expected={str(UNITS/name) for name in NAMES}
    if (intent['status']!='PREVIEW' or result['status']!='FAILED_PRESERVED_FOR_RECONCILIATION' or
            set(intent['files'])!=expected or set(result['created'])!=expected or
            len(result['created'])!=len(expected) or state_exists):
        raise ValueError('Failed installation ownership is incomplete or research state exists')
    if actual_hashes!={target:item['sha256'] for target,item in intent['files'].items()}:
        raise ValueError('Previously created units changed')


def owned_json(path):
    path=checked(path)
    if path.stat().st_uid!=0 or path.stat().st_mode&0o077 or path.stat().st_size>65536:
        raise ValueError('Untrusted operation evidence')
    return json.loads(path.read_bytes())


def next_repair(counts):
    if (any(type(value) is not int for value in counts) or
            sorted(counts)!=list(range(1,len(counts)+1)) or len(counts)>=3):
        raise ValueError('Repair budget exhausted or lineage ambiguous')
    return len(counts)+1


def recovery_preview(sha,failed):
    if any(not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}',value) for value in (sha,failed)):
        raise ValueError('Invalid recovery revision')
    release=checked(BASE/'releases'/sha)
    if not (BASE/'current').is_symlink() or (BASE/'current').resolve()!=release:
        raise ValueError('Verified recovery release must be active')
    metadata=json.loads(checked(release/'RELEASE.json').read_bytes())
    if metadata['commit']!=sha:raise ValueError('Recovery release mismatch')
    old=checked(BASE/'operations'/('research-cycle-install-'+failed))
    intent=owned_json(old/'intent.json');result=owned_json(old/'result.json')
    if intent['release']!=failed:raise ValueError('Failed installation revision mismatch')
    actual={}
    for name in NAMES:
        target=checked(UNITS/name);original=checked(release/'infra/beroun'/name)
        if target.stat().st_uid!=0 or target.stat().st_mode&0o777!=0o644:
            raise ValueError('Unit ownership or permissions changed')
        actual[str(target)]=digest(target.read_bytes())
        if digest(original.read_bytes())!=actual[str(target)] or metadata['files'][original.relative_to(release).as_posix()]!=actual[str(target)]:
            raise ValueError('Recovery source/unit differs')
        show=run(['systemctl','show',name,'-p','DropInPaths','--value'],30).strip()
        if show:raise ValueError('Unreviewed recovery unit overrides')
        if name.endswith('.timer'):
            state=run(['systemctl','show',name,'-p','ActiveState','--value'],30).strip()
            if state!='inactive':raise ValueError('Recovery timer is active')
    failure_contract(intent,result,actual,checked(STATE).exists())
    attempts=[]
    paths=sorted((BASE/'operations').glob('research-cycle-recovery-*'))
    if len(paths)>32:raise ValueError('Recovery history requires manual review')
    for path in paths:
        record=owned_json(path/'intent.json')
        if record['failed_install']!=str(old):continue
        prior=owned_json(path/'result.json')
        if prior['status']!='FAILED_PRESERVED_FOR_RECONCILIATION':raise ValueError('Prior recovery succeeded or is ambiguous')
        attempts.append(record['repair_count'])
    return {'status':'RECOVERY_PREVIEW','release':sha,'failed_install':str(old),'repair_count':next_repair(attempts),
        'unit_hashes':actual,'research_state_must_be_absent':True,'live_activated':False}


def recover(sha,failed,apply=False):
    if not apply:return recovery_preview(sha,failed)
    if os.geteuid()!=0:raise ValueError('Approved root recovery required')
    import fcntl
    with checked(BASE/'operations/research-cycle-install.lock').open('a') as lock:
        os.chmod(lock.name,0o600);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        observation=recovery_preview(sha,failed)
        operation=checked(BASE/'operations'/('research-cycle-recovery-'+sha))
        operation.mkdir(mode=0o700)
        # Reserve the cumulative repair before any writer/service is invoked.
        write_new(operation/'intent.json',json.dumps(observation,indent=2).encode())
        try:result=first_cycle(sha)|{'failed_install':observation['failed_install'],'repair_count':observation['repair_count']}
        except BaseException as exc:
            result={'status':'FAILED_PRESERVED_FOR_RECONCILIATION','error_type':type(exc).__name__,
                'timer_disable_exits':pause_new_timers(),'research_state_preserved':True,
                'failed_install':observation['failed_install'],'repair_count':observation['repair_count']}
            write_new(operation/'result.json',json.dumps(result,indent=2).encode());raise
        write_new(operation/'result.json',json.dumps(result,indent=2).encode());return result


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--release',required=True)
    ap.add_argument('--recover-install-from')
    ap.add_argument('--apply',action='store_true');args=ap.parse_args()
    result=recover(args.release,args.recover_install_from,args.apply) if args.recover_install_from else (install if args.apply else preview)(args.release)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
