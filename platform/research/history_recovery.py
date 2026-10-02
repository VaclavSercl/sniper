#!/usr/bin/env python3
"""Stage bounded public candles, verify provenance, render append-only SQL.

No database connection, sudo, schema creation, strategy promotion or order API.
The caller must separately review/authorize SQL execution. Never fill absent bars.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request

MINUTE = 60000
MAX_SPAN = 35 * 86400000
MAX_BODY = 8_000_000
PAIRS = {
    'binance': {'BTCUSDT','ETHUSDT','SOLUSDT','BTCEUR','BTCUSDC','EURUSDT',
                'EURUSDC','USDCUSDT','FDUSDUSDT'},
    'bitfinex': {'tBTCUSD','tBTCEUR','tBTCUST','tEURUST','tUSTUSD','tUDCUSD'},
    'hyperliquid': {'BTC','ETH','SOL','HYPE'},
}
SOURCES = {'binance':'binance_klines', 'bitfinex':'bitfinex_candles',
           'hyperliquid':'hyperliquid_candles'}


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'),
                       allow_nan=False) + '\n').encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def safe(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Symlink path refused')
    return path


def write_new(path, raw):
    path = safe(path)
    with path.open('xb') as f:
        f.write(raw); f.flush(); os.fsync(f.fileno())


def read_bound(path, limit):
    with safe(path).open('rb') as f:
        raw = f.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('Oversized evidence file')
    return raw


def number(value, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError('Invalid numeric type')
    if len(str(value)) > 80:
        raise ValueError('Oversized number')
    try:
        n = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('Invalid number') from exc
    if not n.is_finite() or abs(n) > Decimal('1e25') or (n <= 0 if positive else n < 0):
        raise ValueError('Invalid market value')
    if n.as_tuple().exponent < -25:
        raise ValueError('Excess numeric precision')
    return format(n, 'f')


def stamp(value):
    if type(value) is not int or not 0 <= value <= 4_102_444_800_000:
        raise ValueError('Invalid millisecond timestamp')
    return value


def identity(venue, symbol, start, end, now):
    if venue not in PAIRS or symbol not in PAIRS[venue]:
        raise ValueError('Unreviewed venue/instrument')
    for v in (start, end, now): stamp(v)
    if start % MINUTE or end % MINUTE or not 0 < end-start <= MAX_SPAN or end > now//MINUTE*MINUTE:
        raise ValueError('Window must be closed, minute aligned, and at most 35 days')


def request_spec(venue, symbol, start, end):
    if venue == 'binance':
        params = {'symbol':symbol,'interval':'1m','startTime':start,'endTime':end-1,'limit':1000}
        return {'url':'https://api.binance.com/api/v3/klines?'+urllib.parse.urlencode(params),'body':None}
    if venue == 'bitfinex':
        params = {'start':start,'end':end-1,'limit':10000,'sort':1}
        return {'url':f'https://api-pub.bitfinex.com/v2/candles/trade:1m:{symbol}/hist?'+urllib.parse.urlencode(params),'body':None}
    return {'url':'https://api.hyperliquid.xyz/info',
            'body':{'type':'candleSnapshot','req':{'coin':symbol,'interval':'1m','startTime':start,'endTime':end-1}}}


def fetch_public(spec):
    data = encode(spec['body']) if spec['body'] is not None else None
    req = urllib.request.Request(spec['url'], data=data,
                                 headers={'User-Agent':'Sniper-History-Recovery/1','Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=20) as r:
        if r.geturl() != spec['url']:
            raise ValueError('Unexpected redirect')
        raw = r.read(MAX_BODY+1)
    if len(raw)>MAX_BODY:
        raise ValueError('Response too large')
    return raw


def normalize(raw, venue, symbol, start, end):
    data = json.loads(raw)
    if not isinstance(data,list) or len(data)>10000:
        raise ValueError('Unexpected response shape')
    out=[]; previous=start-MINUTE
    for item in data:
        if venue == 'binance':
            if not isinstance(item,list) or len(item)!=12: raise ValueError('Invalid Binance candle')
            t=stamp(item[0]); close_t=stamp(item[6]); values=item[1:6]
            quote=number(item[7]); trades=item[8]
        elif venue == 'bitfinex':
            if not isinstance(item,list) or len(item)!=6: raise ValueError('Invalid Bitfinex candle')
            t=stamp(item[0]); close_t=t+MINUTE-1
            values=[item[1],item[3],item[4],item[2],item[5]]
            quote=None; trades=None  # This endpoint does not supply these fields.
        else:
            if not isinstance(item,dict) or item.get('s')!=symbol or item.get('i')!='1m':
                raise ValueError('Wrong Hyperliquid instrument/interval')
            t=stamp(item['t']); close_t=stamp(item['T'])
            values=[item[k] for k in ('o','h','l','c','v')]; quote=None; trades=item['n']
        if t % MINUTE or not start<=t<end or t<=previous or close_t!=t+MINUTE-1:
            raise ValueError('Candle chronology/window/interval mismatch')
        if trades is not None and (type(trades) is not int or not 0<=trades<=2147483647):
            raise ValueError('Invalid trade count')
        vals=[number(v, i<4) for i,v in enumerate(values)]
        o,h,l,c,v=map(Decimal,vals)
        if h<max(o,c) or l>min(o,c) or h<l:
            raise ValueError('Invalid OHLC bounds')
        out.append(dict(zip(('open','high','low','close','volume'),vals),
                        open_ms=t,close_ms=close_t,quote_volume=quote,trades_count=trades,
                        symbol=symbol+'-PERP' if venue=='hyperliquid' else symbol,src=SOURCES[venue]))
        previous=t
    return out


def holes(rows,start,end):
    result=[]; cursor=start
    for row in rows:
        if cursor<row['open_ms']: result.append([cursor,row['open_ms']])
        cursor=row['open_ms']+MINUTE
    if cursor<end: result.append([cursor,end])
    return result


def stage(venue,symbol,start,end,out,fetch=fetch_public,now=None,delay=None):
    now=int(time.time()*1000) if now is None else now
    identity(venue,symbol,start,end,now)
    out=safe(out); out.mkdir(mode=0o700,parents=False,exist_ok=False)
    # Fixed time pages prevent short/sparse responses from skipping an interval.
    size=1000 if venue=='binance' else 5000
    pages=[]; rows=[]
    write_new(out/'STARTED.json',encode({'venue':venue,'symbol':symbol,'start_ms':start,'end_ms':end}))
    for index,a in enumerate(range(start,end,size*MINUTE)):
        b=min(a+size*MINUTE,end); spec=request_spec(venue,symbol,a,b)
        raw=fetch(spec)
        if not isinstance(raw,bytes) or len(raw)>MAX_BODY: raise ValueError('Invalid raw response')
        name=f'raw-{index:03d}.json'; write_new(out/name,raw)
        # Preserve a malformed public response for diagnosis, but never publish
        # a completed manifest for an invalid/partially downloaded bundle.
        page_rows=normalize(raw,venue,symbol,a,b)
        pages.append({'file':name,'sha256':sha(raw),'request':spec,'start_ms':a,'end_ms':b,'rows':len(page_rows)})
        rows.extend(page_rows)
        if b<end: time.sleep((7 if venue=='bitfinex' else 0.25) if delay is None else delay)
    missing=holes(rows,start,end)
    record={'schema':1,'venue':venue,'symbol':symbol,'source':SOURCES[venue],
            'start_ms':start,'end_ms':end,'observed_ms':now,'pages':pages,'rows':len(rows),
            'missing_intervals':missing,'missing_minutes':sum((b-a)//MINUTE for a,b in missing),
            'status':'COMPLETE' if not missing else 'INCOMPLETE',
            'qualification':'UNQUALIFIED','funding_settlement':False}
    write_new(out/'manifest.json',encode(record))
    return dict(record,manifest_sha256=sha(encode(record)))


def verify_bundle(folder,expected_digest):
    folder=safe(folder); raw=read_bound(folder/'manifest.json',1_000_000)
    if not re.fullmatch('[a-f0-9]{64}',expected_digest) or sha(raw)!=expected_digest:
        raise ValueError('Manifest digest mismatch')
    m=json.loads(raw)
    if m['schema']!=1 or m['source']!=SOURCES[m['venue']]: raise ValueError('Unknown source/schema')
    identity(m['venue'],m['symbol'],m['start_ms'],m['end_ms'],m['observed_ms'])
    if m['observed_ms']>int(time.time()*1000)+60000: raise ValueError('Future observation')
    rows=[]; cursor=m['start_ms']; size=1000 if m['venue']=='binance' else 5000
    expected_pages=(m['end_ms']-cursor+size*MINUTE-1)//(size*MINUTE)
    if len(m['pages'])!=expected_pages: raise ValueError('Missing evidence page')
    for i,page in enumerate(m['pages']):
        end=min(cursor+size*MINUTE,m['end_ms'])
        if page['file']!=f'raw-{i:03d}.json' or page['start_ms']!=cursor or page['end_ms']!=end:
            raise ValueError('Invalid evidence page identity')
        if page['request']!=request_spec(m['venue'],m['symbol'],cursor,end): raise ValueError('Wrong source request')
        data=read_bound(folder/page['file'],MAX_BODY)
        if sha(data)!=page['sha256']: raise ValueError('Raw response hash mismatch')
        parsed=normalize(data,m['venue'],m['symbol'],cursor,end)
        if len(parsed)!=page['rows']: raise ValueError('Wrong page count')
        rows.extend(parsed); cursor=end
    missing=holes(rows,m['start_ms'],m['end_ms'])
    if (len(rows)!=m['rows'] or missing!=m['missing_intervals'] or
        sum((b-a)//MINUTE for a,b in missing)!=m['missing_minutes'] or
        m['status']!=('COMPLETE' if not missing else 'INCOMPLETE')):
        raise ValueError('Coverage mismatch')
    return m,rows


def import_sql(folder,digest):
    m,rows=verify_bundle(folder,digest)
    if not rows: raise ValueError('No authentic rows to import')
    # Identity strings come exclusively from the exact allowlists above. Numeric
    # values are canonical Decimal strings and cannot carry executable SQL.
    statements=["\\set ON_ERROR_STOP on",'BEGIN;',"SET LOCAL lock_timeout='10s';",
                "SET LOCAL statement_timeout='120s';",
                'LOCK TABLE market_klines IN SHARE ROW EXCLUSIVE MODE;',
                'CREATE TEMP TABLE history_candidate (LIKE market_klines INCLUDING DEFAULTS) ON COMMIT DROP;']
    columns='open_time,symbol,src,open,high,low,close,volume,quote_volume,trades_count,close_time'
    for a in range(0,len(rows),500):
        vals=[]
        for r in rows[a:a+500]:
            n=[r[k] if r[k] is not None else 'NULL' for k in ('open','high','low','close','volume','quote_volume','trades_count')]
            vals.append(f"(to_timestamp({r['open_ms']}/1000.0),'{r['symbol']}','{r['src']}',"+
                        ','.join(map(str,n))+f",to_timestamp({r['close_ms']}/1000.0))")
        statements.append('INSERT INTO history_candidate ('+columns+') VALUES '+','.join(vals)+';')
    statements.append('''DO $guard$ BEGIN
      IF EXISTS (SELECT 1 FROM history_candidate c JOIN market_klines m
          USING(symbol,src,open_time)
          WHERE (c.open,c.high,c.low,c.close,c.volume,c.close_time)
          IS DISTINCT FROM (m.open,m.high,m.low,m.close,m.volume,m.close_time)) THEN
        RAISE EXCEPTION 'Conflicting existing candle; no rows changed';
      END IF;
    END $guard$;''')
    statements.append('WITH inserted AS (INSERT INTO market_klines ('+columns+') SELECT '+columns+
                      ' FROM history_candidate ON CONFLICT (symbol,src,open_time) DO NOTHING RETURNING *) '
                      "SELECT json_build_object('bundle_sha256','"+digest+"','inserted',count(*),"
                      "'rows',coalesce(json_agg(inserted),'[]'::json)) FROM inserted;")
    statements.append('COMMIT;')
    return '\n'.join(statements)+'\n'


def parse_time(value):
    dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    if dt.utcoffset() is None: raise ValueError('Explicit UTC offset required')
    return int(dt.timestamp()*1000)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__); sub=p.add_subparsers(dest='command',required=True)
    f=sub.add_parser('fetch'); f.add_argument('--venue',choices=PAIRS,required=True)
    f.add_argument('--symbol',required=True);f.add_argument('--start',required=True);f.add_argument('--end',required=True)
    f.add_argument('--out',type=Path,required=True)
    q=sub.add_parser('sql'); q.add_argument('--bundle',type=Path,required=True)
    q.add_argument('--sha256',required=True);q.add_argument('--out',type=Path,required=True)
    args=p.parse_args(argv)
    try:
        if args.command=='fetch':
            r=stage(args.venue,args.symbol,parse_time(args.start),parse_time(args.end),args.out)
            print(json.dumps({k:v for k,v in r.items() if k not in ('pages','missing_intervals')}))
            return 0 if r['status']=='COMPLETE' else 2
        sql=import_sql(args.bundle,args.sha256);write_new(args.out,sql.encode())
        print(json.dumps({'status':'PREPARED_NOT_EXECUTED','sql_sha256':sha(sql.encode())}));return 0
    except urllib.error.HTTPError as e:
        result={'status':'RATE_LIMITED' if e.code==429 else 'BLOCKED','http_status':e.code}
        retry=e.headers.get('Retry-After','') if e.headers else ''
        if retry.isdigit() and len(retry)<7: result['retry_after_seconds']=int(retry)
        print(json.dumps(result));return 2
    except (ValueError,KeyError,OSError) as e:
        print(json.dumps({'status':'BLOCKED','error':type(e).__name__}));return 2


if __name__=='__main__': raise SystemExit(main())
