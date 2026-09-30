#!/usr/bin/env python3
"""Bounded public Hyperliquid spot/perpetual research archive, not an order client.

Minute/hour bars have separate identities. Closed rows and venue funding events
are append-only; discrepancies stop capture. Public rates are not account PnL.
The REST 5000-bar bound cannot establish annual minute coverage for T1.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
import zlib

ENDPOINT='https://api.hyperliquid.xyz/info'
INTERVALS={'1m':60000,'1h':3600000}
LIMIT=4*1024*1024
HOUR=3600000
DAY=24*HOUR
MAX_WEIGHT=500
PERPS=('BTC','ETH','SOL','HYPE')
SPOTS=('UBTC','HYPE')


class BudgetUnavailable(RuntimeError):
    """A bounded reservation could not be obtained; never an HTTP success."""


def encode(value): return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(raw): return hashlib.sha256(raw).hexdigest()


def safe(path):
    path=Path(path).absolute()
    if any(p.is_symlink() for p in (path,*path.parents)):raise ValueError('Unsafe archive path')
    if path.is_file() and path.stat().st_nlink!=1:raise ValueError('Hardlinked archive')
    return path


def number(value,positive=False,nonnegative=False):
    if isinstance(value,bool) or not isinstance(value,(str,int)) or len(str(value))>64:
        raise ValueError('Invalid numeric input')
    try: n=Decimal(value)
    except InvalidOperation:raise ValueError('Invalid numeric input') from None
    if not n.is_finite() or abs(n)>Decimal('1e15') or n.as_tuple().exponent < -30:
        raise ValueError('Nonfinite or unbounded numeric input')
    if positive and n<=0 or nonnegative and n<0:raise ValueError('Invalid numeric sign')
    text=format(n,'f')
    if '.' in text:text=text.rstrip('0').rstrip('.')
    return '0' if n==0 else text


def integer(value,minimum=0,maximum=2**63-1):
    if type(value) is not int or not minimum<=value<=maximum:raise ValueError('Invalid integer')
    return value


def markets(meta,spot):
    if not isinstance(meta['universe'],list) or len(meta['universe'])>10000:
        raise ValueError('Invalid perpetual metadata')
    result=[]
    for name in PERPS:
        rows=[(i,r) for i,r in enumerate(meta['universe']) if r['name']==name]
        if len(rows)!=1:raise ValueError('Missing or ambiguous perpetual identity')
        i,row=rows[0]
        if row.get('isDelisted',False):raise ValueError('Selected market delisted')
        result.append({'id':'perpetual:'+name,'product':'perpetual','coin':name,'symbol':name+'-PERP',
            'asset_id':i,'size_decimals':integer(row['szDecimals'],0,6),
            'max_leverage':integer(row['maxLeverage'],1,1000),'quote':'USDC'})
    tokens={}
    if not isinstance(spot['tokens'],list) or len(spot['tokens'])>10000:raise ValueError('Invalid spot metadata')
    for row in spot['tokens']:
        i=integer(row['index'],0,100000)
        if i in tokens:raise ValueError('Duplicate token index')
        tokens[i]=row
    for name in SPOTS:
        candidates=[]
        for pair in spot['universe']:
            if len(pair['tokens'])!=2:raise ValueError('Invalid pair')
            base,quote=[tokens[i] for i in pair['tokens']]
            if base['name']==name and quote['name']=='USDC':candidates.append((pair,base,quote))
        if len(candidates)!=1:raise ValueError('Missing or ambiguous spot identity')
        pair,base,quote=candidates[0];index=integer(pair['index'],0,100000)
        coin='PURR/USDC' if base['name']=='PURR' else '@'+str(index)
        result.append({'id':'spot:'+name+'/USDC','product':'spot','coin':coin,'symbol':name+'/USDC',
            'asset_id':10000+index,'size_decimals':integer(base['szDecimals'],0,8),'max_leverage':1,
            'quote':'USDC','base_token_id':base['tokenId'],'quote_token_id':quote['tokenId']})
    if len({r['coin'] for r in result})!=len(result):raise ValueError('Conflicting market coins')
    return result


def candles(data,market,interval,start,end):
    if not isinstance(data,list) or len(data)>5000:raise ValueError('Invalid candle response')
    step=INTERVALS[interval];previous=None;rows=[]
    for bar in data:
        if bar['s']!=market['coin'] or bar['i']!=interval:raise ValueError('Wrong candle identity')
        t=integer(bar['t']);close=integer(bar['T'])
        if t%step or close!=t+step-1:raise ValueError('Invalid candle timing')
        if not start<=t<end or close>=end:raise ValueError('Candle outside closed request')
        if previous is not None and t<=previous:raise ValueError('Duplicate or unordered candle')
        previous=t
        row={'time':t,'close_time':close,'open':number(bar['o'],positive=True),
            'high':number(bar['h'],positive=True),'low':number(bar['l'],positive=True),
            'close':number(bar['c'],positive=True),'volume':number(bar['v'],nonnegative=True),
            'trades':integer(bar['n'])}
        if Decimal(row['low'])>min(Decimal(row['open']),Decimal(row['close'])) or \
                Decimal(row['high'])<max(Decimal(row['open']),Decimal(row['close'])):
            raise ValueError('Invalid OHLC envelope')
        rows.append(row)
    return rows


def funding(data,market,start,end):
    if not isinstance(data,list) or len(data)>500:raise ValueError('Invalid funding response')
    rows=[];previous=None
    for item in data:
        t=integer(item['time'])
        if item['coin']!=market['coin'] or not start<=t<end:raise ValueError('Wrong funding identity/time')
        if previous is not None and t<=previous:raise ValueError('Duplicate or unordered funding event')
        previous=t
        rate=number(item['fundingRate']);premium=number(item['premium'])
        if abs(Decimal(rate))>1 or abs(Decimal(premium))>1:raise ValueError('Unbounded funding value')
        rows.append({'time':t,'rate':rate,'premium':premium,
            'kind':'VENUE_RATE_NOT_ACCOUNT_SETTLEMENT'})
    return rows


SCHEMA='''CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE artifacts(sha256 TEXT PRIMARY KEY,raw BLOB NOT NULL);
CREATE TABLE runs(id TEXT PRIMARY KEY,started_ms INTEGER NOT NULL,status TEXT NOT NULL,record TEXT NOT NULL);
CREATE TABLE market_versions(sha256 TEXT PRIMARY KEY,metadata TEXT NOT NULL);
CREATE TABLE candles(market TEXT NOT NULL,interval TEXT NOT NULL,time_ms INTEGER NOT NULL,
 body TEXT NOT NULL,artifact TEXT NOT NULL REFERENCES artifacts(sha256),PRIMARY KEY(market,interval,time_ms));
CREATE TABLE funding(market TEXT NOT NULL,time_ms INTEGER NOT NULL,body TEXT NOT NULL,
 artifact TEXT NOT NULL REFERENCES artifacts(sha256),PRIMARY KEY(market,time_ms));
CREATE TABLE requests(time_ms INTEGER NOT NULL,weight INTEGER NOT NULL);
CREATE TABLE captures(run_id TEXT NOT NULL,market TEXT NOT NULL,kind TEXT NOT NULL,start_ms INTEGER NOT NULL,
 end_ms INTEGER NOT NULL,artifact TEXT NOT NULL REFERENCES artifacts(sha256),rows INTEGER NOT NULL);
'''


def connect(root):
    root=safe(root);root.mkdir(mode=0o700,parents=True,exist_ok=True)
    path=safe(root/'history.sqlite3');exists=path.exists()
    if exists and path.stat().st_size>2*1024**3:raise ValueError('Archive storage limit requires reviewed retention')
    con=sqlite3.connect(path,timeout=2)
    try:
        con.execute('PRAGMA synchronous=FULL');con.execute('PRAGMA foreign_keys=ON')
        if not exists:
            if con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                raise ValueError('Unexpected archive state')
            con.executescript(SCHEMA);con.execute('INSERT INTO settings VALUES(?,?)',('schema','1'));con.commit()
        if con.execute("SELECT value FROM settings WHERE key='schema'").fetchone()!=('1',):
            raise ValueError('Unsupported archive schema')
        return con
    except BaseException:con.close();raise


def retain(con,raw):
    if not isinstance(raw,bytes) or len(raw)>LIMIT:raise ValueError('Response exceeds bound')
    sha=digest(raw);packed=zlib.compress(raw)
    old=con.execute('SELECT raw FROM artifacts WHERE sha256=?',(sha,)).fetchone()
    if old and old[0]!=packed:raise ValueError('Artifact digest collision')
    con.execute('INSERT OR IGNORE INTO artifacts VALUES(?,?)',(sha,packed));con.commit();return sha


def append(con,table,market,rows,artifact,interval=None):
    if table not in ('candles','funding'):raise ValueError('Unsupported archive table')
    with con:
        for row in rows:
            body=encode(row).decode()
            keys=(market,interval,row['time']) if table=='candles' else (market,row['time'])
            condition='market=? AND interval=? AND time_ms=?' if table=='candles' else 'market=? AND time_ms=?'
            old=con.execute('SELECT body FROM '+table+' WHERE '+condition,keys).fetchone()
            if old:
                if old[0]!=body:raise ValueError('Previously closed source row changed')
                continue
            values=keys+(body,artifact)
            con.execute('INSERT INTO '+table+' VALUES('+','.join('?' for _ in values)+')',values)


def reserve(con,weight,clock=lambda:int(time.time()*1000),sleep=time.sleep):
    integer(weight,1,MAX_WEIGHT)
    for attempt in range(3):
        now=clock()
        # Reserves survive a crash/restart. This covers this archive, not every
        # unrelated client on the server/IP. HTTP 429 remains an explicit failure.
        with con:
            con.execute('BEGIN IMMEDIATE')
            rows=con.execute('SELECT time_ms,weight FROM requests WHERE time_ms>? ORDER BY time_ms',(now-60000,)).fetchall()
            if rows and rows[-1][0]>now+1000:raise ValueError('Request clock moved backwards')
            if sum(r[1] for r in rows)+weight<=MAX_WEIGHT:
                con.execute('INSERT INTO requests VALUES(?,?)',(now,weight));return
            deficit=sum(r[1] for r in rows)+weight-MAX_WEIGHT
            released=0;expiry=None
            for stamp,reserved in rows:
                released+=reserved
                if released>=deficit:
                    expiry=stamp+60001;break
            if expiry is None:raise ValueError('Invalid persisted request budget')
        if attempt==2:break
        sleep(min(60,max(.001,(expiry-now)/1000)))
    raise BudgetUnavailable('Request budget unavailable')


def post(payload):
    if payload.get('type') not in ('meta','spotMeta','candleSnapshot','fundingHistory'):
        raise ValueError('Unsupported public request')
    request=urllib.request.Request(ENDPOINT,data=encode(payload),headers={'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(request,timeout=20) as response:raw=response.read(LIMIT+1)
    if len(raw)>LIMIT:raise ValueError('Response exceeds bound')
    return raw


def capture(root,now,fetch=post,budget=reserve):
    integer(now,DAY,2**63-1);root=safe(root)
    with closing(connect(root)) as con:
        run_id=uuid.uuid4().hex
        result={'schema':1,'run_id':run_id,'started_ms':now,'status':'STARTED','qualified':False,
            'model_sha256':digest(Path(__file__).read_bytes()),'requests':0,'errors':[],'captures':[]}
        con.execute('INSERT INTO runs VALUES(?,?,?,?)',(run_id,now,'STARTED',encode(result).decode()));con.commit()
        def get(payload,weight):
            if result['requests']>=60:raise ValueError('Capture request limit exceeded')
            budget(con,weight);result['requests']+=1
            raw=fetch(payload);sha=retain(con,raw)
            return json.loads(raw),sha
        try:
            meta,a=get({'type':'meta'},20);spot,b=get({'type':'spotMeta'},20)
            selected=markets(meta,spot);metadata={'markets':selected,'perp_response':a,'spot_response':b}
            for market in selected:
                fields=('product','coin','base_token_id','quote_token_id') if market['product']=='spot' else ('product','coin')
                identity=encode({k:market[k] for k in fields}).decode();key='instrument:'+market['id']
                prior=con.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
                if prior and prior!=(identity,):raise ValueError('Instrument changed; separate data epoch required')
                con.execute('INSERT OR IGNORE INTO settings VALUES(?,?)',(key,identity))
            version=digest(encode(metadata))
            con.execute('INSERT OR IGNORE INTO market_versions VALUES(?,?)',(version,encode(metadata).decode()));con.commit()
            result['metadata_sha256']=version
            for market in selected:
                for interval,step in INTERVALS.items():
                    end=now//step*step
                    last=con.execute('SELECT max(time_ms) FROM candles WHERE market=? AND interval=?',(market['id'],interval)).fetchone()[0]
                    if last is not None and last>=end-step:continue
                    count=4900 if interval=='1m' else 120*24
                    start=max(end-count*step,last-2*step if last is not None else 0)
                    payload={'type':'candleSnapshot','req':{'coin':market['coin'],'interval':interval,'startTime':start,'endTime':end-1}}
                    data,sha=get(payload,104)
                    rows=candles(data,market,interval,start,end)
                    if not rows:raise ValueError('Empty candle response')
                    append(con,'candles',market['id'],rows,sha,interval)
                    con.execute('INSERT INTO captures VALUES(?,?,?,?,?,?,?)',(run_id,market['id'],interval,start,end,sha,len(rows)));con.commit()
                    result['captures'].append({'market':market['id'],'kind':interval,'rows':len(rows),
                        'requested_rows':(end-start)//step,'missing_requested':(end-start)//step-len(rows)})
                if market['product']=='perpetual':
                    end=now//60000*60000
                    last=con.execute('SELECT max(time_ms) FROM funding WHERE market=?',(market['id'],)).fetchone()[0]
                    if last is not None and last>=now//HOUR*HOUR:continue
                    start=max(end-120*DAY,last-HOUR if last is not None else 0)
                    for _ in range(7):
                        data,sha=get({'type':'fundingHistory','coin':market['coin'],'startTime':start,'endTime':end-1},45)
                        rows=funding(data,market,start,end)
                        if not rows:break
                        append(con,'funding',market['id'],rows,sha)
                        con.execute('INSERT INTO captures VALUES(?,?,?,?,?,?,?)',(run_id,market['id'],'funding',start,end,sha,len(rows)));con.commit()
                        result['captures'].append({'market':market['id'],'kind':'funding','rows':len(rows)})
                        start=rows[-1]['time']+1
                        if len(rows)<500:break
                    else:raise ValueError('Funding pagination exceeded bound')
            result['status']='COLLECTED'
        except (OSError,ValueError,KeyError,TypeError,RuntimeError,sqlite3.Error) as exc:
            con.rollback();result['status']='BLOCKED'
            result['errors'].append({'error_type':type(exc).__name__,
                'category':'REQUEST_BUDGET_UNAVAILABLE' if isinstance(exc,BudgetUnavailable) else 'CAPTURE_FAILURE',
                'http_status':exc.code if isinstance(exc,urllib.error.HTTPError) else None})
        result['finished_ms']=int(time.time()*1000)
        con.execute('UPDATE runs SET status=?,record=? WHERE id=?',(result['status'],encode(result).decode(),run_id));con.commit()
        return result


def status(root):
    path=safe(Path(root)/'history.sqlite3')
    if not path.is_file():return {'status':'NOT_STARTED','qualified':False,'series':[]}
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as con:
        if con.execute("SELECT value FROM settings WHERE key='schema'").fetchone()!=('1',):
            raise ValueError('Unsupported archive schema')
        series=[]
        for market,interval,count,first,last in con.execute('SELECT market,interval,count(*),min(time_ms),max(time_ms) FROM candles GROUP BY market,interval ORDER BY market,interval'):
            step=INTERVALS[interval];series.append({'market':market,'interval':interval,'rows':count,
                'first_ms':first,'last_ms':last,'missing_internal':(last-first)//step+1-count})
        events=[{'market':m,'events':n,'first_ms':a,'last_ms':b} for m,n,a,b in con.execute('SELECT market,count(*),min(time_ms),max(time_ms) FROM funding GROUP BY market ORDER BY market')]
        latest=con.execute('SELECT record FROM runs ORDER BY started_ms DESC,id DESC LIMIT 1').fetchone()
        interrupted=con.execute("SELECT count(*) FROM runs WHERE status='STARTED'").fetchone()[0]
    return {'status':'OBSERVED','qualified':False,'series':series,'funding':events,
        'latest_run':json.loads(latest[0]) if latest else None,'interrupted_runs':interrupted,
        'funding_scope':'VENUE_RATE_NOT_ACCOUNT_SETTLEMENT','minute_api_limit':5000}


def summary(root):
    """Only market coverage/counts; no raw bars, response bodies or account data."""
    data=status(root)
    if data['status']=='NOT_STARTED':return data
    latest=data['latest_run']
    return {k:data[k] for k in ('status','qualified','series','funding','interrupted_runs','minute_api_limit','funding_scope')} | {
        'latest_capture_status':latest['status'] if latest else 'NOT_STARTED',
        'latest_started_ms':latest['started_ms'] if latest else None}


def raw_artifact(con,sha):
    item=con.execute('SELECT raw FROM artifacts WHERE sha256=?',(sha,)).fetchone()
    if item is None or len(item[0])>LIMIT+1024:raise ValueError('Missing or oversized retained response')
    decoder=zlib.decompressobj();raw=decoder.decompress(item[0],LIMIT+1)
    if len(raw)>LIMIT or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail or digest(raw)!=sha:
        raise ValueError('Retained response changed')
    return raw


def reproduce(root,run_id):
    path=safe(Path(root)/'history.sqlite3')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as con:
        if con.execute("SELECT value FROM settings WHERE key='schema'").fetchone()!=('1',):
            raise ValueError('Unsupported archive schema')
        row=con.execute('SELECT record FROM runs WHERE id=?',(run_id,)).fetchone()
        if row is None:raise ValueError('Unknown capture')
        record=json.loads(row[0])
        if record['status']!='COLLECTED':raise ValueError('Capture not complete')
        if record['model_sha256']!=digest(Path(__file__).read_bytes()):
            raise ValueError('Use original reviewed capture code revision')
        metadata=json.loads(con.execute('SELECT metadata FROM market_versions WHERE sha256=?',(record['metadata_sha256'],)).fetchone()[0])
        if digest(encode(metadata))!=record['metadata_sha256']:raise ValueError('Metadata fingerprint mismatch')
        selected=markets(json.loads(raw_artifact(con,metadata['perp_response'])),json.loads(raw_artifact(con,metadata['spot_response'])))
        if metadata['markets']!=selected:raise ValueError('Metadata reconstruction differs')
        selected={m['id']:m for m in selected};count=0
        for market,kind,start,end,sha,n in con.execute('SELECT market,kind,start_ms,end_ms,artifact,rows FROM captures WHERE run_id=? ORDER BY rowid',(run_id,)):
            data=json.loads(raw_artifact(con,sha))
            rows=funding(data,selected[market],start,end) if kind=='funding' else candles(data,selected[market],kind,start,end)
            if len(rows)!=n:raise ValueError('Capture count differs')
            for item in rows:
                if kind=='funding':
                    stored=con.execute('SELECT body FROM funding WHERE market=? AND time_ms=?',(market,item['time'])).fetchone()
                else:
                    stored=con.execute('SELECT body FROM candles WHERE market=? AND interval=? AND time_ms=?',(market,kind,item['time'])).fetchone()
                if stored!=(encode(item).decode(),):raise ValueError('Reconstructed row differs')
                count+=1
    return {'status':'REPRODUCED','run_id':run_id,'verified_rows':count,'qualified':False}


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('action',choices=['capture','status','reproduce'])
    ap.add_argument('--run-id')
    ap.add_argument('--state-dir',type=Path,default=Path('/var/lib/sniper/hyperliquid-data'));args=ap.parse_args()
    os.umask(0o077);root=safe(args.state_dir)
    if args.action=='status':result=status(root)
    elif args.action=='reproduce':
        if not args.run_id:ap.error('--run-id required')
        result=reproduce(root,args.run_id)
    else:
        import fcntl
        root.mkdir(mode=0o700,parents=True,exist_ok=True)
        with safe(root/'capture.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            result=capture(root,int(time.time()*1000))
    print(json.dumps(result,indent=2,allow_nan=False))
    return 2 if result['status']=='BLOCKED' else 0


if __name__=='__main__':raise SystemExit(main())
