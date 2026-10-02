"""Bounded PostgreSQL access through the caller's existing peer role, no sudo.

Connection/SQL error details are never emitted. All errors remain failures even
for older callers passing check=False; absent data is distinct from failed SQL.
"""
import subprocess


def psql(sql, check=True):
    result = subprocess.run(['psql', '-X', '-w', '-v', 'ON_ERROR_STOP=1', '-d', 'beroun',
        '-t', '-A', '-f', '-'], input=sql, capture_output=True, text=True, timeout=30)
    if result.returncode: raise RuntimeError('PostgreSQL command failed; diagnostic retained by server')
    return result.stdout.strip()
