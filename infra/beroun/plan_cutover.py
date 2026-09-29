#!/usr/bin/env python3
"""Read-only systemd source-path migration plan. Never starts or changes a service."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[2]
PREFIXES={
    '/home/wwwenda/beroun-github':'/opt/sniper/current/platform',
    '/opt/beroun/repo':'/opt/sniper/current/platform/legacy',
    '/opt/beroun/services':'/opt/sniper/current/platform/runtime',
}
UNITS=(
    'beroun-gateway.service','beroun-ingest.service','beroun-risk_kernel.service',
    'beroun-watchdog.service','beroun-ingest-market.service','beroun-klines.service',
    'beroun-paper-tick.service','beroun-paper-t15.service','beroun-perfect-ingest.service',
    'beroun-retention-manager.service','beroun-rich-report.service',
    'beroun-strategy-registry-sync.service','beroun-reconcile.service',
    'beroun-backup.service','beroun-restore-test.service','beroun-github-backup.service',
    'beroun-outbox.service','beroun-post-reboot-check.service',
)

def rewrite(value):
    for old,new in PREFIXES.items():value=value.replace(old,new)
    return value

def render(text):
    section=''
    selected=[]
    for line in text.splitlines():
        if line.strip().startswith('['):section=line.strip()
        if section!='[Service]' or '=' not in line:continue
        key,value=line.split('=',1)
        if key not in ('ExecStart','WorkingDirectory'):continue
        if rewrite(value)==value:continue
        if value.endswith('\\'):raise ValueError('Multiline command needs explicit review')
        if key=='ExecStart':selected.append('ExecStart=')
        selected.append(key+'='+rewrite(value))
    if not selected:return None
    return '[Service]\n'+'\n'.join(selected)+'\n'

def inspect(unit):
    raw=subprocess.check_output(['systemctl','show',unit,'-p','FragmentPath','-p',
                                 'DropInPaths','-p','ActiveState','-p','User'],text=True)
    properties=dict(line.split('=',1) for line in raw.splitlines() if '=' in line)
    if properties.get('DropInPaths'):
        raise ValueError(unit+': existing drop-ins require separate merge review')
    path=Path(properties['FragmentPath'])
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError(unit+': unit source not safely readable')
    data=path.read_bytes()
    content=render(data.decode('utf-8'))
    return dict(unit=unit,fragment=str(path),fragment_sha256=hashlib.sha256(data).hexdigest(),
                properties=properties,dropin=content)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    entries=[inspect(unit) for unit in UNITS]
    manifest=json.loads((ROOT/'platform/SOURCE_MANIFEST.json').read_text())
    for entry in entries:
        if not entry['dropin']:continue
        # Every script path from the current unit must exist in the candidate.
        import re
        for script in re.findall(r'(/opt/sniper/current/[^\s;\x27\x22]+\.(?:py|sh))',entry['dropin']):
            if not (ROOT/script.removeprefix('/opt/sniper/current/')).is_file():
                raise ValueError('Missing deployment source: '+script)
    result={'schema_version':1,'canonical_repository':'VaclavSercl/sniper',
            'new_root':'/opt/sniper/current','units':entries,
            'source_manifest_sha256':hashlib.sha256((ROOT/'platform/SOURCE_MANIFEST.json').read_bytes()).hexdigest(),
            'qualification':'BLOCKED','enable_funded_trading':False,
            'note':'Plan only. T15 timer must be paused until a qualified v2 epoch exists.'}
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print('Prepared',sum(bool(e['dropin']) for e in entries),'unit path changes; no services changed')

if __name__=='__main__':main()
