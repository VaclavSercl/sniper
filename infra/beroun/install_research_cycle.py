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
            run(['systemctl','start','sniper-hyperliquid-account.service'],200)
            run(['systemctl','start','sniper-hyperliquid-data.service'],950)
            run(['systemctl','start','sniper-research-cycle.service'],650)
            script=BASE/'releases'/sha/'platform/research/research_cycle.py'
            command=['runuser','-u','beroun','--','python3','-B',str(script)]
            data=json.loads(run(command+['status'],90));variants=validate_first(data);replays=[]
            for variant in variants:
                replay=json.loads(run(command+['reproduce','--variant',variant],90))
                if replay['status']!='REPRODUCED' or replay['qualified'] is not False:raise ValueError('Screen reproduction failed')
                replays.append(replay)
            run(['systemctl','start','sniper-research-compare.service'],150)
            run(['systemctl','start','sniper-observation.service'],60)
            run(['systemctl','start','beroun-strategy-registry-sync.service'],60)
            for name in ('sniper-research-cycle.timer','sniper-research-compare.timer'):
                run(['systemctl','enable','--now',name],30);run(['systemctl','is-active',name],30)
            result={'status':'INSTALLED_RESEARCH_REPRODUCED_TIMERS_ACTIVE','release':sha,'created':created,
                'actual_blueprints':data['blueprints_registered'],'actual_variants':data['registered_market_variants'],
                'completed_screens':data['completed_screens'],'blocked_product_models':data['blocked_product_models'],
                'reproductions':replays,'historical_qualified':0,'paper_qualified':0,'live_eligible':0}
        except BaseException as exc:
            outcomes={}
            for name in ('sniper-research-cycle.timer','sniper-research-compare.timer'):
                p=subprocess.run(['systemctl','disable','--now',name],capture_output=True,text=True,timeout=30);outcomes[name]=p.returncode
            result={'status':'FAILED_PRESERVED_FOR_RECONCILIATION','created':created,
                'error_type':type(exc).__name__,'timer_disable_exits':outcomes,'state_preserved':True}
            write_new(operation/'result.json',json.dumps(result,indent=2).encode());raise
        write_new(operation/'result.json',json.dumps(result,indent=2).encode());return result


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--release',required=True)
    ap.add_argument('--apply',action='store_true');args=ap.parse_args()
    print(json.dumps((install if args.apply else preview)(args.release),indent=2))


if __name__=='__main__':main()
