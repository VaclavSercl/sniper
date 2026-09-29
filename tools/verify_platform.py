#!/usr/bin/env python3
"""Offline source checks, then explicit isolated application tests; no installation."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]

def check_sources():
    for directory in ('infra/beroun','tools'):
        for path in (ROOT/directory).glob('*.py'):
            if directory=='tools' and path.name!='verify_platform.py':continue
            compile(path.read_bytes(),str(path),'exec')
    manifest=json.loads((ROOT/'platform/SOURCE_MANIFEST.json').read_text())
    for entry in manifest['files']:
        path=ROOT/entry['path']
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
            raise ValueError('Unsafe source path: '+entry['path'])
        data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=entry['sha256']:
            raise ValueError('Source differs from reviewed manifest: '+entry['path'])
        if path.suffix=='.py':
            compile(data,str(path),'exec')
        if re.search(rb'(?:AKIA|ASIA)[A-Z0-9]{16}|(?:ghp_|gho_)[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{82}|-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----',data):
            raise ValueError('Credential-like content: '+entry['path'])
    return len(manifest['files'])

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources-only',action='store_true')
    args=parser.parse_args()
    print('Reviewed source files:',check_sources(),flush=True)
    if args.sources_only:return 0
    commands=[
        [sys.executable,'-B','-m','unittest','discover','-s','architect/tests','-p','test_*.py','-v'],
        [sys.executable,'-B','platform/tests/postgres_fixture.py','--',sys.executable,'-B','-m','unittest','discover','-s','platform/tests/unit','-p','test_*.py','-v'],
    ]
    for command in commands:
        result=subprocess.run(command,cwd=ROOT,timeout=280)
        if result.returncode:return result.returncode
    print('PASS: consolidated application tests; no trading or service activation')
    return 0

if __name__=='__main__':
    raise SystemExit(main())
