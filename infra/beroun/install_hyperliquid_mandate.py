#!/usr/bin/env python3
"""Preview/install only an absent owner-delegated policy, without live activation."""
import argparse
import grp
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from install_observation import checked, write_new

BASE = Path('/opt/sniper')
CONFIG = Path('/etc/sniper/hyperliquid-mandate.json')
IDENTITY = Path('/etc/sniper/hyperliquid-account.json')


def prepare(sha):
    if not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}', sha): raise ValueError('Invalid release')
    target = checked(CONFIG)
    if target.exists(): raise ValueError('Existing mandate must be preserved and reconciled')
    release = checked(BASE/'releases'/sha)
    if not (BASE/'current').is_symlink() or (BASE/'current').resolve() != release:
        raise ValueError('Expected verified release must be active')
    metadata = json.loads(checked(release/'RELEASE.json').read_bytes())
    if metadata['commit'] != sha: raise ValueError('Release identity mismatch')
    for name in ('platform/research/hyperliquid_mandate.py', 'platform/scripts/sync_strategy_registry.py',
                 'infra/beroun/install_hyperliquid_mandate.py'):
        if hashlib.sha256(checked(release/name).read_bytes()).hexdigest() != metadata['files'][name]:
            raise ValueError('Reviewed source drift')
    sys.path.insert(0, str(release/'platform/research'))
    import hyperliquid_mandate as mandate
    if Path(mandate.__file__).resolve() != release/'platform/research/hyperliquid_mandate.py':
        raise ValueError('Unexpected mandate module')
    identity = checked(IDENTITY)
    if identity.stat().st_uid != 0 or identity.stat().st_mode & 0o777 != 0o640 or identity.stat().st_size > 8192:
        raise ValueError('Untrusted account identity')
    parent = checked(CONFIG.parent)
    if parent.stat().st_uid != 0 or parent.stat().st_mode & 0o022:
        raise ValueError('Untrusted configuration parent')
    config = mandate.validate(mandate.DEFAULTS | {'account_binding_sha256':hashlib.sha256(identity.read_bytes()).hexdigest()})
    raw = json.dumps(config, sort_keys=True, indent=2).encode()
    return {'status':'PREVIEW','release':sha,'target':str(CONFIG),'owner':'root:beroun','mode':'0640',
            'config_sha256':hashlib.sha256(raw).hexdigest(),'config':config,'live_activated':False}, raw


def apply(sha):
    if os.geteuid() != 0: raise ValueError('Authorized root operation required')
    import fcntl
    with checked(BASE/'operations/hyperliquid-mandate-install.lock').open('a') as lock:
        os.chmod(lock.name,0o600);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        preview,raw = prepare(sha)
        operation = checked(BASE/'operations'/('hyperliquid-mandate-install-'+sha))
        operation.mkdir(mode=0o700)
        write_new(operation/'intent.json',json.dumps(preview,indent=2).encode())
        try:
            if prepare(sha)[0] != preview: raise ValueError('Mandate target/source changed')
            write_new(CONFIG,raw,0o640);os.chown(CONFIG,0,grp.getgrnam('beroun').gr_gid)
            script = BASE/'releases'/sha/'platform/scripts/sync_strategy_registry.py'
            argv = ['runuser','-u','beroun','--','python3','-B',str(script),
                    '--candle-observation','/var/lib/sniper/observations/candle.json']
            r = subprocess.run(argv,capture_output=True,text=True,timeout=30)
            report = json.loads(r.stdout)
            data = report['hyperliquid_mandate']
            if r.returncode != 0 or data['status'] != 'OWNER_MANDATE_OBSERVED_POLICY_ONLY' or data['live_eligible'] is not False:
                raise ValueError('Actual mandate report not verified')
            result = {'status':'INSTALLED_POLICY_OBSERVED_NO_TRADING','release':sha,'config_sha256':preview['config_sha256'],
                      'mandate':data,'live_activated':False,'risk_mode_changed':False}
        except BaseException as exc:
            write_new(operation/'result.json',json.dumps({'status':'FAILED_PRESERVED_FOR_RECONCILIATION',
                      'error_type':type(exc).__name__,'live_activated':False},indent=2).encode())
            raise
        write_new(operation/'result.json',json.dumps(result,indent=2).encode())
        return result


def main():
    ap = argparse.ArgumentParser(description=__doc__);ap.add_argument('--release',required=True)
    ap.add_argument('--apply',action='store_true');args=ap.parse_args()
    print(json.dumps(apply(args.release) if args.apply else prepare(args.release)[0],indent=2))


if __name__ == '__main__': main()
