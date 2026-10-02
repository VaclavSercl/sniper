"""Versioned measured-book research and immutable forward-paper state machine.

An owned private database is an application trust boundary. This code never
signs, submits orders or treats caller-supplied qualification flags as evidence.
Old epochs/holdouts are not reset. New holdouts start after preregistration.
"""
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import zlib

import execution_evidence as ex
import strategy_recipes as recipes
from hyperliquid_history import safe, encode, integer, number

D=Decimal
HOUR=3600000
DAY=24*HOUR
SCHEMA='''CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE metadata(id TEXT PRIMARY KEY,created INTEGER NOT NULL,body BLOB NOT NULL);
CREATE TABLE books(market TEXT NOT NULL,sent INTEGER NOT NULL,received INTEGER NOT NULL,
 venue INTEGER NOT NULL,bid TEXT NOT NULL,sha TEXT NOT NULL,body BLOB NOT NULL,
 PRIMARY KEY(market,sent));
CREATE INDEX book_receipt ON books(market,received);
CREATE TABLE families(id TEXT PRIMARY KEY,day TEXT NOT NULL,created INTEGER NOT NULL,recipe TEXT NOT NULL);
CREATE TABLE candidates(id TEXT PRIMARY KEY,family TEXT NOT NULL REFERENCES families(id),
 market TEXT NOT NULL,lookback INTEGER NOT NULL,created INTEGER NOT NULL,start INTEGER NOT NULL,end INTEGER NOT NULL,
 body TEXT NOT NULL,UNIQUE(family,market,lookback));
CREATE TABLE screens(candidate TEXT PRIMARY KEY REFERENCES candidates(id),status TEXT NOT NULL,body TEXT NOT NULL);
CREATE TABLE trials(candidate TEXT PRIMARY KEY REFERENCES candidates(id),day TEXT NOT NULL,
 status TEXT NOT NULL,holdout_used INTEGER NOT NULL,body TEXT NOT NULL);
CREATE TABLE paper(candidate TEXT PRIMARY KEY REFERENCES candidates(id),week TEXT NOT NULL,
 started INTEGER NOT NULL,status TEXT NOT NULL,last_ms INTEGER NOT NULL,body TEXT NOT NULL);
CREATE TABLE orders(candidate TEXT NOT NULL,lane TEXT NOT NULL,decision INTEGER NOT NULL,
 side TEXT NOT NULL,request TEXT NOT NULL,result TEXT,PRIMARY KEY(candidate,lane,decision));
CREATE TABLE marks(candidate TEXT NOT NULL,lane TEXT NOT NULL,time INTEGER NOT NULL,
 equity TEXT NOT NULL,observed INTEGER NOT NULL,book_sha TEXT NOT NULL,PRIMARY KEY(candidate,lane,time));
CREATE TABLE calibrations(id TEXT PRIMARY KEY,body BLOB NOT NULL);
CREATE TABLE health(id INTEGER PRIMARY KEY,time INTEGER NOT NULL,status TEXT NOT NULL,body TEXT NOT NULL);
CREATE TABLE requests(time INTEGER NOT NULL,weight INTEGER NOT NULL);
CREATE TABLE comparisons(week TEXT PRIMARY KEY,body TEXT NOT NULL);
'''


def source_hash():
    return ex.sha({p.name:hashlib.sha256(safe(p.resolve()).read_bytes()).hexdigest() for p in
                   (Path(ex.__file__),Path(recipes.__file__),Path(__file__),Path(__file__).with_name('forward_reference.py'))})


def connect(root):
    root=safe(root); root.mkdir(mode=0o700,parents=True,exist_ok=True)
    path=safe(root/'forward.sqlite3'); existed=path.exists()
    for suffix in ('-wal','-shm','-journal'): safe(Path(str(path)+suffix))
    if existed and path.stat().st_size>8*1024**3: raise ValueError('Storage budget requires reviewed retention')
    con=sqlite3.connect(path,timeout=3)
    try:
        con.execute('PRAGMA foreign_keys=ON'); con.execute('PRAGMA synchronous=FULL')
        con.execute('PRAGMA journal_mode=WAL')
        if not existed:
            con.executescript('BEGIN IMMEDIATE;'+SCHEMA)
            con.executemany('INSERT INTO settings VALUES(?,?)',
                [('schema','1'),('campaign',ex.sha(recipes.CAMPAIGN)),('model',ex.sha(ex.MODEL)),('source',source_hash())])
            con.commit(); os.chmod(path,0o600)
        validate_store(con)
        return con
    except BaseException: con.close(); raise


def validate_store(con):
    settings=dict(con.execute('SELECT key,value FROM settings'))
    expected={'schema':'1','campaign':ex.sha(recipes.CAMPAIGN),'model':ex.sha(ex.MODEL),'source':source_hash()}
    if any(settings.get(k)!=v for k,v in expected.items()):
        raise ValueError('Frozen campaign/code changed; explicit separate epoch required')


def unpack(blob,limit=4*1024*1024):
    if len(blob)>limit: raise ValueError('Oversized stored evidence')
    decoder=zlib.decompressobj(); raw=decoder.decompress(blob,limit+1)
    if len(raw)>limit or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError('Invalid compressed evidence')
    return json.loads(raw)


def retain_book(con,raw,market,sent,received):
    snapshot=ex.book(raw,market,sent,received)
    body=encode({'raw':raw,'snapshot':snapshot}); digest=hashlib.sha256(body).hexdigest()
    with con:
        con.execute('INSERT INTO books VALUES(?,?,?,?,?,?,?)',
            (market['id'],sent,received,snapshot['venue_ms'],snapshot['levels'][0][0][0],digest,zlib.compress(body)))
    return snapshot


def decoded(row):
    sent,received,digest,blob=row; value=unpack(blob)
    if ex.sha(value)!=digest: raise ValueError('Stored book fingerprint changed')
    snapshot=value['snapshot']
    if ex.book(value['raw'],snapshot['market'],sent,received)!=snapshot:
        raise ValueError('Book reconstruction differs')
    return snapshot


def future_book(con,market,decision,stress=False):
    arrival=decision+ex.MODEL['latency_ms']*(2 if stress else 1)
    row=con.execute('SELECT sent,received,sha,body FROM books WHERE market=? AND sent>=? AND venue>=? ORDER BY sent LIMIT 1',
                    (market,arrival,arrival)).fetchone()
    if row is None: raise LookupError('Future book not yet available')
    snap=decoded(row)
    if snap['received_ms']-arrival>ex.MODEL['max_gap_ms']: raise ValueError('Future book gap')
    return snap


def latest_book(con,market,now):
    row=con.execute('SELECT sent,received,sha,body FROM books WHERE market=? AND received<=? ORDER BY sent DESC LIMIT 1',
                    (market,now)).fetchone()
    if row is None: raise LookupError('No observed book')
    snap=decoded(row)
    if now-snap['venue_ms']>ex.MODEL['max_book_age_ms']: raise ValueError('Stale book')
    return snap


def generate(con,markets,now):
    integer(now); day=datetime.fromtimestamp(now/1000,timezone.utc).date().isoformat()
    spots=[m for m in markets if m['product']=='spot']
    if len(spots)!=2 or len({m['id'] for m in spots})!=2: raise ValueError('Exact two spot identities required')
    created=[]; variants=0
    with con:
        con.execute('BEGIN IMMEDIATE'); validate_store(con)
        used=con.execute('SELECT count(*) FROM families WHERE day=?',(day,)).fetchone()[0]
        for index in range(len(recipes.GRAMMAR)):
            if used>=recipes.CAMPAIGN['mechanisms_per_day']: break
            recipe=recipes.proposed(index); economic=recipes.economic_id(recipe)
            if con.execute('SELECT 1 FROM families WHERE id=?',(economic,)).fetchone(): continue
            con.execute('INSERT INTO families VALUES(?,?,?,?)',(economic,day,now,encode(recipe).decode()))
            start=(now//DAY+1)*DAY; end=start+recipes.CAMPAIGN['history_days']*DAY
            for market in spots:
                for lookback in recipes.LOOKBACKS[recipe['trigger']]:
                    parameters={**recipe,'lookback_hours':lookback}
                    value={'schema':1,'family':economic,'recipe':parameters,'market':market,
                           'created_ms':now,'historical_start_ms':start,'historical_end_ms':end,
                           'campaign':recipes.CAMPAIGN,'model':ex.MODEL,'source_sha256':source_hash(),
                           'variant_scope':'MARKET_AND_LOOKBACK_NOT_NEW_ECONOMIC_MECHANISM'}
                    identity=ex.sha(value)
                    con.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?,?,?)',
                        (identity,economic,market['id'],lookback,now,start,end,encode(value).decode()))
                    variants+=1
            used+=1; created.append(economic)
    return {'status':'GENERATED' if created else ('EXHAUSTED' if con.execute('SELECT count(*) FROM families').fetchone()[0]==len(recipes.GRAMMAR) else 'DAILY_QUOTA'),
            'new_economic_mechanisms':len(created),'new_market_variants':variants}


def candidate(con,identity):
    row=con.execute('SELECT body FROM candidates WHERE id=?',(identity,)).fetchone()
    if row is None: raise ValueError('Unknown candidate')
    value=json.loads(row[0]); recipes.validate(value['recipe'])
    if ex.sha(value)!=identity or value['source_sha256']!=source_hash() or value['campaign']!=recipes.CAMPAIGN or value['model']!=ex.MODEL:
        raise ValueError('Candidate identity or protocol changed')
    return value


def closed_bars(history_root,market,start,end):
    path=safe(Path(history_root)/'history.sqlite3')
    if not path.is_file(): raise FileNotFoundError('Hourly venue history missing')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as src:
        src.execute('BEGIN')
        rows=src.execute('SELECT body,artifact FROM candles WHERE market=? AND interval=? AND time_ms>=? AND time_ms<? ORDER BY time_ms',
                         (market['id'],'1h',start,end)).fetchall()
        # Reconstruct from retained raw responses, independently of normalized rows.
        import hyperliquid_history as history
        verified={}; bars=[]
        for body,artifact in rows:
            if artifact not in verified:
                request=src.execute("SELECT start_ms,end_ms FROM captures WHERE market=? AND kind='1h' AND artifact=? ORDER BY rowid LIMIT 1",(market['id'],artifact)).fetchone()
                if request is None: raise ValueError('Candle request provenance missing')
                raw=json.loads(history.raw_artifact(src,artifact))
                verified[artifact]={r['time']:encode(r).decode() for r in history.candles(raw,market,'1h',*request)}
            bar=json.loads(body)
            if verified[artifact].get(bar['time'])!=body: raise ValueError('Changed candle source')
            bars.append(bar)
    if len(bars)!=(end-start)//HOUR or any(b['time']!=start+i*HOUR for i,b in enumerate(bars)):
        raise ValueError('Closed hourly coverage incomplete')
    return bars


def preview_screen(recipe,bars,stress=False):
    """Known-data diagnostic only. No book fills, no final holdout, no eligibility."""
    initial=D('1000'); cash=initial; inventory=D(0); entered=0; fills=0; marks=[]
    fee=D(ex.MODEL['fee_rate'])*(2 if stress else 1); slippage=D('.005')*(2 if stress else 1)
    for i,row in enumerate(bars):
        stamp=row['time']; price=D(row['open'])
        if inventory and stamp-entered>=recipe['holding_hours']*HOUR:
            cash+=inventory*price*(1-slippage)*(1-fee); inventory=D(0); fills+=1
        elif not inventory and recipes.signal(recipe,bars[:i],stamp):
            ntl=D('45'); amount=ntl/(price*(1+slippage)); cash-=ntl; inventory=amount*(1-fee); entered=stamp; fills+=1
        marks.append([stamp,ex.wire(cash+inventory*D(row['close']))])
    return ex.performance(marks,fills)


def screen_pending(con,old_research_root):
    """Use only the frozen old TRAIN/VALIDATION portion; never its final holdout."""
    rows=con.execute('SELECT id FROM candidates WHERE id NOT IN (SELECT candidate FROM screens) ORDER BY created,id LIMIT 12').fetchall()
    if not rows: return 0
    path=safe(Path(old_research_root)/'cycle.sqlite3')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as src:
        from research_cycle import load_epoch
        epoch,epoch_sha=load_epoch(src)
    done=0
    for identity, in rows:
        value=candidate(con,identity); bars=epoch['series'][value['market']['id']]
        train_end=len(bars)*60//100; validation_end=len(bars)*80//100
        phases={}
        for name,start,end in (('train',0,train_end),('validation',train_end,validation_end)):
            segment=bars[start:end]
            phases[name]={'normal':preview_screen(value['recipe'],segment),
                          'stress':preview_screen(value['recipe'],segment,True)}
        result={'status':'CANDLE_DIAGNOSTIC_ONLY','source_epoch':epoch_sha,'phases':phases,
                'known_data':True,'holdout_evaluated':False,'qualified':False,
                'execution_scope':'OHLC_ASSUMPTIONS_NOT_BOOK_EXECUTION'}
        with con: con.execute('INSERT INTO screens VALUES(?,?,?)',(identity,result['status'],encode(result).decode()))
        done+=1
    return done


def compare(con,now):
    """Weekly snapshots within the same market and frozen known-data epoch."""
    week=datetime.fromtimestamp(now/1000,timezone.utc).strftime('%G-W%V')
    if con.execute('SELECT 1 FROM comparisons WHERE week=?',(week,)).fetchone(): return
    cohorts={}
    for identity,market,body in con.execute('SELECT c.id,c.market,s.body FROM candidates c JOIN screens s ON c.id=s.candidate ORDER BY c.id'):
        result=json.loads(body); key=market+'|'+result['source_epoch']
        score=min(D(p['stress']['net_return']) for p in result['phases'].values())
        cohorts.setdefault(key,[]).append({'candidate':identity,'worst_stress_diagnostic_return':ex.wire(score)})
    record={'scope':'KNOWN_DATA_DIAGNOSTIC_ONLY_NO_ADMISSION','week':week,'time_ms':now,
            'cohorts':{k:sorted(v,key=lambda r:(-D(r['worst_stress_diagnostic_return']),r['candidate'])) for k,v in sorted(cohorts.items())}}
    with con: con.execute('INSERT INTO comparisons VALUES(?,?)',(week,encode(record).decode()))


def book_archive(con,market,start,end):
    """Stream fingerprint all raw book records and check coverage, not just fills."""
    previous=None; digest=hashlib.sha256(); minima={}; maxima={}; rows=0
    for sent,received,venue,bid,signature,blob in con.execute('SELECT sent,received,venue,bid,sha,body FROM books WHERE market=? AND received>=? AND received<? ORDER BY sent',(market,start,end)):
        snap=decoded((sent,received,signature,blob))
        if snap['levels'][0][0][0]!=bid or snap['venue_ms']!=venue: raise ValueError('Book index differs from raw source')
        if previous is None:
            if received-start>ex.MODEL['max_gap_ms']: raise ValueError('Archive start gap')
        elif received-previous>ex.MODEL['max_gap_ms'] or received<=previous: raise ValueError('Archive feed gap')
        previous=received; rows+=1
        digest.update(encode([sent,received,signature])); hour=received//HOUR*HOUR
        minima[hour]=min(minima.get(hour,D(bid)),D(bid))
        maxima[hour]=max(maxima.get(hour,D(bid)),D(bid))
    if previous is None or end-previous>ex.MODEL['max_gap_ms']: raise ValueError('Archive end gap')
    return {'sha256':digest.hexdigest(),'rows':rows,'minimum_bids':minima,'maximum_bids':maxima}


def replay(con,value,bars,start,end,stress=False,benchmark=None,minima=None,maxima=None):
    cash=D(recipes.CAMPAIGN['hypothetical_capital']); inventory=D(0); entered=None
    marks=[]; fills=0; peak=cash; daily=cash; loss=False; first_day=None
    journal=[]; recipe=value['recipe']; market=value['market']
    for i,row in enumerate(bars):
        stamp=row['time']
        if not start<=stamp<end: continue
        snap=latest_book(con,market['id'],stamp); bid=D(snap['levels'][0][0][0])
        equity=cash+inventory*bid; peak=max(peak,equity)
        day=stamp//DAY
        if day!=first_day: daily=cash+inventory*bid; first_day=day
        if peak-equity>D('1000')*D('.9')*D('.03') or daily-equity>D('1000')*D('.9')*D('.01') or inventory*bid>D('180'): loss=True
        buy=False; sell=False
        has_position=inventory*bid>=D(ex.MODEL['minimum_notional'])
        if has_position:
            sell=loss or (benchmark!='buy_hold' and stamp-entered>=recipe['holding_hours']*HOUR) or stamp==end-HOUR
        elif i>=recipes.CAMPAIGN['warmup_hours'] and not loss and stamp<end-recipe['holding_hours']*HOUR:
            if benchmark=='buy_hold': buy=entered is None
            elif benchmark=='ma': buy=sum(D(b['close']) for b in bars[i-6:i])/6>sum(D(b['close']) for b in bars[i-24:i])/24
            elif benchmark!='cash': buy=recipes.signal(recipe,bars[max(0,i-48):i],stamp)
        if buy or sell:
            limit=ex.limit_price(snap,buy)
            quantity=ex.lot(D('45')/D(limit) if buy else inventory,market['size_decimals'])
            if quantity:
                order={'decision_ms':stamp,'side':'B' if buy else 'A','limit':limit,'quantity':ex.wire(quantity)}
                future=future_book(con,market['id'],stamp,stress)
                outcome=ex.ioc(order,future,ex.wire(cash),ex.wire(inventory),stress)
                cash=D(outcome['cash']); inventory=D(outcome['inventory'])
                if D(outcome['filled'])>0: fills+=1; entered=stamp if buy else entered
                journal.append({'order':order,'outcome':outcome})
        # Future adverse prices are assessed only AFTER this hour's decision.
        # They can pause the next hour, never alter the already-made signal.
        adverse_equity=cash+inventory*(minima or {}).get(stamp,bid)
        peak=max(peak,cash+inventory*(maxima or {}).get(stamp,bid))
        if peak-adverse_equity>D('27') or daily-adverse_equity>D('9'): loss=True
        close=latest_book(con,market['id'],stamp+HOUR)
        equity=cash if stamp==end-HOUR else cash+inventory*D(close['levels'][0][0][0])
        # At the terminal boundary, residual inventory (including dust) is
        # written off rather than assuming a free executable liquidation.
        # Time denotes the CLOSED hourly period, not an assertion of an
        # instantaneous pre-fill equity observation at the period's opening.
        marks.append([stamp,ex.wire(equity)])
    result=ex.performance(marks,fills)
    result.update(risk_breached=loss,remaining_inventory=ex.wire(inventory),
                  journal_sha256=ex.sha(journal),equity_sha256=ex.sha(marks),journal=journal,equity=marks)
    return result


def calibration_snapshot(con,now):
    samples=[]
    for identity,blob in con.execute('SELECT id,body FROM calibrations ORDER BY id'):
        value=unpack(blob)
        if ex.sha(value)!=identity: raise ValueError('Changed calibration journal')
        stamp=integer(value['actual']['time_ms'])
        if stamp>now: raise ValueError('Future calibration evidence')
        if now-ex.MODEL['calibration_max_age_ms']<=stamp: samples.append(value)
    return ex.calibration(samples,now)


def historical(con,identity,history_root,now):
    """Consume final holdout durably before its first evaluation. Never retry it."""
    value=candidate(con,identity); start=value['historical_start_ms']; end=value['historical_end_ms']
    if now<end: return {'status':'WAITING_PROSPECTIVE_HISTORY','candidate':identity,'ready_after_ms':end}
    cal=calibration_snapshot(con,now)
    if cal['status']!='PASS': return {'status':'BLOCKED_CALIBRATION','candidate':identity,'calibration':cal}
    if con.execute('SELECT 1 FROM trials WHERE candidate=?',(identity,)).fetchone(): return {'status':'ALREADY_ATTEMPTED','candidate':identity}
    archive=book_archive(con,value['market']['id'],start,end)
    bars=closed_bars(history_root,value['market'],start,end)
    day=datetime.fromtimestamp(now/1000,timezone.utc).date().isoformat()
    with con:
        con.execute('BEGIN IMMEDIATE')
        if con.execute('SELECT count(*) FROM trials WHERE day=?',(day,)).fetchone()[0]>=recipes.CAMPAIGN['bundles_per_day']:
            return {'status':'DAILY_TEST_QUOTA','candidate':identity}
        con.execute('INSERT INTO trials VALUES(?,?,?,?,?)',(identity,day,'STARTED',0,encode({'candidate':identity,'calibration':cal,'archive_sha256':archive['sha256'],'candle_sha256':ex.sha(bars)}).decode()))
    a=start+int(D(recipes.CAMPAIGN['train_fraction'])*len(bars))*HOUR
    b=a+int(D(recipes.CAMPAIGN['validation_fraction'])*len(bars))*HOUR
    results={}; state='REJECTED'
    def phase(left,right):
        bounds={'minima':archive['minimum_bids'],'maxima':archive.get('maximum_bids',{})}
        return {'normal':replay(con,value,bars,left,right,**bounds),
                'stress':replay(con,value,bars,left,right,True,**bounds),
                'benchmarks':{name:replay(con,value,bars,left,right,benchmark=name,**bounds) for name in ('cash','buy_hold','ma')}}
    def passed(r,holdout=False):
        for key in ('normal','stress'):
            leg=r[key]
            if (D(leg['net_return'])<=0 or leg['risk_breached'] or leg['filled_orders']<recipes.CAMPAIGN['minimum_orders'] or
                D(leg['max_drawdown'])>D(recipes.CAMPAIGN['max_drawdown']) or
                D(leg['net_return'])<=max(D(v['net_return']) for v in r['benchmarks'].values())): return False
            if holdout and (leg['complete_days']<recipes.CAMPAIGN['minimum_holdout_days'] or
                D(str(leg['positive_block_sign_pvalue']))>D(recipes.CAMPAIGN['alpha'])/recipes.CAMPAIGN['max_trials']): return False
        return True
    results['train']=phase(start,a)
    if passed(results['train']):
        results['validation']=phase(a,b)
        if passed(results['validation']):
            with con: con.execute('UPDATE trials SET holdout_used=1 WHERE candidate=?',(identity,))
            results['holdout']=phase(b,end)
            if passed(results['holdout'],True): state='HISTORICAL_QUALIFIED'
    from forward_reference import verify
    reference=[]
    for name,(left,right) in {'train':(start,a),'validation':(a,b),'holdout':(b,end)}.items():
        if name in results:
            for leg in ('normal','stress'):
                reference.append(verify(con,value,bars,left,right,results[name][leg],leg=='stress',minima=archive['minimum_bids'],maxima=archive.get('maximum_bids',{})))
            for key,leg in results[name]['benchmarks'].items():
                reference.append(verify(con,value,bars,left,right,leg,benchmark=key,minima=archive['minimum_bids'],maxima=archive.get('maximum_bids',{})))
    record={'candidate':identity,'status':state,'results':results,'calibration':cal,
            'archive_sha256':archive['sha256'],'candle_sha256':ex.sha(bars),
            'independent_author_review':'NOT_VERIFIED','reproduced':True,'source_sha256':source_hash(),
            'independent_reference':reference}
    with con: con.execute('UPDATE trials SET status=?,body=? WHERE candidate=?',(state,encode(record).decode(),identity))
    return record


def admit(con,identity,now):
    """Automatic worker uses independently reproduced exact historical evidence.

    Results include a separate reference ledger, never caller-supplied pass flags.
    """
    value=candidate(con,identity)
    row=con.execute('SELECT status,body,holdout_used FROM trials WHERE candidate=?',(identity,)).fetchone()
    if row is None or row[0]!='HISTORICAL_QUALIFIED' or row[2]!=1:
        raise ValueError('Historical qualification missing')
    record=json.loads(row[1])
    if (record['candidate']!=identity or record['status']!=row[0] or record['source_sha256']!=source_hash() or
        record['reproduced'] is not True or len(record['independent_reference'])!=15 or
        record['calibration']['status']!='PASS' or record['calibration']['model_sha256']!=ex.sha(ex.MODEL)):
        raise ValueError('Independent reference identity/results missing')
    reference=record['independent_reference']
    for phase in ('train','validation','holdout'):
        r=record['results'][phase]
        for lane in ('normal','stress'):
            leg=r[lane]
            if (D(leg['net_return'])<=0 or leg['risk_breached'] or leg['filled_orders']<10 or D(leg['max_drawdown'])>D('.03') or
                D(leg['net_return'])<=max(D(v['net_return']) for v in r['benchmarks'].values()) or
                (phase=='holdout' and (leg['complete_days']<40 or D(str(leg['positive_block_sign_pvalue']))>D('.05')/recipes.CAMPAIGN['max_trials']))):
                raise ValueError('Historical qualification performance failed')
    legs=[r[k] for r in record['results'].values() for k in ('normal','stress')]+[leg for r in record['results'].values() for leg in r['benchmarks'].values()]
    if sorted(r['result_sha256'] for r in reference if r['status']=='PASS')!=sorted(ex.sha(r) for r in legs):
        raise ValueError('Independent reference differs')
    if calibration_snapshot(con,now)['status']!='PASS': raise ValueError('Fresh measured calibration required')
    if now<=value['historical_end_ms']: raise ValueError('Paper cannot predate final historical window')
    week=datetime.fromtimestamp(now/1000,timezone.utc).strftime('%G-W%V')
    state={'candidate':identity,'cash':'1000','inventory':'0','entered_ms':None,
           'stress_cash':'1000','stress_inventory':'0','stress_entered_ms':None,
           'peak':'1000','day':now//DAY,'day_equity':'1000','filled_orders':0,
           'stress_filled_orders':0,'source_sha256':source_hash(),
           'historical_sha256':ex.sha(record),'reference_sha256':ex.sha(reference),
           'risk_paused':False,'recovery_verified':False,'last_decision_ms':None}
    with con:
        con.execute('BEGIN IMMEDIATE')
        if con.execute('SELECT count(*) FROM paper WHERE week=?',(week,)).fetchone()[0]>=recipes.CAMPAIGN['paper_per_week']:
            raise ValueError('Weekly admission quota')
        con.execute('INSERT INTO paper VALUES(?,?,?,?,?,?)',(identity,week,now,'RUNNING',now,encode(state).decode()))
    return state


def paper_tick(con,identity,bars,now,stopping=False):
    value=candidate(con,identity); row=con.execute('SELECT started,status,last_ms,body FROM paper WHERE candidate=?',(identity,)).fetchone()
    if row is None: raise ValueError('No qualified admission')
    started,status,last,body=row; state=json.loads(body)
    if status!='RUNNING': return {'status':status}
    if now<last or now<started: raise ValueError('Paper clock moved backwards')
    gap=now-last>ex.MODEL['max_gap_ms']
    if gap:
        with con:
            for lane,decision,request in con.execute('SELECT lane,decision,request FROM orders WHERE candidate=? AND result IS NULL',(identity,)).fetchall():
                order=json.loads(request)
                con.execute('UPDATE orders SET result=? WHERE candidate=? AND lane=? AND decision=?',
                            (encode({'status':'CANCELLED_FEED_GAP','filled':'0','cancelled':order['quantity']}).decode(),identity,lane,decision))
            state['risk_paused']=True
            con.execute('UPDATE paper SET status=?,last_ms=?,body=? WHERE candidate=?',('INVALIDATED_FEED_GAP',now,encode(state).decode(),identity))
        return {'status':'INVALIDATED_FEED_GAP','risk_paused':True}
    snapshot=latest_book(con,value['market']['id'],now)
    hour=now//HOUR*HOUR; bid=D(snapshot['levels'][0][0][0])
    equity=D(state['cash'])+D(state['inventory'])*bid
    if state['day']!=now//DAY: state.update(day=now//DAY,day_equity=ex.wire(equity))
    peak=max(D(state['peak']),equity); state['peak']=ex.wire(peak)
    stress_eq=D(state['stress_cash'])+D(state['stress_inventory'])*bid
    stress_peak=max(D(state.get('stress_peak','1000')),stress_eq); state['stress_peak']=ex.wire(stress_peak)
    if state.get('stress_day')!=now//DAY: state.update(stress_day=now//DAY,stress_day_equity=ex.wire(stress_eq))
    risk=(peak-equity>D('27') or D(state['day_equity'])-equity>D('9') or
          stress_peak-stress_eq>D('27') or D(state['stress_day_equity'])-stress_eq>D('9') or
          D(state['inventory'])*bid>D('180') or D(state['stress_inventory'])*bid>D('180'))
    state['risk_paused']=state['risk_paused'] or risk or stopping or gap
    with con:
        con.execute('BEGIN IMMEDIATE')
        for lane,prefix,stress in (('normal','',False),('stress','stress_',True)):
            cash=D(state[prefix+'cash']); inventory=D(state[prefix+'inventory'])
            pending=con.execute('SELECT decision,request FROM orders WHERE candidate=? AND lane=? AND result IS NULL ORDER BY decision',(identity,lane)).fetchall()
            if len(pending)>1: raise ValueError('Overlapping paper intent')
            for decision,request in pending:
                order=json.loads(request)
                if stopping or gap:
                    outcome={'status':'CANCELLED_STOP_OR_GAP','filled':'0','cancelled':order['quantity'],'fee':'0','cash':ex.wire(cash),'inventory':ex.wire(inventory)}
                else:
                    try: future=future_book(con,value['market']['id'],decision,stress)
                    except LookupError: continue
                    outcome=ex.ioc(order,future,ex.wire(cash),ex.wire(inventory),stress)
                cash=D(outcome['cash']); inventory=D(outcome['inventory'])
                con.execute('UPDATE orders SET result=? WHERE candidate=? AND lane=? AND decision=?',
                            (encode(outcome).decode(),identity,lane,decision))
                if D(outcome['filled'])>0:
                    state[prefix+'filled_orders']+=1
                    if order['side']=='B': state[prefix+'entered_ms']=decision
            pending=con.execute('SELECT 1 FROM orders WHERE candidate=? AND lane=? AND result IS NULL',(identity,lane)).fetchone()
            decision=None; buy=False
            if not pending:
                has_position=inventory*bid>=D('10')
                if has_position and (state['risk_paused'] or (hour!=state['last_decision_ms'] and hour-state[prefix+'entered_ms']>=value['recipe']['holding_hours']*HOUR)):
                    decision=now
                elif not has_position and not state['risk_paused'] and hour!=state['last_decision_ms'] and recipes.signal(value['recipe'],bars,hour):
                    decision=now; buy=True
                if decision is not None:
                    limit=ex.limit_price(snapshot,buy); quantity=ex.lot(D('45')/D(limit) if buy else inventory,value['market']['size_decimals'])
                    if quantity and quantity*D(limit)>=D('10'):
                        order={'decision_ms':decision,'side':'B' if buy else 'A','limit':limit,'quantity':ex.wire(quantity)}
                        con.execute('INSERT INTO orders VALUES(?,?,?,?,?,NULL)',(identity,lane,decision,order['side'],encode(order).decode()))
            state[prefix+'cash']=ex.wire(cash); state[prefix+'inventory']=ex.wire(inventory)
            # Marks are taken only near the actual hour. An old hour must never
            # be backfilled with a later price after disconnect/restart.
            if hour>started//HOUR*HOUR and now-hour<=ex.MODEL['max_gap_ms']:
                con.execute('INSERT OR IGNORE INTO marks VALUES(?,?,?,?,?,?)',
                            (identity,lane,hour-HOUR,ex.wire(cash+inventory*bid),snapshot['received_ms'],ex.sha(snapshot)))
        state['last_decision_ms']=hour
        status='STOPPED' if stopping and D(state['inventory'])*bid<D('10') and D(state['stress_inventory'])*bid<D('10') else 'RUNNING'
        con.execute('UPDATE paper SET status=?,last_ms=?,body=? WHERE candidate=?',(status,now,encode(state).decode(),identity))
    return {'status':status,'risk_paused':state['risk_paused'],'equity':ex.wire(equity)}


def recovery_probe(con,identity,bars,now):
    """Fault-inject on two isolated clones of the actual paper journal.

    Test stop/cancel, disconnect invalidation and reopening/deduplication. The
    production journal is not rewound. This is paper fault testing, not real
    exchange recovery. Evidence is bound to code, candidate and current journal.
    """
    original=con.execute('SELECT body FROM paper WHERE candidate=?',(identity,)).fetchone()
    if original is None: raise ValueError('Paper admission required')
    proofs=[]
    with tempfile.TemporaryDirectory(prefix='sniper-paper-recovery-') as directory:
        for index in range(2):
            path=Path(directory)/str(index); path.mkdir(mode=0o700)
            with closing(connect(path)) as clone:
                # Copy the candidate journal and relevant recent books only.
                # Copying a months-long public archive would stall capture.
                value=candidate(con,identity)
                selectors=(('families','id',value['family']),('candidates','id',identity),
                           ('paper','candidate',identity),('orders','candidate',identity),('marks','candidate',identity))
                with clone:
                    for table,column,selected in selectors:
                        for record in con.execute('SELECT * FROM '+table+' WHERE '+column+'=?',(selected,)):
                            clone.execute('INSERT INTO '+table+' VALUES('+','.join('?' for _ in record)+')',record)
                    for record in con.execute('SELECT * FROM books WHERE market=? AND received>=?',(value['market']['id'],now-60000)):
                        clone.execute('INSERT INTO books VALUES(?,?,?,?,?,?,?)',record)
                before=clone.execute('SELECT count(*) FROM orders').fetchone()[0]
                result=paper_tick(clone,identity,bars,now if index==0 else now+ex.MODEL['max_gap_ms']+1,stopping=index==0)
                if index==0:
                    pending_buys=clone.execute("SELECT count(*) FROM orders WHERE candidate=? AND side='B' AND result IS NULL",(identity,)).fetchone()[0]
                    if pending_buys: raise ValueError('Stop did not cancel pending entry')
                elif result['status']!='INVALIDATED_FEED_GAP' or clone.execute('SELECT 1 FROM orders WHERE candidate=? AND result IS NULL',(identity,)).fetchone():
                    raise ValueError('Disconnect retained an unresolved paper intent')
                clone.commit(); after=clone.execute('SELECT count(*) FROM orders').fetchone()[0]
            with closing(connect(path)) as reopened:
                if reopened.execute('SELECT count(*) FROM orders').fetchone()[0]!=after: raise ValueError('Restart changed journal')
                duplicate=reopened.execute('SELECT candidate,lane,decision,count(*) FROM orders GROUP BY candidate,lane,decision HAVING count(*)>1').fetchone()
                if duplicate: raise ValueError('Duplicate paper intent after recovery')
            proofs.append({'case':'STOP_CANCEL_REOPEN' if index==0 else 'DISCONNECT_INVALIDATION_REOPEN','status':'PASS','orders_before':before,'orders_after':after})
    if con.execute('SELECT body FROM paper WHERE candidate=?',(identity,)).fetchone()!=original:
        raise ValueError('Recovery test mutated production journal')
    state=json.loads(original[0]); state['recovery_verified']=True
    state['recovery_evidence']={'scope':'ISOLATED_CLONES_OF_ACTUAL_PAPER_STATE_NOT_EXCHANGE',
        'source_sha256':source_hash(),'candidate':identity,'time_ms':now,'journal_sha256':ex.sha(state),'proofs':proofs}
    with con: con.execute('UPDATE paper SET body=? WHERE candidate=?',(encode(state).decode(),identity))
    return state['recovery_evidence']


def paper_result(con,identity,now):
    row=con.execute('SELECT started,status,last_ms,body FROM paper WHERE candidate=?',(identity,)).fetchone()
    if row is None: return {'status':'NOT_ADMITTED','qualified':False}
    started,status,last,body=row; state=json.loads(body); reasons=[]; legs={}
    value=candidate(con,identity)
    historical_row=con.execute('SELECT status,holdout_used,body FROM trials WHERE candidate=?',(identity,)).fetchone()
    if (historical_row is None or historical_row[:2]!=('HISTORICAL_QUALIFIED',1) or started<=value['historical_end_ms'] or
        state.get('source_sha256')!=source_hash() or
        state.get('historical_sha256')!=ex.sha(json.loads(historical_row[2]))): reasons.append('HISTORICAL_CHAIN_MISSING_OR_CHANGED')
    if status!='RUNNING' or state['risk_paused']: reasons.append('NOT_HEALTHY_RUNNING')
    if now-started<recipes.CAMPAIGN['paper_days']*DAY: reasons.append('THIRTY_REAL_DAYS_NOT_ELAPSED')
    if now-last>ex.MODEL['max_gap_ms'] or last>now: reasons.append('STALE_OR_FUTURE_HEARTBEAT')
    recovery=state.get('recovery_evidence',{})
    if (not state['recovery_verified'] or recovery.get('source_sha256')!=source_hash() or
        recovery.get('candidate')!=identity or len(recovery.get('proofs',[]))!=2 or
        any(p.get('status')!='PASS' for p in recovery.get('proofs',[]))):
        reasons.append('STOP_CANCEL_DISCONNECT_RECOVERY_NOT_VERIFIED')
    if calibration_snapshot(con,now)['status']!='PASS': reasons.append('CALIBRATION_MISSING_OR_STALE')
    for lane,prefix in (('normal',''),('stress','stress_')):
        marks=con.execute('SELECT time,equity FROM marks WHERE candidate=? AND lane=? ORDER BY time',(identity,lane)).fetchall()
        try:
            if D(state[prefix+'inventory'])*D(latest_book(con,value['market']['id'],now)['levels'][0][0][0])>=D('10'):
                reasons.append('OPEN_POSITION_NOT_SETTLED')
            from forward_reference import paper_ledger
            paper_ledger(con,identity,lane,value,state)
            metrics=ex.performance(marks,state[prefix+'filled_orders']); legs[lane]=metrics
            if marks[0][0]>(started//HOUR+1)*HOUR or marks[-1][0]<now//HOUR*HOUR-HOUR: reasons.append('INCOMPLETE_FORWARD_WINDOW')
            if (D(metrics['net_return'])<=0 or metrics['complete_days']<30 or metrics['filled_orders']<10 or
                    D(metrics['max_drawdown'])>D('.03') or metrics['positive_block_sign_pvalue']>.025):
                reasons.append('PERFORMANCE_SAMPLE_OR_RISK_GATE')
        except (ValueError,LookupError): reasons.append('MISSING_OR_GAPPED_EQUITY')
    return {'status':'BLOCKED' if reasons else 'PAPER_QUALIFIED','qualified':not reasons,
            'candidate':identity,'runtime_status':status,'reasons':sorted(set(reasons)),'legs':legs,'live_eligible':False}


def report(root,now=None):
    now=int(time.time()*1000) if now is None else integer(now)
    path=safe(Path(root)/'forward.sqlite3')
    if not path.is_file(): return {'status':'MISSING','qualified':False}
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as con:
        con.execute('BEGIN'); validate_store(con)
        counts={name:con.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in ('families','candidates','screens','trials','paper','orders','books')}
        health=con.execute('SELECT time,status,body FROM health WHERE id=1').fetchone()
        research=con.execute('SELECT time,status,body FROM health WHERE id=2').fetchone()
        book_counts=[{'market':m,'snapshots':n,'first_ms':a,'last_ms':b} for m,n,a,b in con.execute('SELECT market,count(*),min(received),max(received) FROM books GROUP BY market ORDER BY market')]
        papers=[paper_result(con,i,now) for i, in con.execute('SELECT candidate FROM paper ORDER BY candidate')]
        cal=calibration_snapshot(con,now)
        return {'status':'OBSERVED' if health and health[1]=='PASS' and 0<=now-health[0]<=60000 and research and research[1]=='PASS' and 0<=now-research[0]<=1800000 else 'STALE_OR_FAILED',
                'campaign':recipes.CAMPAIGN,'source_sha256':source_hash(),'counts':counts,
                'mechanisms_remaining':len(recipes.GRAMMAR)-counts['families'],
                'generator':'DETERMINISTIC_DECLARATIVE_ECONOMIC_INTERACTIONS_NO_LLM',
                'calibration':cal,'books':book_counts,'paper_results':papers,
                'historical_qualified':con.execute("SELECT count(*) FROM trials WHERE status='HISTORICAL_QUALIFIED'").fetchone()[0],
                'paper_qualified':sum(p['qualified'] for p in papers),'live_eligible':0,
                'perpetual_stage':'BLOCKED_MISSING_MARGIN_MARK_FUNDING_EXECUTION_MODEL',
                'historical_stages':[{'candidate':i,'stage':s if s else 'WAITING_PROSPECTIVE_BOOK_HISTORY','ready_after_ms':end} for i,end,s in con.execute('SELECT c.id,c.end,t.status FROM candidates c LEFT JOIN trials t ON c.id=t.candidate ORDER BY c.created,c.id')],
                'last_health':{'time_ms':health[0],'status':health[1],'details':json.loads(health[2])} if health else None,
                'research_health':{'time_ms':research[0],'status':research[1],'details':json.loads(research[2])} if research else None}
