#!/usr/bin/env python3
"""Preview or reversibly disable the single reviewed broad polkit grant."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat

RULE = Path('/etc/polkit-1/rules.d/51-sudo-manage-units.rules')


def revoke(path, expected, apply=False):
    for part in (path, *path.parents):
        if part.is_symlink(): raise ValueError('Symlink policy path')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('Unsafe policy file')
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected: raise ValueError('Policy changed since approval')
    backup = path.with_name(path.name + '.disabled-' + digest)
    if backup.exists() or backup.is_symlink(): raise ValueError('Recovery target already exists')
    result = {'status': 'PREVIEW', 'path': str(path), 'recovery': str(backup),
              'sha256': digest, 'other_rules_reviewed': False}
    if not apply: return result
    if os.geteuid() != 0: raise ValueError('Approved root operation required')
    lock = path.with_name(path.name + '.change-lock')
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Concurrent policy change')
        if backup.exists() or backup.is_symlink(): raise ValueError('Recovery conflict')
        os.rename(path, backup)  # Same directory: preserve bytes, ownership and mode.
        directory = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
        if hashlib.sha256(backup.read_bytes()).hexdigest() != expected:
            raise ValueError('Recovery verification failed')
        result['status'] = 'DISABLED_RECOVERABLE'
        return result
    finally:
        lock.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(revoke(RULE, args.expected_sha256, args.apply), indent=2))


if __name__ == '__main__': main()
