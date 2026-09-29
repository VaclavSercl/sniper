#!/usr/bin/env python3
"""Bounded research queue. No provider calls, Git publication or exchange writes.

Candidates are deterministic parameter hypotheses, not AI-discovered strategies.
SQLite transactions serialize daily proposals and immutable evaluation records.
Only the existing Hydra simulator is supported; other strategies are explicitly
unqualified. The current execution model cannot authorize paper/live promotion.
"""
import argparse
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import importlib.util
import itertools
import json
import math
import os
import shutil
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SIMULATOR = ROOT / 'architect' / 'counterfactual_backtest.py'
CATALOG = {
    'T12': 'Documented backtest not reproduced; no registered simulator',
    'T13': 'Unit-tested research model; no qualified execution evidence',
    'T14': 'Unit-tested research model; no qualified execution evidence',
    'T15': 'Paused; validated quotes, settled funding and new epoch required',
    'T16': 'External host claim; not verified on Beroun',
    'P019': 'Proposal only; no implemented backtest',
    'hydra': 'Uncalibrated execution model',
    'moonshot': 'No registered strategy-specific simulator',
    'grid': 'No registered strategy-specific simulator',
    'trigon': 'No registered strategy-specific simulator',
    'nexus': 'No registered strategy-specific simulator',
}

def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def file_hash(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''): h.update(chunk)
    return h.hexdigest()

def retain(root, source, suffix):
    """Content-addressed reproduction input; never replace a different file."""
    folder=safe_path(Path(root)/'evidence-inputs')
    folder.mkdir(mode=0o700,exist_ok=True)
    digest=file_hash(source)
    target=safe_path(folder/(digest+suffix))
    if target.exists():
        if file_hash(target)!=digest: raise ValueError('Evidence input corrupted')
    else:
        with source.open('rb') as src, target.open('xb') as dst:
            shutil.copyfileobj(src,dst);dst.flush();os.fsync(dst.fileno())
        if file_hash(target)!=digest: raise ValueError('Evidence input changed')
    return str(target.relative_to(root))

def safe_path(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Symlink path refused')
    return path

@contextmanager
def database(root):
    root = safe_path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = safe_path(root/'research.sqlite3')
    con = sqlite3.connect(path, timeout=5)
    try:
        con.execute('PRAGMA foreign_keys=ON')
        con.executescript('''
          CREATE TABLE IF NOT EXISTS proposals(
            id TEXT PRIMARY KEY, day TEXT UNIQUE NOT NULL,
            bot TEXT NOT NULL, params TEXT NOT NULL, created_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS evaluations(
            proposal_id TEXT PRIMARY KEY REFERENCES proposals(id),
            status TEXT NOT NULL, evidence TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS prior_evaluations(
            attempt INTEGER PRIMARY KEY, proposal_id TEXT NOT NULL,
            status TEXT NOT NULL, evidence TEXT NOT NULL, archived_at TEXT NOT NULL);
        ''')
        yield con
    finally:
        con.close()

def propose(con, now):
    """At most one proposal/day, each parameter set once; no OOS retuning."""
    day = now.date().isoformat()
    con.execute('BEGIN IMMEDIATE')
    try:
        row = con.execute('SELECT id FROM proposals WHERE day=?',(day,)).fetchone()
        if row:
            con.commit(); return {'status':'ALREADY_PROPOSED','id':row[0]}
        for step, position, gamma in itertools.product((2.0,4.0,8.0),(0.001,0.003),(0.05,0.1)):
            params={'grid_step':step,'max_position':position,'gamma':gamma}
            identity='hydra-'+sha(encoded(params).encode())[:16]
            if not con.execute('SELECT 1 FROM proposals WHERE id=?',(identity,)).fetchone():
                con.execute('INSERT INTO proposals VALUES(?,?,?,?,?)',
                            (identity,day,'hydra',encoded(params),now.isoformat()))
                con.commit(); return {'status':'PROPOSED','id':identity,'params':params}
        con.commit(); return {'status':'SEARCH_SPACE_EXHAUSTED'}
    except BaseException:
        con.rollback(); raise

def policy_values(path):
    raw = safe_path(path).read_bytes()
    p = json.loads(raw)
    # No default account cost or allocation. These are research assumptions.
    if set(p) != {'maker_fee','initial_capital','cost_basis'}:
        raise ValueError('Explicit research cost policy required')
    for key in ('maker_fee','initial_capital'):
        if isinstance(p[key],bool) or not isinstance(p[key],(int,float)) or not math.isfinite(p[key]):
            raise ValueError('Invalid research cost policy')
    if not 0 <= p['maker_fee'] < 1 or p['initial_capital'] <= 0:
        raise ValueError('Unverified rebates or invalid capital')
    if not isinstance(p['cost_basis'],str) or not p['cost_basis'].strip():
        raise ValueError('Cost provenance required')
    return p,sha(raw)

def evaluate(con, root, data_path, split_path, policy_path, now):
    """Evaluate one oldest untested hypothesis against a private SQLite snapshot.

    Lock is deliberately held for the bounded offline test: a competing worker
    times out rather than running the same candidate twice. Process termination
    rolls back the transaction. Failed results remain immutable, not auto-retuned.
    """
    con.execute('BEGIN IMMEDIATE')
    try:
        row=con.execute('SELECT p.id,p.bot,p.params FROM proposals p LEFT JOIN evaluations e '
                        'ON p.id=e.proposal_id WHERE e.proposal_id IS NULL ORDER BY p.day LIMIT 1').fetchone()
        if not row:
            con.commit(); return {'status':'NO_PENDING_PROPOSALS'}
        identity,bot,params=row
        evidence={'schema':1,'proposal_id':identity,'bot':bot,'evaluated_at':now.isoformat(),
                  'scope':'RESEARCH_ONLY','live_eligible':False,'paper_eligible':False,
                  'runner_sha256':file_hash(Path(__file__))}
        status='BLOCKED'
        try:
            policy,policy_hash=policy_values(policy_path)
            data=safe_path(data_path); split=safe_path(split_path)
            evidence['policy_sha256']=policy_hash
            split_bytes=split.read_bytes()
            split_value=json.loads(split_bytes)
            end=split_value['oos_end_ms']
            if isinstance(end,bool) or not isinstance(end,int) or end > int(now.timestamp()*1000):
                raise ValueError('Future or invalid evaluation interval')
            if not data.is_file(): raise FileNotFoundError('Market data absent')
            evidence['split_sha256']=sha(split_bytes)
            params=json.loads(params)
            params.update({k:policy[k] for k in ('maker_fee','initial_capital')})
            with tempfile.TemporaryDirectory(prefix='evaluation-',dir=root) as temp:
                snapshot=Path(temp)/'market.sqlite3'; pinned_split=Path(temp)/'split.json'
                pinned_split.write_bytes(split_bytes)
                source=sqlite3.connect(data.resolve().as_uri()+'?mode=ro',uri=True)
                dest=sqlite3.connect(snapshot)
                try: source.backup(dest)
                finally: dest.close(); source.close()
                evidence['data_sha256']=file_hash(snapshot)
                evidence['data_artifact']=retain(root,snapshot,'.sqlite3')
                evidence['split_artifact']=retain(root,pinned_split,'.json')
                # Persist exact policy bytes used, refusing a change after validation.
                if file_hash(Path(policy_path))!=policy_hash: raise ValueError('Policy changed')
                evidence['policy_artifact']=retain(root,Path(policy_path),'.json')
                simulator=safe_path(SIMULATOR)
                evidence['simulator_sha256']=file_hash(simulator)
                evidence['simulator_artifact']=retain(root,simulator,'.py')
                spec=importlib.util.spec_from_file_location('lifecycle_simulator',simulator)
                module=importlib.util.module_from_spec(spec)
                sys.modules[spec.name]=module
                spec.loader.exec_module(module)
                module.DB_PATH=str(snapshot); module.WF_SPLIT_PATH=str(pinned_split)
                # Do not silently combine an immutable snapshot with mutable Parquet.
                module._lake_rows=lambda kind: iter(())
                evidence['data_sources']=['sqlite_snapshot_only']
                ins,oos=module.run_full_walk_forward(bot,params)
                evidence['in_sample']=asdict(ins); evidence['out_of_sample']=asdict(oos)
                if file_hash(simulator)!=evidence['simulator_sha256']:
                    raise RuntimeError('Simulator changed during evaluation')
                reasons=sorted(set(ins.fail_reasons+oos.fail_reasons))
                evidence['reasons']=reasons
                # Existing simulator explicitly marks its execution model unverified.
                # Even successful research metrics are not paper/live qualification.
                status='RESEARCH_PASS' if ins.passed and oos.passed and not reasons else 'RESEARCH_REJECTED'
        except (OSError,ValueError,KeyError,TypeError,RuntimeError,sqlite3.Error) as exc:
            evidence['reasons']=['EVALUATION_BLOCKED_'+type(exc).__name__]
            status='BLOCKED'
        evidence['status']=status
        con.execute('INSERT INTO evaluations VALUES(?,?,?)',(identity,status,encoded(evidence)))
        con.commit(); return evidence
    except BaseException:
        con.rollback(); raise

def retry_blocked(con, identity, now):
    """Explicit retry archives the old failure atomically; never clears rejections."""
    con.execute('BEGIN IMMEDIATE')
    try:
        row=con.execute('SELECT status,evidence FROM evaluations WHERE proposal_id=?',(identity,)).fetchone()
        if row is None or row[0]!='BLOCKED': raise ValueError('Only blocked attempts may be retried')
        attempts=con.execute('SELECT count(*) FROM prior_evaluations WHERE proposal_id=?',(identity,)).fetchone()[0]
        if attempts>=3: raise ValueError('Retry budget exhausted')
        con.execute('INSERT INTO prior_evaluations(proposal_id,status,evidence,archived_at) VALUES(?,?,?,?)',
                    (identity,row[0],row[1],now.isoformat()))
        con.execute('DELETE FROM evaluations WHERE proposal_id=?',(identity,))
        con.commit()
    except BaseException:
        con.rollback();raise
    return {'status':'REQUEUED','id':identity,'prior_attempts':attempts+1}

def report(con):
    rows=con.execute('SELECT p.id,p.bot,p.day,e.status,e.evidence FROM proposals p '
                     'LEFT JOIN evaluations e ON p.id=e.proposal_id ORDER BY p.day').fetchall()
    return {'schema':1,'live_eligible':0,'paper_qualified':0,
            'catalog':[{'id':k,'status':'UNQUALIFIED','reason':v} for k,v in CATALOG.items()],
            'proposals':[{'id':r[0],'bot':r[1],'day':r[2],'status':r[3] or 'PROPOSED',
                          'evidence':json.loads(r[4]) if r[4] else None} for r in rows]}

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action',choices=['propose','evaluate','cycle','status','retry-blocked'])
    ap.add_argument('--proposal-id')
    ap.add_argument('--state-dir',type=Path,required=True)
    ap.add_argument('--data',type=Path)
    ap.add_argument('--split',type=Path)
    ap.add_argument('--policy',type=Path)
    args=ap.parse_args(); now=datetime.now(timezone.utc)
    os.umask(0o077)
    with database(args.state_dir) as con:
        result={}
        if args.action in ('propose','cycle'): result['proposal']=propose(con,now)
        if args.action in ('evaluate','cycle'):
            if not all((args.data,args.split,args.policy)):
                ap.error('Evaluation requires --data, --split and --policy')
            result['evaluation']=evaluate(con,args.state_dir,args.data,args.split,args.policy,now)
        if args.action=='status': result=report(con)
        if args.action=='retry-blocked':
            if not args.proposal_id: ap.error('--proposal-id required')
            result=retry_blocked(con,args.proposal_id,now)
        print(json.dumps(result,indent=2,allow_nan=False))
        return 2 if result.get('evaluation',{}).get('status')=='BLOCKED' else 0

if __name__=='__main__':
    raise SystemExit(main())
