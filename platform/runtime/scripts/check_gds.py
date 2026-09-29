#!/usr/bin/env python3
"""Report the kernel GDS status truthfully; never change CPU mitigations."""
import argparse
import json
from pathlib import Path

SYSFS = '/sys/devices/system/cpu/vulnerabilities/gather_data_sampling'
# Exact states documented by Linux; unknown/new states require review.
MITIGATED = {'Not affected', 'Mitigation: Microcode', 'Mitigation: Microcode (locked)',
             'Mitigation: AVX disabled, no microcode'}


def classify(text):
    state = text.strip()
    if state in MITIGATED:
        return 'PASS'
    if state in {'Vulnerable', 'Vulnerable: No microcode'}:
        return 'FAIL'
    return 'BLOCKED'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--status-file', type=Path, default=Path(SYSFS))
    args = parser.parse_args(argv)
    try:
        with args.status_file.open(encoding='utf-8') as stream:
            state = stream.read(512).strip()
        result = classify(state)
    except (OSError, UnicodeError):
        state, result = 'UNREADABLE', 'BLOCKED'
    print(json.dumps({'check': 'gather_data_sampling', 'status': result, 'kernel_state': state}))
    return {'PASS': 0, 'FAIL': 1, 'BLOCKED': 2}[result]


if __name__ == '__main__': raise SystemExit(main())
