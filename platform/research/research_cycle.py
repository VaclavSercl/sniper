#!/usr/bin/env python3
"""Finite preregistered research registry. No providers, generated code or orders.

The six blueprints are an offline library, not unlimited AI invention. Separate
market variants and uncalibrated screens can never grant paper/live eligibility.
"""
import argparse
from contextlib import closing
from datetime import datetime,timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import zlib
import research_protocol as protocol
import hyperliquid_history as history
import hyperliquid_account as account

MAX_EPOCH=32*1024*1024
MAX_REGISTRY=128*1024*1024
MODEL_PATHS=tuple(Path(module.__file__).resolve() for module in (protocol,history,account))+(Path(__file__).resolve(),)
MODEL_BYTES=tuple(path.read_bytes() for path in MODEL_PATHS)
SCHEMA='''CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE epoch(id INTEGER PRIMARY KEY,sha256 TEXT NOT NULL,raw BLOB NOT NULL);
CREATE TABLE families(id TEXT PRIMARY KEY,day TEXT NOT NULL,record TEXT NOT NULL);
CREATE TABLE variants(id TEXT PRIMARY KEY,family TEXT NOT NULL REFERENCES families(id),market TEXT NOT NULL,
 UNIQUE(family,market));
CREATE TABLE attempts(variant TEXT PRIMARY KEY REFERENCES variants(id),day TEXT NOT NULL,status TEXT NOT NULL,
 holdout_consumed INTEGER NOT NULL,record TEXT NOT NULL);
CREATE TABLE comparisons(week TEXT PRIMARY KEY,record TEXT NOT NULL);
'''


def encode(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(raw):return hashlib.sha256(raw).hexdigest()


def model_hash():
    if any(path.read_bytes()!=raw for path,raw in zip(MODEL_PATHS,MODEL_BYTES)):
        raise ValueError('Pinned research source bytes changed')
    return digest(b'\x00'.join(MODEL_BYTES))


def preregistration():
    return {'blueprints':protocol.BLUEPRINTS,'parameters':protocol.PARAMETERS,'rules':protocol.RULES,
        'model_sha256':model_hash(),'blueprint_count':len(protocol.BLUEPRINTS),
        'variants_are':'MARKET_VARIANTS_NOT_NEW_ECONOMIC_HYPOTHESES',
        'unverified':['fills_and_market_fee_multiplier','intrahour_liquidity_and_drawdown',
            'walk_forward_serial_dependence_and_portfolio_correlation','forward_paper',
            'signed_execution_and_recovery','owner_capital_and_risk_mandate'],
        'paper_eligible':False,'live_eligible':False}


def connect(root):
    root=history.safe(root);root.mkdir(mode=0o700,parents=True,exist_ok=True)
    path=history.safe(root/'cycle.sqlite3');exists=path.exists()
    if exists and path.stat().st_size>MAX_REGISTRY:raise ValueError('Research storage limit requires review')
    con=sqlite3.connect(path,timeout=3)
    try:
        con.execute('PRAGMA foreign_keys=ON');con.execute('PRAGMA synchronous=FULL')
        if not exists:
            if con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():raise ValueError('Unexpected registry')
            con.executescript(SCHEMA);con.execute('INSERT INTO settings VALUES(?,?)',('schema','1'));con.commit()
        if con.execute("SELECT value FROM settings WHERE key='schema'").fetchone()!=('1',):raise ValueError('Unsupported research schema')
        return con
    except BaseException:con.close();raise


def validate_bars(bars,start,end):
    if len(bars)!=protocol.RULES['history_days']*24:raise ValueError('Incomplete hourly history')
    for i,row in enumerate(bars):
        t=history.integer(row['time'])
        if t!=start+i*protocol.HOUR or row['close_time']!=t+protocol.HOUR-1 or row['close_time']>=end:
            raise ValueError('Gap, duplicate or unclosed hourly row')
        for key in ('open','high','low','close'):history.number(row[key],positive=True)
        history.number(row['volume'],nonnegative=True);history.integer(row['trades'])
        if Decimal(row['low'])>min(Decimal(row['open']),Decimal(row['close'])) or Decimal(row['high'])<max(Decimal(row['open']),Decimal(row['close'])):
            raise ValueError('Invalid research OHLC envelope')
    return bars


def freeze_inputs(data_root,account_root,now):
    data=history.safe(Path(data_root)/'history.sqlite3')
    if not data.is_file():raise FileNotFoundError('Exact venue archive required')
    fee_source=account.summary(account_root,now)
    if fee_source['status']!='ACCOUNT_OBSERVED_READ_ONLY' or fee_source['association_revalidated'] is not True:
        raise ValueError('Fresh actual account fee observation required')
    fee=max(Decimal(protocol.RULES['spot_fee_floor']),Decimal(fee_source['fees']['userSpotCrossRate'])*3)
    with closing(sqlite3.connect(data.as_uri()+'?mode=ro',uri=True)) as con:
        con.execute('BEGIN')
        if con.execute("SELECT value FROM settings WHERE key='schema'").fetchone()!=('1',):raise ValueError('Unknown venue archive schema')
        latest=con.execute('SELECT record FROM runs ORDER BY started_ms DESC,id DESC LIMIT 1').fetchone()
        if latest is None:raise ValueError('No complete source capture')
        capture=json.loads(latest[0])
        if capture['status']!='COLLECTED' or not 0<=now-capture['started_ms']<=10*60000:
            raise ValueError('Current capture incomplete or stale')
        end=capture['started_ms']//protocol.HOUR*protocol.HOUR
        start=end-protocol.RULES['history_days']*protocol.DAY
        meta=json.loads(con.execute('SELECT metadata FROM market_versions WHERE sha256=?',(capture['metadata_sha256'],)).fetchone()[0])
        if digest(encode(meta))!=capture['metadata_sha256']:raise ValueError('Venue metadata changed')
        markets=history.markets(json.loads(history.raw_artifact(con,meta['perp_response'])),json.loads(history.raw_artifact(con,meta['spot_response'])))
        if markets!=meta['markets']:raise ValueError('Venue identity reconstruction differs')
        sets={};provenance={};verified={}
        for market in markets:
            rows=con.execute('SELECT body,artifact FROM candles WHERE market=? AND interval=? AND time_ms>=? AND time_ms<? ORDER BY time_ms',
                (market['id'],'1h',start,end)).fetchall()
            bars=[];references=set()
            for body,artifact in rows:
                key=(market['id'],artifact)
                if key not in verified:
                    bounds=con.execute("SELECT start_ms,end_ms FROM captures WHERE market=? AND kind='1h' AND artifact=? ORDER BY rowid LIMIT 1",
                        (market['id'],artifact)).fetchone()
                    if bounds is None:raise ValueError('Missing source request provenance')
                    raw=json.loads(history.raw_artifact(con,artifact))
                    normalized=history.candles(raw,market,'1h',bounds[0],bounds[1])
                    verified[key]={r['time']:encode(r).decode() for r in normalized}
                row=json.loads(body)
                if verified[key].get(row['time'])!=body:raise ValueError('Retained source differs from research input')
                bars.append(row);references.add(artifact)
            sets[market['id']]=validate_bars(bars,start,end);provenance[market['id']]=sorted(references)
    return {'schema':1,'venue':'hyperliquid','interval':'1h','start_ms':start,'end_ms':end,
        'markets':markets,'series':sets,'source_artifacts':provenance,'source_capture':capture['run_id'],
        'metadata_sha256':capture['metadata_sha256'],'spot_fee':str(fee),
        'fee_observation_ms':fee_source['observed_ms'],'base_account_fee':fee_source['fees']['userSpotCrossRate'],
        'cost_scope':'CONSERVATIVE_RESEARCH_ASSUMPTION_MARKET_MULTIPLIER_AND_FILLS_UNCALIBRATED',
        'preregistration':preregistration(),'qualified':False}


def save_epoch(con,epoch):
    if con.execute('SELECT 1 FROM epoch').fetchone():raise ValueError('Existing frozen epoch cannot be replaced')
    raw=encode(epoch)
    if len(raw)>MAX_EPOCH:raise ValueError('Research input exceeds bound')
    with con:con.execute('INSERT INTO epoch VALUES(?,?,?)',(1,digest(raw),zlib.compress(raw)))


def load_epoch(con):
    item=con.execute('SELECT sha256,raw FROM epoch WHERE id=1').fetchone()
    if item is None:raise ValueError('Frozen research epoch missing')
    if len(item[1])>MAX_EPOCH:raise ValueError('Research archive exceeds bound')
    decoder=zlib.decompressobj()
    try:raw=decoder.decompress(item[1],MAX_EPOCH+1)
    except zlib.error:raise ValueError('Corrupt frozen research bytes') from None
    if len(raw)>MAX_EPOCH or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail or digest(raw)!=item[0]:
        raise ValueError('Frozen research epoch changed')
    epoch=json.loads(raw)
    if epoch['schema']!=1 or encode(epoch['preregistration'])!=encode(preregistration()):
        raise ValueError('Reviewed research protocol/code changed; separate epoch required')
    if len(epoch['markets'])!=6 or {m['id'] for m in epoch['markets']}!=set(epoch['series']):raise ValueError('Frozen market identity changed')
    for bars in epoch['series'].values():validate_bars(bars,epoch['start_ms'],epoch['end_ms'])
    return epoch,item[0]


def check_idle(con):
    if con.execute("SELECT 1 FROM attempts WHERE status='STARTED' LIMIT 1").fetchone():
        raise ValueError('Interrupted research attempt needs explicit reconciliation')


def register(con,epoch,now):
    day=datetime.fromtimestamp(now/1000,timezone.utc).date().isoformat()
    with con:
        con.execute('BEGIN IMMEDIATE');check_idle(con)
        used=con.execute('SELECT count(*) FROM families WHERE day=?',(day,)).fetchone()[0]
        if used>=protocol.RULES['new_blueprints_per_utc_day']:return {'status':'DAILY_QUOTA_ALREADY_REGISTERED','new_blueprints':0}
        created=[]
        for blueprint in protocol.BLUEPRINTS:
            if con.execute('SELECT 1 FROM families WHERE id=?',(blueprint['id'],)).fetchone():continue
            record={'blueprint':blueprint,'registered_ms':now,'parameters':protocol.PARAMETERS,
                'protocol':epoch['preregistration'],'holdout_single_use':True,'qualified':False}
            con.execute('INSERT INTO families VALUES(?,?,?)',(blueprint['id'],day,encode(record).decode()))
            for market in epoch['markets']:
                identity=blueprint['id']+'/'+market['id']
                con.execute('INSERT INTO variants VALUES(?,?,?)',(identity,blueprint['id'],market['id']))
            created.append(blueprint['id']);used+=1
            if used>=protocol.RULES['new_blueprints_per_utc_day']:break
        return {'status':'REGISTERED' if created else 'NEED_NEW_BLUEPRINTS_OR_PROVIDER_BUDGET',
            'new_blueprints':len(created),'ids':created,'new_market_variants':len(created)*6}


def persist(con,variant,record,status):
    if record['model_sha256']!=model_hash():raise ValueError('Research model changed during evaluation')
    record['status']=status
    with con:con.execute('UPDATE attempts SET status=?,record=? WHERE variant=?',(status,encode(record).decode(),variant))


def evaluate_next(con,epoch,epoch_hash,now):
    day=datetime.fromtimestamp(now/1000,timezone.utc).date().isoformat()
    with con:
        con.execute('BEGIN IMMEDIATE');check_idle(con)
        count=con.execute('SELECT count(*) FROM attempts WHERE day=?',(day,)).fetchone()[0]
        if count>=protocol.RULES['primary_test_bundles_per_utc_day']:return None
        row=con.execute('SELECT v.id,v.family,v.market FROM variants v LEFT JOIN attempts a ON a.variant=v.id '
            'JOIN families f ON f.id=v.family WHERE a.variant IS NULL ORDER BY f.day,v.family,v.market LIMIT 1').fetchone()
        if row is None:return None
        variant,family,market=row
        result={'variant':variant,'family':family,'market':market,'started_ms':now,'epoch_sha256':epoch_hash,
            'model_sha256':model_hash(),'phases':{},'holdout_consumed':False,
            'historical_qualified':False,'paper_eligible':False,'live_eligible':False}
        con.execute('INSERT INTO attempts VALUES(?,?,?,?,?)',(variant,day,'STARTED',0,encode(result).decode()))
    try:
        if market.startswith('perpetual:'):
            result['missing_evidence']=['historical_mark_index_basis','margin_and_liquidation_model',
                'explicit_perpetual_leverage_limit','calibrated_fills']
            persist(con,variant,result,'BLOCKED_PRODUCT_MODEL');return result
        bars=epoch['series'][market];a,b,c,d=protocol.partitions(bars);fee=epoch['spot_fee']
        train=protocol.segment(bars,a,b,family,fee);result['phases']['train']=train
        if train['failures']:persist(con,variant,result,'SCREEN_REJECTED_TRAINING');return result
        validation=protocol.segment(bars,b,c,family,fee);result['phases']['validation']=validation
        if validation['failures']:persist(con,variant,result,'SCREEN_REJECTED_VALIDATION');return result
        # Consumption is durable before evaluation. A crash cannot grant a new
        # final-holdout attempt or a fresh repair budget by changing the day.
        result['holdout_consumed']=True
        with con:
            con.execute('UPDATE attempts SET holdout_consumed=1,record=? WHERE variant=?',
                (encode(result).decode(),variant))
        holdout=protocol.segment(bars,c,d,family,fee,holdout=True);result['phases']['holdout']=holdout
        result['missing_evidence']=list(epoch['preregistration']['unverified'])
        state='EXPLORATORY_POSITIVE_UNQUALIFIED' if holdout['exploratory_positive'] else 'SCREEN_REJECTED_HOLDOUT'
        if result['model_sha256']!=model_hash():raise ValueError('Research code changed during evaluation')
        persist(con,variant,result,state);return result
    except (ValueError,KeyError,TypeError,OSError,ArithmeticError) as exc:
        result['error_type']=type(exc).__name__;persist(con,variant,result,'BLOCKED_EVALUATION');return result


def report(con):
    epoch,epoch_hash=load_epoch(con)
    records=[json.loads(r[0])|{'status':r[1],'holdout_consumed':bool(r[2])} for r in
        con.execute('SELECT record,status,holdout_consumed FROM attempts ORDER BY day,variant')]
    families=[{'id':r[0],'day':r[1]} for r in con.execute('SELECT id,day FROM families ORDER BY day,id')]
    stages=[]
    for record in records:
        stages.append({k:record.get(k) for k in ('variant','family','market','status','holdout_consumed','error_type','missing_evidence')}|
            {'net_by_phase':{phase:values['normal']['net'] for phase,values in record['phases'].items()},
             'failures_by_phase':{phase:values['failures'] for phase,values in record['phases'].items()},
             'paper_eligible':False,'live_eligible':False})
    comparison=con.execute('SELECT record FROM comparisons ORDER BY week DESC LIMIT 1').fetchone()
    return {'status':'RESEARCH_OBSERVED','generator':'FINITE_OFFLINE_BLUEPRINT_LIBRARY_NO_PAID_MODEL',
        'epoch_sha256':epoch_hash,'evaluation_window':[epoch['start_ms'],epoch['end_ms']],
        'new_blueprints_per_utc_day':2,'max_variants_per_blueprint':6,'max_primary_test_bundles_per_utc_day':12,
        'blueprints_registered':len(families),'blueprints_remaining':len(protocol.BLUEPRINTS)-len(families),
        'registered_market_variants':con.execute('SELECT count(*) FROM variants').fetchone()[0],
        'completed_screens':sum(r['status'].startswith('SCREEN_') or r['status']=='EXPLORATORY_POSITIVE_UNQUALIFIED' for r in records),
        'blocked_product_models':sum(r['status']=='BLOCKED_PRODUCT_MODEL' for r in records),
        'interrupted':sum(r['status']=='STARTED' for r in records),'families':families,'stages':stages,
        'exhaustion_policy':'NEED_NEW_BLUEPRINTS_OR_PROVIDER_BUDGET',
        'historical_qualified':0,'paper_qualified':0,'live_eligible':0,
        'latest_comparison':json.loads(comparison[0]) if comparison else None}


def compare(con,now):
    week=datetime.fromtimestamp(now/1000,timezone.utc).strftime('%G-W%V')
    if con.execute('SELECT 1 FROM comparisons WHERE week=?',(week,)).fetchone():return {'status':'COMPARISON_ALREADY_RECORDED','week':week}
    data=report(con);groups={}
    for row in data['stages']:
        # All candidates share one immutable input/cost/risk epoch. Separate
        # instruments/products never compete as an allegedly identical cohort.
        key=row['market'];groups.setdefault(key,[])
        if row['status']=='EXPLORATORY_POSITIVE_UNQUALIFIED':groups[key].append(row)
    ranked={key:sorted(rows,key=lambda r:(-Decimal(r['net_by_phase']['holdout']),r['variant'])) for key,rows in sorted(groups.items())}
    result={'status':'COMPARISON_RECORDED','week':week,'observed_ms':now,'epoch_sha256':data['epoch_sha256'],
        'cohort_keys':['hyperliquid','product/instrument','frozen_window','frozen_costs','0.25_hypothetical_allocation'],
        'exploratory_rankings':{key:[r['variant'] for r in rows] for key,rows in ranked.items()},
        'promotion':'KEEP_CASH_NO_QUALIFIED_CANDIDATE','paper_eligible':False,'live_eligible':False}
    with con:con.execute('INSERT INTO comparisons VALUES(?,?)',(week,encode(result).decode()))
    return result


def reproduce(con,variant):
    epoch,epoch_hash=load_epoch(con)
    row=con.execute('SELECT status,record,holdout_consumed FROM attempts WHERE variant=?',(variant,)).fetchone()
    if row is None or row[0] in ('STARTED','BLOCKED_EVALUATION'):raise ValueError('No completed reproducible attempt')
    result=json.loads(row[1])
    if result['epoch_sha256']!=epoch_hash or result['model_sha256']!=model_hash():raise ValueError('Attempt provenance mismatch')
    if not result['phases']:
        if row[0]!='BLOCKED_PRODUCT_MODEL':raise ValueError('Missing completed research metrics')
        return {'status':'BLOCKED_EVIDENCE_CONFIRMED','variant':variant,'qualified':False}
    bars=epoch['series'][result['market']];a,b,c,d=protocol.partitions(bars)
    windows={'train':(a,b),'validation':(b,c),'holdout':(c,d)}
    for phase,record in result['phases'].items():
        if phase=='holdout' and not row[2]:raise ValueError('Unrecorded holdout consumption')
        computed=protocol.segment(bars,*windows[phase],result['family'],epoch['spot_fee'],holdout=phase=='holdout')
        if encode(computed)!=encode(record):raise ValueError('Reproduced research metrics differ')
    return {'status':'REPRODUCED','variant':variant,'phases':sorted(result['phases']),'qualified':False}


def status(root):
    path=history.safe(Path(root)/'cycle.sqlite3')
    if not path.is_file():return {'status':'NOT_STARTED','historical_qualified':0,'paper_qualified':0,'live_eligible':0}
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as con:
        if con.execute("SELECT value FROM settings WHERE key='schema'").fetchone()!=('1',):raise ValueError('Unsupported research schema')
        return report(con)


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('action',choices=('cycle','compare','status','reproduce'))
    ap.add_argument('--state-dir',type=Path,default=Path('/var/lib/sniper/hyperliquid-research'))
    ap.add_argument('--data-dir',type=Path,default=Path('/var/lib/sniper/hyperliquid-data'))
    ap.add_argument('--account-dir',type=Path,default=Path('/var/lib/sniper/hyperliquid-account'))
    ap.add_argument('--variant');args=ap.parse_args();os.umask(0o077);now=int(time.time()*1000)
    if args.action=='status':result=status(args.state_dir)
    elif args.action=='reproduce':
        if not args.variant:ap.error('--variant required')
        path=history.safe(args.state_dir/'cycle.sqlite3')
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as con:result=reproduce(con,args.variant)
    else:
        import fcntl
        root=history.safe(args.state_dir);root.mkdir(mode=0o700,parents=True,exist_ok=True)
        with history.safe(root/'cycle.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with closing(connect(root)) as con:
                if not con.execute('SELECT 1 FROM epoch').fetchone():
                    if args.action!='cycle':raise ValueError('Research epoch not initialized')
                    save_epoch(con,freeze_inputs(args.data_dir,args.account_dir,now))
                epoch,epoch_hash=load_epoch(con)
                if epoch['end_ms']>now:raise ValueError('Future frozen window')
                if args.action=='compare':result=compare(con,now)
                else:
                    proposal=register(con,epoch,now);evaluated=0
                    while evaluate_next(con,epoch,epoch_hash,now) is not None:evaluated+=1
                    result=report(con)|{'registration':proposal,'primary_test_bundles_this_invocation':evaluated}
    print(json.dumps(result,indent=2,allow_nan=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
