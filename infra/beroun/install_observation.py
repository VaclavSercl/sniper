#!/usr/bin/env python3
"""Preview/install the new observation units and one owned reporting override.

Existing unit/override hashes must be explicitly pinned. Never rewrite those
files, private state, old releases or unknown overrides. Failures retain durable
evidence and disable only the newly created timer; reruns require reconciliation.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

BASE = Path('/opt/sniper')
UNITS = Path('/etc/systemd/system')
STATE = Path('/var/lib/sniper/observations')
REPORT = 'beroun-strategy-registry-sync.service'


def checked(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Unsafe installation path')
    if path.is_file() and path.stat().st_nlink != 1: raise ValueError('Hardlinked installation target')
    return path


def digest(raw): return hashlib.sha256(raw).hexdigest()


def write_new(path, raw, mode=0o600):
    checked(path)
    with path.open('xb') as stream:
        os.chmod(path, mode); stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def run(argv):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    if result.returncode: raise RuntimeError('Installation command failed: '+argv[0])
    return result.stdout


def preview(sha, prior_unit, prior_override):
    if not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}', sha): raise ValueError('Invalid release')
    release = checked(BASE/'releases'/sha)
    if not (BASE/'current').is_symlink() or (BASE/'current').resolve() != release:
        raise ValueError('Expected release must be active')
    metadata = json.loads(checked(release/'RELEASE.json').read_bytes())
    if metadata['commit'] != sha: raise ValueError('Release mismatch')
    prior = {}
    for path, expected in ((UNITS/REPORT, prior_unit),
            (UNITS/(REPORT+'.d')/'90-sniper-source.conf', prior_override)):
        if not re.fullmatch('[0-9a-f]{64}', expected) or digest(checked(path).read_bytes()) != expected:
            raise ValueError('Existing reporting configuration changed')
        prior[str(path)] = expected
    directory = checked(UNITS/(REPORT+'.d'))
    if sorted(p.name for p in directory.iterdir()) != ['90-sniper-source.conf']:
        raise ValueError('Unknown reporting overrides require review')
    files = {}
    for name, target in (('sniper-observation.service', UNITS/'sniper-observation.service'),
            ('sniper-observation.timer', UNITS/'sniper-observation.timer'),
            ('95-sniper-observation.conf', directory/'95-sniper-observation.conf')):
        source = checked(release/'infra/beroun'/name)
        fingerprint = digest(source.read_bytes())
        if metadata['files'][source.relative_to(release).as_posix()] != fingerprint:
            raise ValueError('Reviewed source changed')
        if checked(target).exists(): raise ValueError('Target already exists; preserve and reconcile')
        files[str(target)] = {'source': str(source), 'sha256': fingerprint}
    if checked(STATE).exists(): raise ValueError('Existing observation state requires reconciliation')
    checked(BASE/'operations'); checked(STATE.parent)
    return {'status': 'PREVIEW', 'release': sha, 'files': files, 'prior': prior,
        'producer': 'beroun', 'reporter': 'wwwenda', 'private_state_permissions_changed': False,
        'live_activated': False}


def install(sha, prior_unit, prior_override):
    if os.geteuid() != 0: raise ValueError('Approved root installation required')
    import fcntl
    lock_path = checked(BASE/'operations'/'observation-install.lock')
    with lock_path.open('a') as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        observation = preview(sha, prior_unit, prior_override)
        operation = checked(BASE/'operations'/('observation-install-'+sha))
        operation.mkdir(mode=0o700)
        write_new(operation/'intent.json', json.dumps(observation, indent=2).encode())
        for name in observation['prior']:
            write_new(operation/(Path(name).name+'.previous'), Path(name).read_bytes())
        if preview(sha, prior_unit, prior_override) != observation: raise ValueError('Targets changed')
        created = []
        try:
            for name, item in observation['files'].items():
                raw = Path(item['source']).read_bytes()
                if digest(raw) != item['sha256']: raise ValueError('Source changed')
                write_new(Path(name), raw, 0o644); created.append(name)
            run(['systemctl', 'daemon-reload'])
            run(['systemctl', 'start', 'sniper-observation.service'])
            script = BASE/'releases'/sha/'platform/scripts/sync_strategy_registry.py'
            report = json.loads(run(['runuser', '-u', 'wwwenda', '--', 'python3', '-B', str(script),
                '--state-dir', '/var/lib/sniper/research', '--candle-observation', str(STATE/'candle.json')]))
            if report['status'] != 'OBSERVED' or report['live_eligible'] != 0:
                raise ValueError('Actual least-privilege reporting incomplete')
            run(['systemctl', 'start', REPORT])
            run(['systemctl', 'enable', '--now', 'sniper-observation.timer'])
            run(['systemctl', 'is-active', 'sniper-observation.timer'])
            if any(digest(Path(p).read_bytes()) != h for p, h in observation['prior'].items()):
                raise ValueError('Previous configuration changed')
            outcome = {'status': 'INSTALLED_OBSERVED_TIMER_ACTIVE', 'release': sha,
                'created': created, 'report_status': report['status'], 'observation_at': report['observation_at'],
                'private_state_preserved': True, 'live_activated': False}
        except BaseException as exc:
            stop = subprocess.run(['systemctl', 'disable', '--now', 'sniper-observation.timer'],
                capture_output=True, text=True, timeout=30)
            outcome = {'status': 'FAILED_PRESERVED_FOR_RECONCILIATION', 'error_type': type(exc).__name__,
                'created': created, 'timer_disable_exit': stop.returncode, 'private_state_preserved': True}
            write_new(operation/'result.json', json.dumps(outcome, indent=2).encode())
            raise
        write_new(operation/'result.json', json.dumps(outcome, indent=2).encode())
        return outcome


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--release', required=True)
    ap.add_argument('--prior-unit-sha256', required=True)
    ap.add_argument('--prior-override-sha256', required=True)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    print(json.dumps((install if args.apply else preview)(args.release,
        args.prior_unit_sha256, args.prior_override_sha256), indent=2))


if __name__ == '__main__': main()
