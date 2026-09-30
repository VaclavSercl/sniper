#!/usr/bin/env python3
"""Consolidate the explicitly scoped Sniper workers under existing beroun UID.

Preview by default. Pin the preview digest before apply. Preserve old root-owned
units, exact private registry bytes, inactive T15, security guardian, SSH and all
home directories. Failed operations retain backups and stop affected schedules.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
from install_observation import checked, write_new, digest, run

BASE = Path('/opt/sniper')
UNITS = Path('/etc/systemd/system')
STATE = Path('/var/lib/sniper/research')
SERVICES = ('beroun-perfect-ingest', 'beroun-retention-manager',
    'beroun-strategy-registry-sync', 'beroun-rich-report', 'beroun-paper-t15')
OVERRIDE = '96-sniper-application-user.conf'


def encode(value): return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def entry(path):
    checked(path); info = path.stat()
    if not stat.S_ISREG(info.st_mode) and not stat.S_ISDIR(info.st_mode): raise ValueError('Unsupported managed type')
    if path.is_file() and info.st_size > 16*1024*1024: raise ValueError('Managed file oversized')
    return {'uid': info.st_uid, 'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode),
        'type': 'directory' if path.is_dir() else 'file',
        'sha256': digest(path.read_bytes()) if path.is_file() else None}


def snapshot():
    config = {}
    for name in SERVICES:
        path = UNITS/(name+'.service'); config[str(path)] = entry(path)
        directory = checked(UNITS/(name+'.service.d'))
        if not directory.is_dir(): raise ValueError('Expected existing scoped override directory')
        expected = {'90-sniper-source.conf'}
        if name == 'beroun-strategy-registry-sync': expected.add('95-sniper-observation.conf')
        if {p.name for p in directory.iterdir()} != expected: raise ValueError('Unexpected existing overrides')
        for path in sorted(directory.iterdir()): config[str(path)] = entry(path)
    if any(v['uid'] != 0 or v['type'] != 'file' for v in config.values()):
        raise ValueError('Configuration must remain root-owned')
    data = {'.': entry(STATE)}
    paths = sorted(STATE.rglob('*'))
    if len(paths) > 200: raise ValueError('Registry inventory exceeds scoped bound')
    for path in paths:
        checked(path)
        if not path.resolve().is_relative_to(STATE): raise ValueError('Registry escape')
        data[path.relative_to(STATE).as_posix()] = entry(path)
    owner = pwd.getpwnam('wwwenda').pw_uid
    if any(v['uid'] != owner for v in data.values()): raise ValueError('Unexpected registry owner')
    return {'configuration': config, 'registry': data}


def preview(sha):
    if not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}',sha): raise ValueError('Invalid release identity')
    release = checked(BASE/'releases'/sha)
    if not (BASE/'current').is_symlink() or (BASE/'current').resolve() != release:
        raise ValueError('Expected reviewed release must be active')
    metadata = json.loads(checked(release/'RELEASE.json').read_bytes())
    if metadata['commit'] != sha: raise ValueError('Release mismatch')
    source = {}
    for name in (OVERRIDE, '96-sniper-application-report.conf'):
        path = checked(release/'infra/beroun'/name); fingerprint = digest(path.read_bytes())
        if metadata['files'][path.relative_to(release).as_posix()] != fingerprint:
            raise ValueError('Source drift')
        source[name] = {'path': str(path), 'sha256': fingerprint}
    account = pwd.getpwnam('beroun')
    return {'status': 'PREVIEW', 'release': sha, 'source': source, 'before': snapshot(),
        'target_uid': account.pw_uid, 'target_gid': account.pw_gid, 'services': list(SERVICES),
        'preserved_identities': ['wwwenda administrative SSH/Hermes', 'beroun-kernel guardian',
            'postgres database', 'root privileged maintenance'], 'live_activated': False}


def active(unit):
    p = subprocess.run(['systemctl','is-active',unit],capture_output=True,text=True,timeout=10)
    state = p.stdout.strip()
    if state not in ('active','inactive','failed'): raise ValueError('Unit in transition requires retry')
    return state == 'active'


def install(sha, expected):
    if os.geteuid() != 0: raise ValueError('Approved root installation required')
    import fcntl
    with checked(BASE/'operations/application-identity.lock').open('a') as lock:
        os.chmod(lock.name,0o600);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        observation = preview(sha)
        if digest(encode(observation)) != expected: raise ValueError('Preview changed')
        operation = checked(BASE/'operations'/('application-identity-'+sha))
        operation.mkdir(mode=0o700)
        prior_timers = {name+'.timer': active(name+'.timer') for name in SERVICES}
        write_new(operation/'intent.json', encode({'preview': observation, 'timers': prior_timers}))
        created = []
        try:
            for timer in prior_timers: run(['systemctl','stop',timer])
            for name in SERVICES: run(['systemctl','stop',name+'.service'])
            if snapshot() != observation['before']: raise ValueError('Managed state changed after writers stopped')
            backup = operation/'recovery';backup.mkdir(mode=0o700)
            for name, item in observation['before']['registry'].items():
                if item['type'] == 'file':
                    write_new(backup/digest(name.encode()), checked(STATE/name).read_bytes())
            for name, item in observation['before']['configuration'].items():
                write_new(backup/digest(name.encode()), checked(Path(name)).read_bytes())
            for name in SERVICES:
                source_name = '96-sniper-application-report.conf' if name == 'beroun-rich-report' else OVERRIDE
                source = observation['source'][source_name]; raw = Path(source['path']).read_bytes()
                if digest(raw) != source['sha256']: raise ValueError('Override source changed')
                target = checked(UNITS/(name+'.service.d')/OVERRIDE)
                write_new(target,raw,0o644);created.append(str(target))
            for name, item in observation['before']['registry'].items():
                target = checked(STATE if name == '.' else STATE/name)
                if entry(target) != item: raise ValueError('Private state changed before transfer')
                os.chown(target,observation['target_uid'],observation['target_gid'])
                os.chmod(target,0o700 if item['type']=='directory' else 0o600)
            run(['systemctl','daemon-reload'])
            role = run(['runuser','-u','beroun','--','psql','-X','-w','-qAt','-d','beroun','-c',
                'BEGIN READ ONLY; SELECT current_user; ROLLBACK;']).strip()
            if role != 'beroun': raise ValueError('Actual database peer identity differs')
            run(['systemctl','start','sniper-observation.service'])
            run(['systemctl','start','beroun-strategy-registry-sync.service'])
            run(['systemctl','start','beroun-rich-report.service'])
            for name in SERVICES:
                if run(['systemctl','show',name+'.service','--property=User','--value']).strip() != 'beroun':
                    raise ValueError('Application identity not applied')
            for name, item in observation['before']['registry'].items():
                current = entry(STATE if name == '.' else STATE/name)
                if current['sha256'] != item['sha256'] or current['uid'] != observation['target_uid']:
                    raise ValueError('Registry bytes changed during read-only verification')
            for timer, was_active in prior_timers.items():
                if was_active: run(['systemctl','start',timer])
            outcome = {'status':'APPLICATION_WORKERS_CONSOLIDATED','release':sha,
                'user':'beroun','uid':observation['target_uid'],'created':created,
                'private_bytes_unchanged':True,'prior_active_timers_restored':prior_timers,
                'disabled_t15_preserved':not prior_timers['beroun-paper-t15.timer'],
                'safety_guardian_unchanged':True,'live_activated':False}
        except BaseException as exc:
            stopped = {}
            for timer in prior_timers:
                p=subprocess.run(['systemctl','stop',timer],capture_output=True,text=True,timeout=15)
                stopped[timer]=p.returncode
            outcome={'status':'FAILED_PRESERVED_FOR_RECOVERY','error_type':type(exc).__name__,
                'created':created,'timer_stop_exits':stopped,'recovery':str(operation/'recovery')}
            write_new(operation/'result.json',encode(outcome));raise
        write_new(operation/'result.json',encode(outcome));return outcome


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--release',required=True)
    ap.add_argument('--preview-sha256');ap.add_argument('--apply',action='store_true');args=ap.parse_args()
    if args.apply:
        if not args.preview_sha256:ap.error('--preview-sha256 required for apply')
        result=install(args.release,args.preview_sha256)
    else:
        observation=preview(args.release);result={'preview':observation,'sha256':digest(encode(observation))}
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
