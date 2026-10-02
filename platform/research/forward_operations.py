"""Private forward-state health, transition alerts and verified online backups.

No network, credential access, production restore or epoch migration.
Pruning removes only hash-verified owned backup databases; evidence is retained.
SQLite's online backup API includes committed WAL data without stopping capture.
Alerts are a durable local outbox; delivery to a person needs an explicit adapter.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import sqlite3
import time
import uuid

from hyperliquid_history import safe, encode, integer

GIB = 1024**3
TABLES = ('settings', 'metadata', 'books', 'families', 'candidates', 'screens',
          'trials', 'paper', 'orders', 'marks', 'calibrations', 'health', 'requests', 'comparisons')


def private(path, directory=False, create=False):
    path = safe(path)
    if create:
        if directory: path.mkdir(mode=0o700, parents=True, exist_ok=True)
        else:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
    st = path.stat()
    if path.is_dir() != directory or st.st_mode & 0o077 or st.st_uid != os.getuid():
        raise ValueError('Private owned operation path required')
    return path


def durable(path, value):
    path = safe(path)
    with path.open('x', encoding='utf-8') as stream:
        os.chmod(path, 0o600)
        stream.write(encode(value).decode()+'\n'); stream.flush(); os.fsync(stream.fileno())
    flush(path.parent)


def flush(path):
    if os.name == 'posix':
        fd = os.open(path, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)


def digest(path):
    h = hashlib.sha256()
    with safe(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()


def readonly(path):
    return sqlite3.connect(safe(path).as_uri()+'?mode=ro', uri=True, timeout=3)


def snapshot(con):
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if tables != set(TABLES): raise ValueError('Unexpected forward database schema')
    if con.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise ValueError('Database integrity failed')
    if con.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('Database foreign-key integrity failed')
    return {'settings': dict(con.execute('SELECT key,value FROM settings')),
            'counts': {t: con.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in TABLES}}


def standalone_copy(con, expected_state):
    """Finalize only a newly created private copy, never the live source.

    SQLite backup copies the source's WAL header. Read-only validation of that
    copy otherwise leaves auxiliary files behind. SQLite performs the journal
    transition itself while this owned destination is still open.
    """
    if con.execute('PRAGMA journal_mode=DELETE').fetchone()!=('delete',):
        raise ValueError('Unable to finalize standalone backup copy')
    if snapshot(con)!=expected_state:
        raise ValueError('Copy state changed during journal normalization')


def inspect(root, now_ms=None, *, research_started_ms=None):
    now_ms = int(time.time()*1000) if now_ms is None else integer(now_ms)
    root = private(root, directory=True); path = private(root/'forward.sqlite3')
    sizes = sum(safe(p).stat().st_size for p in
                (path, Path(str(path)+'-wal'), Path(str(path)+'-shm')) if p.exists())
    space = shutil.disk_usage(root); reasons = []
    with closing(readonly(path)) as con:
        con.execute('BEGIN')
        health = dict((i, (stamp, state)) for i, stamp, state in con.execute('SELECT id,time,status FROM health'))
        for identity, age, label in ((1, 60000, 'CAPTURE'), (2, 1800000, 'RESEARCH')):
            row = health.get(identity)
            # Internal maintenance may inspect the exact current RUNNING cycle.
            # External status never considers RUNNING successful.
            in_progress = (identity == 2 and research_started_ms is not None and
                           row == (research_started_ms, 'RUNNING'))
            if not row or (row[1] != 'PASS' and not in_progress) or not 0 <= now_ms-row[0] <= age:
                reasons.append(label+'_STALE_FAILED_OR_FUTURE')
        streams = []
        for market, count, first, last in con.execute('SELECT market,count(*),min(received),max(received) FROM books GROUP BY market ORDER BY market'):
            gaps = con.execute('SELECT count(*) FROM (SELECT received-lag(received) OVER (ORDER BY sent) AS gap FROM books WHERE market=?) WHERE gap>30000 OR gap<0', (market,)).fetchone()[0]
            streams.append({'market':market, 'snapshots':count, 'first_ms':first, 'last_ms':last, 'gaps_over_30s_or_backwards':gaps})
            if not 0 <= now_ms-last <= 30000: reasons.append('BOOK_STALE_OR_FUTURE')
        if len(streams) != 2: reasons.append('TWO_MARKET_STREAMS_REQUIRED')
        rows = con.execute('SELECT end FROM candidates ORDER BY end').fetchall()
    if sizes >= 8*GIB*8//10: reasons.append('FORWARD_STORAGE_ABOVE_80_PERCENT')
    if space.free < 2*GIB: reasons.append('FILESYSTEM_FREE_BELOW_2_GIB')
    earliest = min((s['first_ms'] for s in streams), default=now_ms)
    elapsed = now_ms-earliest
    growth = sizes*86400000//elapsed if elapsed >= 3600000 else None
    horizon = max(0, max((r[0] for r in rows), default=now_ms)-now_ms)
    forecast = sizes+growth*horizon//86400000 if growth is not None else None
    if forecast is not None and forecast > 8*GIB: reasons.append('PROJECTED_HISTORY_EXCEEDS_STORAGE_BUDGET')
    return {'schema':1, 'observed_ms':now_ms, 'status':'ALERT' if reasons else 'PASS',
            'reasons':sorted(set(reasons)), 'streams':streams, 'database_bytes':sizes,
            'filesystem_free_bytes':space.free, 'estimated_daily_growth_bytes':growth,
            'estimated_bytes_at_last_registered_window':forecast,
            'gap_scope':'ENTIRE_ARCHIVE_DIAGNOSTIC_NOT_AUTOMATIC_INVALIDATION_OF_LATER_WINDOWS',
            'external_alert_delivery':'NOT_CONFIGURED'}


def record_alert(root, report):
    outbox = private(Path(root)/'operations', directory=True, create=True)
    private(outbox/'alerts', directory=True, create=True)
    path = safe(outbox/'alerts/state.sqlite3')
    private(path, create=not path.exists())
    for suffix in ('-wal','-shm','-journal'): safe(Path(str(path)+suffix))
    with closing(sqlite3.connect(path, timeout=3)) as con:
        con.execute('CREATE TABLE IF NOT EXISTS transitions(id INTEGER PRIMARY KEY,time INTEGER,state TEXT,body TEXT)')
        state = encode({'status':report['status'], 'reasons':report['reasons']}).decode()
        previous = con.execute('SELECT state FROM transitions ORDER BY id DESC LIMIT 1').fetchone()
        if previous and previous[0] == state: return {'status':'UNCHANGED'}
        with con:
            con.execute('INSERT INTO transitions(time,state,body) VALUES(?,?,?)',
                        (report['observed_ms'], state, encode(report).decode()))
    return {'status':'TRANSITION_RECORDED', 'delivery':'LOCAL_OUTBOX_ONLY'}


def backup_inventory(archive):
    """Unknown, partial or modified attempts block; never adopt or retry them."""
    verified=[]; used=0
    for folder in archive.iterdir():
        safe(folder)
        if folder.name == 'writer.lock':
            private(folder)
            continue
        if not re.fullmatch('[0-9a-f]{32}',folder.name): raise ValueError('Unknown backup artifact')
        private(folder,directory=True)
        names={p.name for p in folder.iterdir()}
        for name in names: private(folder/name)
        allowed={'intent.json','result.json','forward.sqlite3','prune-intent.json','pruned.json'}
        if not names <= allowed or not {'intent.json','result.json'} <= names:
            raise ValueError('Incomplete backup requires explicit reconciliation')
        result_path=private(folder/'result.json')
        if result_path.stat().st_size>65536: raise ValueError('Oversized backup evidence')
        data=json.loads(result_path.read_bytes())
        if (data.get('schema')!=2 or data.get('status')!='PASS' or data.get('restore_verified') is not True or
            data.get('intent_sha256')!=digest(folder/'intent.json')):
            raise ValueError('Unverified backup evidence')
        used+=sum(p.stat().st_size for p in folder.iterdir())
        if 'pruned.json' in names:
            retired=json.loads((folder/'pruned.json').read_bytes())
            if ('forward.sqlite3' in names or 'prune-intent.json' not in names or
                retired != json.loads((folder/'prune-intent.json').read_bytes()) or
                retired.get('database_sha256')!=data['database_sha256'] or
                retired.get('result_sha256')!=digest(result_path)):
                raise ValueError('Interrupted or changed pruning evidence')
            continue
        if 'prune-intent.json' in names: raise ValueError('Interrupted pruning requires reconciliation')
        if 'forward.sqlite3' not in names or digest(folder/'forward.sqlite3')!=data['database_sha256']:
            raise ValueError('Existing backup changed')
        verified.append((integer(data['created_ms']),folder,data))
    return sorted(verified,key=lambda row:(row[0],row[1].name)),used


def retain_two(archive):
    copies,_=backup_inventory(archive)
    for _,folder,data in copies[:-2]:
        proof={'schema':1,'kind':'PRUNE_VERIFIED_OWNED_DATABASE',
               'database_sha256':data['database_sha256'],'result_sha256':digest(folder/'result.json')}
        durable(folder/'prune-intent.json',proof)
        target=private(folder/'forward.sqlite3')
        if digest(target)!=proof['database_sha256']: raise ValueError('Backup changed before pruning')
        target.unlink(); flush(folder)
        durable(folder/'pruned.json',proof)


def backup(root, now_ms=None, max_backup_bytes=24*GIB):
    now_ms = int(time.time()*1000) if now_ms is None else integer(now_ms)
    root = private(root, directory=True); source = private(root/'forward.sqlite3')
    # parents=True does not apply mode to intermediate directories.
    operations = private(root/'operations', directory=True, create=True)
    archive = private(operations/'backups', directory=True, create=True)
    import fcntl
    lock = safe(archive/'writer.lock')
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a+b') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        copies,used=backup_inventory(archive)
        if any(stamp>now_ms for stamp,_,_ in copies): raise ValueError('Backup clock moved backwards')
        for stamp,folder,_ in copies:
            if stamp//86400000 == now_ms//86400000:
                retain_two(archive)
                return {'status':'ALREADY_VERIFIED_TODAY', 'path':str(folder)}
        expected = sum(p.stat().st_size for p in (source, Path(str(source)+'-wal')) if p.exists())
        if used+expected*2 > max_backup_bytes or shutil.disk_usage(root).free < expected*2+2*GIB:
            raise ValueError('Backup budget/free space unavailable; existing copies preserved')
        folder = private(archive/uuid.uuid4().hex, directory=True, create=True)
        durable(folder/'intent.json', {'schema':1,'created_ms':now_ms,'kind':'ONLINE_FORWARD_BACKUP','source':str(source)})
        target = private(folder/'forward.sqlite3', create=True)
        deadline = time.monotonic()+300
        def progress(*_):
            if time.monotonic()>deadline: raise TimeoutError('Bounded backup deadline')
            size=target.stat().st_size
            if used+size*2>max_backup_bytes or shutil.disk_usage(root).free<size+2*GIB:
                raise ValueError('Growing backup exceeds copy/restore space budget')
        with closing(readonly(source)) as src, closing(sqlite3.connect(target)) as dst:
            src.backup(dst, pages=256, progress=progress, sleep=.01)
            expected_state = snapshot(dst)
            standalone_copy(dst,expected_state)
        with target.open('rb') as stream: os.fsync(stream.fileno())
        result = {'schema':2, 'status':'PASS', 'created_ms':now_ms,
                  'database_sha256':digest(target), 'snapshot':expected_state,
                  'intent_sha256':digest(folder/'intent.json'),
                  'restore_verified':False, 'retention':'TWO_VERIFIED_DATABASES_EVIDENCE_RETAINED'}
        # Actually restore into a separate absent private directory and validate.
        restore(folder, folder/'restore-probe', manifest=result)
        probe=private(folder/'restore-probe',directory=True)
        expected_names={'forward.sqlite3','restore-result.json'}
        if {p.name for p in probe.iterdir()}!=expected_names: raise ValueError('Unknown restore artifact')
        proof_path=private(probe/'restore-result.json')
        proof=json.loads(proof_path.read_bytes())
        copied=private(probe/'forward.sqlite3')
        if digest(copied)!=proof['restored_sha256'] or proof['backup_sha256']!=result['database_sha256']:
            raise ValueError('Temporary restored data changed')
        result['restore_proof']=proof
        copied.unlink();proof_path.unlink();probe.rmdir();flush(folder)
        result['restore_verified'] = True
        durable(folder/'result.json', result)
        retain_two(archive)
        return {**result, 'path':str(folder)}


def restore(backup_root, target, manifest=None):
    backup_root = private(backup_root, directory=True)
    if manifest is None:
        result_path = private(backup_root/'result.json')
        if result_path.stat().st_size > 65536: raise ValueError('Oversized backup evidence')
        manifest = json.loads(result_path.read_bytes())
    source = private(backup_root/'forward.sqlite3'); target = safe(target)
    if target.exists(): raise ValueError('Restore target must be absent; never overwrite production')
    if manifest.get('status') != 'PASS' or digest(source) != manifest.get('database_sha256'):
        raise ValueError('Backup fingerprint/result mismatch')
    with closing(readonly(source)) as con:
        if snapshot(con) != manifest['snapshot']: raise ValueError('Backup state evidence changed')
    target = private(target, directory=True, create=True)
    copied = private(target/'forward.sqlite3', create=True)
    with closing(readonly(source)) as src, closing(sqlite3.connect(copied)) as dst:
        src.backup(dst)
        standalone_copy(dst,manifest['snapshot'])
    with closing(readonly(copied)) as con:
        if snapshot(con) != manifest['snapshot']: raise ValueError('Restored state differs')
    with copied.open('rb') as stream: os.fsync(stream.fileno())
    durable(target/'restore-result.json', {'schema':1,'status':'PASS','backup_sha256':manifest['database_sha256'],
        'restored_sha256':digest(copied),'snapshot':manifest['snapshot']})
    return {'status':'PASS','target':str(target)}


def maintain(root, now_ms=None, *, research_started_ms=None):
    report = inspect(root, now_ms,research_started_ms=research_started_ms)
    try: report['backup'] = backup(root, now_ms)
    except (OSError, ValueError, sqlite3.Error, TimeoutError) as exc:
        report['backup'] = {'status':'BLOCKED','error_type':type(exc).__name__}
        report['status'] = 'ALERT'; report['reasons'].append('VERIFIED_BACKUP_UNAVAILABLE')
    report['alert'] = record_alert(root, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('status','maintain','restore'))
    parser.add_argument('--state-dir', type=Path, default=Path('/var/lib/sniper/forward-research-v1'))
    parser.add_argument('--backup-dir', type=Path)
    parser.add_argument('--restore-to', type=Path)
    args = parser.parse_args()
    if args.command == 'restore':
        if not args.backup_dir or not args.restore_to: parser.error('Explicit backup and absent restore target required')
        result = restore(args.backup_dir,args.restore_to)
    else: result = maintain(args.state_dir) if args.command == 'maintain' else inspect(args.state_dir)
    print(json.dumps(result,indent=2))
    return 0 if result['status']=='PASS' else 2


if __name__=='__main__': raise SystemExit(main())
