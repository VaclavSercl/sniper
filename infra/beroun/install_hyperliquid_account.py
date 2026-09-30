#!/usr/bin/env python3
"""Install absent read-only units and whitelisted public account identity.

Never copies a signing key, invokes a provider or changes exchange state.
Failed installation preserves all evidence and requires reconciliation.
"""
import argparse
import grp
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from install_observation import checked,write_new,digest
from install_hyperliquid_data import run

BASE=Path('/opt/sniper')
UNITS=Path('/etc/systemd/system')
CONFIG=Path('/etc/sniper/hyperliquid-account.json')
STATE=Path('/var/lib/sniper/hyperliquid-account')
OPERATIONS=Path('/home/wwwenda/sniper-operations')


def compatible_directory(uid,gid,mode,worker_group):
    # Existing root-owned 0755 configuration directories are safe to preserve.
    # The new file itself is 0640 root:beroun. Never chmod a shared directory.
    return uid==0 and not mode&0o022 and bool(mode&0o001 or gid==worker_group and mode&0o010)


def config_from_evidence(source,expected):
    source=checked(source)
    if not source.is_relative_to(OPERATIONS) or source.name!='account-observation.json':
        raise ValueError('Unapproved identity evidence source')
    companion=checked(source.parent/'account-role.json')
    if source.stat().st_size>4*1024*1024 or companion.stat().st_size>65536:
        raise ValueError('Oversized identity evidence')
    raw=source.read_bytes();role_raw=companion.read_bytes()
    sha=hashlib.sha256(raw+b'\x00'+role_raw).hexdigest()
    if sha!=expected:raise ValueError('Identity evidence changed')
    data=json.loads(raw);role=json.loads(role_raw)
    if (data['status']!='ACTUAL_ACCOUNT_OBSERVED_READ_ONLY' or data['live_enabled'] is not False or
            role['role']['role']!='agent' or role['signer'].lower()!=data['signer'].lower() or
            role['role']['data']['user'].lower()!=data['account'].lower() or
            data['account_role']['role'] not in ('user','subAccount')):
        raise ValueError('Unverified delegated account')
    config={'schema':1,'account':data['account'],'signer':data['signer'],
        'account_role':data['account_role']['role'],'evidence':sha}
    for key in ('account','signer'):
        if not re.fullmatch('0x[0-9a-fA-F]{40}',config[key]) or int(config[key][2:],16)==0:
            raise ValueError('Invalid public identity')
    if config['account'].lower()==config['signer'].lower():raise ValueError('Agent is not the actual account')
    return config


def preview(sha,source,evidence_hash):
    if not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}',sha):raise ValueError('Invalid release')
    release=checked(BASE/'releases'/sha)
    if not (BASE/'current').is_symlink() or (BASE/'current').resolve()!=release:
        raise ValueError('Expected release must be active')
    metadata=json.loads(checked(release/'RELEASE.json').read_bytes())
    if metadata['commit']!=sha:raise ValueError('Release mismatch')
    files={}
    for name in ('sniper-hyperliquid-account.service','sniper-hyperliquid-account.timer'):
        original=checked(release/'infra/beroun'/name);target=checked(UNITS/name)
        sha256=digest(original.read_bytes())
        if metadata['files'][original.relative_to(release).as_posix()]!=sha256:raise ValueError('Source drift')
        if target.exists():raise ValueError('Existing account unit requires reconciliation')
        files[str(target)]={'source':str(original),'sha256':sha256}
    if checked(CONFIG).exists() or checked(STATE).exists():raise ValueError('Existing account state must be preserved')
    group=grp.getgrnam('beroun').gr_gid
    parent=checked(CONFIG.parent)
    if parent.exists() and not compatible_directory(parent.stat().st_uid,parent.stat().st_gid,parent.stat().st_mode,group):
        raise ValueError('Existing config directory requires reconciliation')
    config=config_from_evidence(source,evidence_hash)
    return {'status':'PREVIEW','release':sha,'files':files,'state':str(STATE),'config':str(CONFIG),
        'identity_evidence':evidence_hash,'account_masked':config['account'][:8]+'...'+config['account'][-4:],
        'user':'beroun','network_scope':'read-only account info','live_activated':False}


def install(sha,source,evidence_hash):
    if os.geteuid()!=0:raise ValueError('Approved root installation required')
    import fcntl
    with checked(BASE/'operations/hyperliquid-account-install.lock').open('a') as lock:
        os.chmod(lock.name,0o600);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        observation=preview(sha,source,evidence_hash)
        operation=checked(BASE/'operations'/('hyperliquid-account-install-'+sha))
        operation.mkdir(mode=0o700);write_new(operation/'intent.json',json.dumps(observation,indent=2).encode())
        if preview(sha,source,evidence_hash)!=observation:raise ValueError('Installation target changed')
        config=config_from_evidence(source,evidence_hash);created=[]
        try:
            group=grp.getgrnam('beroun').gr_gid
            if not CONFIG.parent.exists():
                CONFIG.parent.mkdir(mode=0o750);os.chmod(CONFIG.parent,0o750);os.chown(CONFIG.parent,0,group)
            write_new(CONFIG,json.dumps(config,sort_keys=True).encode(),0o640);os.chown(CONFIG,0,group)
            created.append(str(CONFIG))
            for target,item in observation['files'].items():
                raw=Path(item['source']).read_bytes()
                if digest(raw)!=item['sha256']:raise ValueError('Source changed')
                write_new(Path(target),raw,0o644);created.append(target)
            run(['systemctl','daemon-reload'],30)
            run(['systemctl','start','sniper-hyperliquid-account.service'],200)
            script=BASE/'releases'/sha/'platform/research/hyperliquid_account.py'
            data=json.loads(run(['runuser','-u','beroun','--','python3','-B',str(script),'status'],30))
            if (data['status']!='ACCOUNT_OBSERVED_READ_ONLY' or data['live_enabled'] is not False or
                    data['association_revalidated'] is not True or data['account_masked']!=observation['account_masked']):
                raise ValueError('Actual account observation did not verify')
            run(['systemctl','start','sniper-observation.service'],60)
            run(['systemctl','start','beroun-strategy-registry-sync.service'],60)
            run(['systemctl','enable','--now','sniper-hyperliquid-account.timer'],30)
            run(['systemctl','is-active','sniper-hyperliquid-account.timer'],30)
            result={'status':'INSTALLED_ACCOUNT_OBSERVED_TIMER_ACTIVE','release':sha,'created':created,
                'account_masked':data['account_masked'],'observed_ms':data['observed_ms'],
                'signing_material_copied':False,'live_activated':False}
        except BaseException as exc:
            p=subprocess.run(['systemctl','disable','--now','sniper-hyperliquid-account.timer'],capture_output=True,text=True,timeout=30)
            result={'status':'FAILED_PRESERVED_FOR_RECONCILIATION','created':created,
                'error_type':type(exc).__name__,'timer_disable_exit':p.returncode,'live_activated':False}
            write_new(operation/'result.json',json.dumps(result,indent=2).encode());raise
        write_new(operation/'result.json',json.dumps(result,indent=2).encode());return result


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--release',required=True)
    ap.add_argument('--identity-source',type=Path,required=True);ap.add_argument('--source-sha256',required=True)
    ap.add_argument('--apply',action='store_true');args=ap.parse_args()
    print(json.dumps((install if args.apply else preview)(args.release,args.identity_source,args.source_sha256),indent=2))


if __name__=='__main__':main()
