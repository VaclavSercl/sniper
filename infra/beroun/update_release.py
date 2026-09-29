#!/usr/bin/env python3
"""Explicit source-only update after an approved, hash-bound deployment preview.

Does not install research schedules, alter databases, delete old releases or
activate trading. Existing timers pick up reviewed Python changes on next run.
Long-running unchanged gateway/risk services are not restarted unnecessarily.
"""
import argparse
import json
from pathlib import Path
import os
import re
from apply_cutover import atomic, digest, no_links, switch, BASE, run

def validate(source, metadata):
    if not re.fullmatch('[0-9a-f]{40,64}',metadata['commit']):
        raise ValueError('Invalid release identity')
    if not metadata['files'] or set(metadata['modes'])!=set(metadata['files']):
        raise ValueError('Incomplete release metadata')
    for name, expected in metadata['files'].items():
        relative=Path(name)
        if relative.is_absolute() or '..' in relative.parts or '\\' in name:
            raise ValueError('Unsafe release path')
        if relative.parts[0] not in ('platform','architect','infra','tools'):
            raise ValueError('Unexpected release scope')
        path=source/relative
        no_links(path)
        if metadata['modes'][name] not in (0o644,0o755) or digest(path.read_bytes())!=expected:
            raise ValueError('Release differs from verified candidate')

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source',type=Path,required=True)
    ap.add_argument('--expected-current',required=True)
    ap.add_argument('--manifest-sha256',required=True)
    ap.add_argument('--apply',action='store_true')
    args=ap.parse_args()
    source=args.source.absolute();no_links(source)
    raw=(source/'RELEASE.json').read_bytes()
    if digest(raw)!=args.manifest_sha256:raise ValueError('Unapproved release manifest')
    metadata=json.loads(raw);validate(source,metadata)
    target=BASE/'releases'/metadata['commit']
    previous=BASE/'releases'/args.expected_current
    if not re.fullmatch('[0-9a-f]{40,64}',args.expected_current):raise ValueError('Invalid baseline')
    no_links(BASE);no_links(target)
    current=BASE/'current'
    if not current.is_symlink() or current.readlink()!=previous:
        raise ValueError('Deployed source changed since preview')
    if target.exists():raise ValueError('Release exists; reconcile operation')
    record_path=BASE/'operations'/(metadata['commit']+'-update.json')
    no_links(record_path)
    if record_path.exists():raise ValueError('Operation already recorded')
    record={'status':'PREVIEW','previous':str(previous),'target':str(target),
            'manifest_sha256':args.manifest_sha256,'trading_activation':False}
    if not args.apply:
        print(json.dumps(record,indent=2));return
    if os.geteuid()!=0:raise ValueError('Approved privileged action required')
    # Single cooperative update lock; no action on contention or stale unknown lock.
    lock=BASE/'operations'/'source-update.lock';no_links(lock)
    fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    os.close(fd)
    linked=False
    def persist():atomic(record_path,(json.dumps(record,indent=2)+'\n').encode(),0o600)
    try:
        record['status']='STARTED';persist()
        if current.readlink()!=previous:raise ValueError('Concurrent source update')
        target.mkdir(mode=0o755)
        for name in metadata['files']:
            content=(source/name).read_bytes()
            if digest(content)!=metadata['files'][name]:raise ValueError('Source mutated')
            atomic(target/name,content,metadata['modes'][name])
        atomic(target/'RELEASE.json',raw)
        # A root-owned immutable copy must be complete before link replacement.
        validate(target,metadata)
        if current.readlink()!=previous:raise ValueError('Concurrent source update')
        switch(target);linked=True
        for unit in ('beroun-gateway.service','beroun-ingest.service','beroun-risk_kernel.service','beroun-watchdog.service'):
            run('systemctl','is-active',unit)
        record['status']='DEPLOYED_SOURCE_ONLY';persist()
    except BaseException:
        record['status']='FAILED'
        if linked and current.is_symlink() and current.readlink()==target:
            switch(previous);record['status']='ROLLED_BACK'
        persist();raise
    finally:
        lock.unlink()
    print(json.dumps(record,indent=2))

if __name__=='__main__':main()
