#!/usr/bin/env python3
"""Public-data collector and automatic spot research/paper worker. No exchange writes."""
import argparse
from contextlib import closing
import json
import os
import re
from pathlib import Path
import signal
import sqlite3
import sys
import time
import urllib.request
import zlib

import execution_evidence as ex
import forward_pipeline as pipeline
import hyperliquid_history as history

STATE=Path('/var/lib/sniper/forward-research-v1')
CALIBRATION=Path('/var/lib/sniper/execution-calibration/mainnet.json')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): raise ValueError('Redirect forbidden')


def pairs(items):
    value={}
    for k,v in items:
        if k in value: raise ValueError('Duplicate JSON key')
        value[k]=v
    return value


def post(con,payload,weight):
    if payload.get('type') not in ('meta','spotMeta','l2Book'): raise ValueError('Unapproved public request')
    if weight!=(2 if payload['type']=='l2Book' else 20) or type(weight) is not int:
        raise ValueError('Incorrect request weight')
    if (set(payload)!=({'type','coin'} if payload['type']=='l2Book' else {'type'}) or
        (payload['type']=='l2Book' and not re.fullmatch('@[0-9]{1,6}',payload['coin']))):
        raise ValueError('Unsupported public request shape')
    now=int(time.time()*1000)
    with con:
        con.execute('BEGIN IMMEDIATE')
        if con.execute('SELECT 1 FROM requests WHERE time>? LIMIT 1',(now+1000,)).fetchone(): raise ValueError('Request clock moved backwards')
        total=con.execute('SELECT coalesce(sum(weight),0) FROM requests WHERE time>=?',(now-60000,)).fetchone()[0]
        if total+weight>100: raise RuntimeError('Public request budget exhausted')
        con.execute('INSERT INTO requests VALUES(?,?)',(now,weight))
        con.execute('DELETE FROM requests WHERE time<?',(now-120000,))
    request=urllib.request.Request(history.ENDPOINT,history.encode(payload),{'Content-Type':'application/json'},method='POST')
    sent=int(time.time()*1000)
    with urllib.request.build_opener(NoRedirect).open(request,timeout=10) as response:
        if response.status!=200 or response.geturl()!=history.ENDPOINT: raise ValueError('Unexpected public endpoint')
        raw=response.read(history.LIMIT+1)
    received=int(time.time()*1000)
    if len(raw)>history.LIMIT: raise ValueError('Oversized public response')
    def nonfinite(_): raise ValueError('Nonfinite response')
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=nonfinite),sent,received


def metadata(con,now):
    row=con.execute('SELECT id,created,body FROM metadata ORDER BY created DESC,id DESC LIMIT 1').fetchone()
    cached=None
    if row:
        cached=pipeline.unpack(row[2])
        if ex.sha(cached)!=row[0] or [m for m in history.markets(cached['perpetual_response'],cached['spot_response']) if m['product']=='spot']!=cached['markets']:
            raise ValueError('Cached metadata reconstruction differs')
    if row and 0<=now-row[1]<3600000: return cached['markets']
    perp,_,_=post(con,{'type':'meta'},20); spot,_,_=post(con,{'type':'spotMeta'},20)
    markets=[m for m in history.markets(perp,spot) if m['product']=='spot']
    if row and cached['markets']!=markets:
        raise ValueError('Selected instrument metadata changed; reviewed separate epoch required')
    value={'perpetual_response':perp,'spot_response':spot,'markets':markets,'observed_ms':now}
    with con: con.execute('INSERT INTO metadata VALUES(?,?,?)',(ex.sha(value),now,zlib.compress(history.encode(value))))
    return markets


def import_calibration(con,now,path=CALIBRATION):
    path=history.safe(path)
    if not path.exists(): return {'status':'BLOCKED','reason':'NO_ROOT_OWNED_REAL_ORDER_JOURNAL'}
    info=path.stat()
    if info.st_uid!=0 or info.st_mode&0o027 or info.st_size>4*1024*1024:
        raise ValueError('Untrusted calibration import')
    value=json.loads(path.read_bytes(),object_pairs_hook=pairs)
    if value.get('schema')!=1 or value.get('network')!='MAINNET' or set(value)!={'schema','network','samples'}:
        raise ValueError('Unsupported measured order journal')
    result=ex.calibration(value['samples'],now)
    # Imports are append-only, never a replaceable PASS certificate.
    with con:
        for sample in value['samples']:
            identity=ex.sha(sample)
            con.execute('INSERT OR IGNORE INTO calibrations VALUES(?,?)',(identity,zlib.compress(history.encode(sample))))
    return result


def stored_markets(con,now):
    row=con.execute('SELECT id,created,body FROM metadata ORDER BY created DESC,id DESC LIMIT 1').fetchone()
    if row is None or not 0<=now-row[1]<=3600000: raise ValueError('Fresh collector metadata required')
    value=pipeline.unpack(row[2])
    if ex.sha(value)!=row[0]: raise ValueError('Collector metadata fingerprint changed')
    return value['markets']


def research_work(con,history_root,old_research_root,now=None):
    now=int(time.time()*1000) if now is None else now
    markets=stored_markets(con,now)
    generation=pipeline.generate(con,markets,now)
    screens=pipeline.screen_pending(con,old_research_root)
    pipeline.compare(con,now)
    calibration=import_calibration(con,now)
    attempted=[]
    # No busy-loop reruns of a consumed holdout or interrupted trial.
    for identity, in con.execute('SELECT id FROM candidates WHERE end<=? AND id NOT IN (SELECT candidate FROM trials) ORDER BY created,id LIMIT 12',(now,)).fetchall():
        try: attempted.append(pipeline.historical(con,identity,history_root,now)['status'])
        except (ValueError,LookupError,OSError) as exc:
            attempted.append('BLOCKED_'+type(exc).__name__)
            break
    if con.execute("SELECT 1 FROM trials WHERE status='STARTED'").fetchone():
        raise ValueError('Interrupted trial requires explicit reconciliation; holdout not repeated')
    qualified=[]
    for identity,body in con.execute("SELECT candidate,body FROM trials WHERE status='HISTORICAL_QUALIFIED' AND candidate NOT IN (SELECT candidate FROM paper)"):
        result=json.loads(body); score=result['results']['holdout']['stress']['net_return']
        qualified.append((ex.D(score),identity))
    for _,identity in sorted(qualified,reverse=True):
        # Quota does not convert a waiting candidate into a failed one.
        from datetime import datetime,timezone
        week=datetime.fromtimestamp(now/1000,timezone.utc).strftime('%G-W%V')
        if con.execute('SELECT count(*) FROM paper WHERE week=?',(week,)).fetchone()[0]>=2: break
        pipeline.admit(con,identity,now)
    details={'generation':generation,'new_known_data_screens':screens,
             'calibration':calibration,'historical_attempts':attempted,
             'real_exchange_orders':0,'paid_provider_calls':0}
    with con: con.execute('INSERT OR REPLACE INTO health VALUES(2,?,?,?)',(now,'PASS',history.encode(details).decode()))
    return details


def capture_work(con,history_root,now=None):
    now=int(time.time()*1000) if now is None else now
    markets=metadata(con,now); snapshots=[]
    for market in markets:
        raw,sent,received=post(con,{'type':'l2Book','coin':market['coin']},2)
        snapshots.append(pipeline.retain_book(con,raw,market,sent,received))
    now=int(time.time()*1000)
    for identity, in con.execute("SELECT candidate FROM paper WHERE status='RUNNING' ORDER BY candidate").fetchall():
        candidate=pipeline.candidate(con,identity)
        if candidate['market'] not in markets: raise ValueError('Paper metadata identity changed')
        hour=now//pipeline.HOUR*pipeline.HOUR
        bars=pipeline.closed_bars(history_root,candidate['market'],hour-48*pipeline.HOUR,hour)
        state=pipeline.paper_tick(con,identity,bars,now)
        body=json.loads(con.execute('SELECT body FROM paper WHERE candidate=?',(identity,)).fetchone()[0])
        if state['status']=='RUNNING' and not body['recovery_verified']:
            pipeline.recovery_probe(con,identity,bars,now)
    details={'market_snapshots':len(snapshots),
             'real_exchange_orders':0,'paid_provider_calls':0}
    with con: con.execute('INSERT OR REPLACE INTO health VALUES(1,?,?,?)',(now,'PASS',history.encode(details).decode()))
    return details


def storage_budget(con):
    path=history.safe(con.execute('PRAGMA database_list').fetchone()[2])
    total=sum(p.stat().st_size for p in (path,history.safe(Path(str(path)+'-wal')),history.safe(Path(str(path)+'-shm'))) if p.is_file())
    if total>8*1024**3: raise RuntimeError('Storage budget requires reviewed retention; no automatic deletion')


def work(con,history_root,old_research_root):
    capture_work(con,history_root)
    return research_work(con,history_root,old_research_root)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command',choices=('run','once','research-once','status'))
    ap.add_argument('--state-dir',type=Path,default=STATE)
    ap.add_argument('--history-dir',type=Path,default=Path('/var/lib/sniper/hyperliquid-data'))
    ap.add_argument('--old-research-dir',type=Path,default=Path('/var/lib/sniper/hyperliquid-research'))
    args=ap.parse_args()
    if args.command=='status':
        data=pipeline.report(args.state_dir); print(json.dumps(data,indent=2,allow_nan=False))
        return 0 if data['status']=='OBSERVED' else 2
    import fcntl
    root=history.safe(args.state_dir); root.mkdir(mode=0o700,parents=True,exist_ok=True)
    lock=history.safe(root/('research.lock' if args.command=='research-once' else 'capture.lock'))
    with lock.open('a') as stream:
        os.chmod(lock,0o600); fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        stopping=[False]
        def stop(*_): stopping[0]=True
        signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
        with closing(pipeline.connect(root)) as con:
            while not stopping[0]:
                started=time.monotonic()
                try:
                    storage_budget(con)
                    if args.command=='research-once': result=research_work(con,args.history_dir,args.old_research_dir)
                    elif args.command=='once': result=work(con,args.history_dir,args.old_research_dir)
                    else: result=capture_work(con,args.history_dir)
                    if args.command!='run': print(json.dumps(result,allow_nan=False)); return 0
                except (OSError,ValueError,LookupError,RuntimeError,sqlite3.Error) as exc:
                    con.rollback()
                    result={'error_type':type(exc).__name__,'real_exchange_orders':0}
                    health_id=2 if args.command=='research-once' else 1
                    with con: con.execute('INSERT OR REPLACE INTO health VALUES(?,?,?,?)',(health_id,int(time.time()*1000),'FAILED',history.encode(result).decode()))
                    print(json.dumps(result),flush=True)
                    if args.command!='run': return 2
                delay=max(0,10-(time.monotonic()-started))
                # Bounded, interruptible sleep; no catch-and-forget failures.
                end=time.monotonic()+delay
                while not stopping[0] and time.monotonic()<end: time.sleep(min(.5,end-time.monotonic()))
            # No financial action on exit. Pending PAPER entries are cancelled;
            # a subsequent feed gap invalidates the qualification epoch.
            with con:
                for identity,lane,decision,request in con.execute('SELECT candidate,lane,decision,request FROM orders WHERE result IS NULL').fetchall():
                    order=json.loads(request)
                    con.execute('UPDATE orders SET result=? WHERE candidate=? AND lane=? AND decision=?',
                        (history.encode({'status':'CANCELLED_WORKER_STOP','filled':'0','cancelled':order['quantity']}).decode(),identity,lane,decision))
    return 0


if __name__=='__main__': raise SystemExit(main())
