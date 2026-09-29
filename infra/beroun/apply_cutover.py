#!/usr/bin/env python3
"""Apply one explicitly approved source-only cutover; preserve rollback evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from plan_cutover import UNITS, render

BASE=Path('/opt/sniper')
SYSTEMD=Path('/etc/systemd/system')
TIMER='beroun-paper-t15.timer'

def run(*args,check=True):
    return subprocess.run(list(args),check=check,text=True,capture_output=True,timeout=60)

def digest(data):
    return hashlib.sha256(data).hexdigest()

def no_links(path):
    for part in (path,*path.parents):
        if part.is_symlink():raise ValueError('Symlink in managed write path')

def atomic(path,data,mode=0o644):
    no_links(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.sniper-')
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.chmod(tmp,mode)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def validate_plan(plan,source):
    if plan['schema_version']!=1 or plan['canonical_repository']!='VaclavSercl/sniper':
        raise ValueError('Wrong plan identity')
    if plan['enable_funded_trading'] is not False:raise ValueError('Trading activation forbidden')
    if set(e['unit'] for e in plan['units'])!=set(UNITS) or len(plan['units'])!=len(UNITS):
        raise ValueError('Unit scope changed')
    if digest((source/'platform/SOURCE_MANIFEST.json').read_bytes())!=plan['source_manifest_sha256']:
        raise ValueError('Source manifest changed')
    for entry in plan['units']:
        path=Path(entry['fragment'])
        if path!=SYSTEMD/entry['unit']:raise ValueError('Unexpected unit location')
        no_links(path)
        content=path.read_bytes()
        if digest(content)!=entry['fragment_sha256'] or render(content.decode())!=entry['dropin']:
            raise ValueError('Unit changed since preview: '+entry['unit'])
        overrides=run('systemctl','show',entry['unit'],'-p','DropInPaths','--value').stdout.strip()
        if overrides:raise ValueError('Unreviewed existing override: '+entry['unit'])
        no_links(SYSTEMD/(entry['unit']+'.d')/'90-sniper-source.conf')

def switch(target):
    temporary=BASE/'current.pending'
    if temporary.exists() or temporary.is_symlink():raise ValueError('Pending link exists')
    temporary.symlink_to(target)
    os.replace(temporary,BASE/'current')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True,type=Path)
    parser.add_argument('--plan',required=True,type=Path)
    parser.add_argument('--commit',required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    if not args.apply:parser.error('Explicit --apply required; preview uses plan_cutover.py')
    if os.geteuid()!=0:raise ValueError('Approved privileged operation required')
    if not re.fullmatch('[0-9a-f]{40,64}',args.commit):raise ValueError('Invalid commit')
    source=args.source.absolute()
    no_links(source)
    plan=json.loads(args.plan.read_text())
    release=json.loads((source/'RELEASE.json').read_text())
    if release['commit']!=args.commit:raise ValueError('Release commit mismatch')
    for name,expected in release['files'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError('Unsafe release-relative path')
        if release['modes'].get(name) not in (0o644,0o755):
            raise ValueError('Unsupported release permission')
        path=source/name
        no_links(path)
        if not path.resolve().is_relative_to(source) or digest(path.read_bytes())!=expected:
            raise ValueError('Release content mismatch')
    validate_plan(plan,source)
    no_links(BASE)
    target=BASE/'releases'/args.commit
    if target.exists():raise ValueError('Release already exists; reconcile prior operation')
    current=BASE/'current'
    previous=str(current.readlink()) if current.is_symlink() else None
    if current.exists() and not current.is_symlink():raise ValueError('Unmanaged current path')
    if previous and not Path(previous).is_relative_to(BASE/'releases'):
        raise ValueError('Unmanaged previous release')
    active=[e['unit'] for e in plan['units']
            if run('systemctl','is-active',e['unit'],check=False).stdout.strip()=='active']
    record={'commit':args.commit,'previous':previous,'active_services':active,
            'timer_enabled':run('systemctl','is-enabled',TIMER,check=False).stdout.strip(),
            'timer_active':run('systemctl','is-active',TIMER,check=False).stdout.strip(),
            'status':'PREPARED','dropins':{}}
    record_path=BASE/'operations'/(args.commit+'.json')
    if record_path.exists():raise ValueError('Operation exists; reconcile before retry')
    def persist():
        atomic(record_path,(json.dumps(record,indent=2)+'\n').encode(),0o600)
    persist()
    # Only validated files enter the root-owned release; runtime state is excluded.
    target.mkdir(parents=True,mode=0o755)
    for name in release['files']:
        atomic(target/name,(source/name).read_bytes(),release['modes'][name])
    atomic(target/'RELEASE.json',(source/'RELEASE.json').read_bytes())
    written=[]
    linked=False
    try:
        run('systemctl','disable','--now',TIMER)
        switch(target);linked=True
        for entry in plan['units']:
            if not entry['dropin']:continue
            path=SYSTEMD/(entry['unit']+'.d')/'90-sniper-source.conf'
            data=entry['dropin'].encode()
            atomic(path,data);written.append((path,digest(data)))
            record['dropins'][str(path)]=digest(data);persist()
        run('systemctl','daemon-reload')
        for unit in active:
            if unit!='beroun-paper-t15.service':run('systemctl','restart',unit)
        for unit in active:
            if unit!='beroun-paper-t15.service':
                run('systemctl','is-active',unit)
        for entry in plan['units']:
            if entry['dropin']:
                actual=run('systemctl','show',entry['unit'],'-p','ExecStart','--value').stdout
                if '/opt/sniper/current/' not in actual:
                    raise ValueError('Effective source path not switched: '+entry['unit'])
        record['status']='DEPLOYED_SOURCE_ONLY_T15_PAUSED';persist()
    except BaseException:
        record['status']='ROLLBACK_STARTED';persist()
        for path,expected in reversed(written):
            if not path.is_symlink() and digest(path.read_bytes())==expected:path.unlink()
            else:raise RuntimeError('Changed drop-in; automatic rollback refused')
        if linked:
            if previous:switch(Path(previous))
            elif current.is_symlink() and current.readlink()==target:current.unlink()
            else:raise RuntimeError('Changed current link; automatic rollback refused')
        run('systemctl','daemon-reload')
        for unit in active:run('systemctl','restart',unit)
        if record['timer_enabled']=='enabled':run('systemctl','enable',TIMER)
        if record['timer_active']=='active':run('systemctl','start',TIMER)
        record['status']='ROLLED_BACK';persist()
        raise
    print(json.dumps(record,indent=2))

if __name__=='__main__':main()
