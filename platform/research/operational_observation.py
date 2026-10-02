#!/usr/bin/env python3
"""Publish bounded public research observations, never private input/state files.

The digest detects interrupted/tampered observations, not an untrusted producer.
Root-owned code and a dedicated producer account form the operational trust boundary.
Observations are reporting inputs only and cannot authorize trading/promotion.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

LIMIT = 131072
MAX_AGE = 600


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def checked(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Unsafe observation path')
    if path.exists() and path.is_file() and path.stat().st_nlink != 1:
        raise ValueError('Observation must not be hardlinked')
    return path


def stamp(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None: raise ValueError('Timestamp requires timezone')
    return dt.astimezone(timezone.utc)


def public_candle(data):
    if data['status'] not in ('OBSERVED', 'NOT_STARTED'): raise ValueError('Unknown source status')
    rows = data['runs']
    if not isinstance(rows, list) or len(rows) > 18: raise ValueError('Invalid run count')
    public = []
    for row in rows:
        candidate = row['candidate']
        if type(candidate) is not int or not 0 <= candidate < 18: raise ValueError('Invalid candidate')
        day = row['day']
        if not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
            raise ValueError('Invalid run day')
        state = row['status']
        if state not in ('BASELINE_SCREEN_COMPLETE', 'TRAINING_SCREEN_COMPLETE', 'BLOCKED', 'STARTED'):
            raise ValueError('Invalid run state')
        item = {'candidate': candidate, 'day': day, 'status': state}
        for field in ('policy_sha256', 'model_sha256'):
            digest = row[field]
            if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
                raise ValueError('Invalid source fingerprint')
            item[field] = digest
        if state.endswith('SCREEN_COMPLETE'):
            item['holdout_used'] = row['holdout_used']
            if type(item['holdout_used']) is not bool: raise ValueError('Invalid holdout flag')
            item['result'] = {}
            for part in ('training', 'holdout') if item['holdout_used'] else ('training',):
                flag = row['result'][part]['net_positive_under_assumptions']
                if type(flag) is not bool: raise ValueError('Invalid profitability observation')
                item['result'][part] = {'net_positive_under_assumptions': flag}
        public.append(item)
    return {'status': data['status'], 'runs': public, 'live_eligible': 0, 'paper_qualified': 0}


AUDIT_SQL = """BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout='15s';
WITH bounds AS (SELECT date_trunc('minute',now()) AS cutoff),
k AS (SELECT m.*,lag(open_time) OVER(PARTITION BY src,symbol ORDER BY open_time) AS previous
 FROM market_klines m,bounds WHERE src IN ('hyperliquid_candles','hyperliquid_spot_candles')
 AND open_time>=cutoff-interval '35 days' AND open_time<cutoff),
series AS (SELECT src,symbol,count(*) AS candles,min(open_time) AS first_open,max(open_time) AS last_open,
 count(*) FILTER(WHERE previous IS NOT NULL AND open_time-previous<>interval '1 minute') AS gap_intervals,
 count(*) FILTER(WHERE open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL OR volume IS NULL
 OR open::text IN ('NaN','Infinity','-Infinity') OR high::text IN ('NaN','Infinity','-Infinity')
 OR low::text IN ('NaN','Infinity','-Infinity') OR close::text IN ('NaN','Infinity','-Infinity')
 OR volume::text IN ('NaN','Infinity','-Infinity') OR open<=0 OR high<=0 OR low<=0 OR close<=0 OR volume<0
 OR low>LEAST(open,close) OR high<GREATEST(open,close) OR high<low
 OR close_time IS NULL OR close_time<open_time OR close_time>=open_time+interval '1 minute'
 OR open_time<>date_trunc('minute',open_time)) AS invalid_candles
 FROM k GROUP BY src,symbol),
fund AS (SELECT src,symbol,count(*) AS observations,min(funding_time) AS first_event,
 max(funding_time) AS last_event FROM market_funding,bounds
 WHERE src LIKE 'hyperliquid%' AND funding_time>=cutoff-interval '35 days' AND funding_time<cutoff
 GROUP BY src,symbol)
SELECT json_build_object('status','OBSERVED','cutoff',(SELECT cutoff FROM bounds),
 'window_days',35,'candles',COALESCE((SELECT json_agg(series ORDER BY src,symbol) FROM series),'[]'::json),
 'funding',COALESCE((SELECT json_agg(fund ORDER BY src,symbol) FROM fund),'[]'::json),
 'qualified',false,'funding_note','Polling observations are not settled historical funding events');
ROLLBACK;
"""


def audit_market():
    env = os.environ.copy()
    env['PGOPTIONS'] = '-c default_transaction_read_only=on -c statement_timeout=15000'
    proc = subprocess.run(['psql', '-X', '-w', '-qAt', '-v', 'ON_ERROR_STOP=1', '-d', 'beroun'],
        input=AUDIT_SQL, text=True, capture_output=True, timeout=25, env=env)
    if proc.returncode: raise RuntimeError('Read-only market audit failed')
    if len(proc.stdout.encode()) > LIMIT: raise ValueError('Audit exceeds output bound')
    return json.loads(proc.stdout)


def build(root, now, auditor=audit_market):
    from candle_research import status
    body = {'schema': 1, 'observed_at': now.astimezone(timezone.utc).isoformat(),
        'purpose': 'REPORTING_ONLY_NO_PROMOTION_AUTHORITY', 'candle': public_candle(status(root)),
        'market_data': auditor()}
    return {'body': body, 'sha256': hashlib.sha256(encode(body)).hexdigest()}


def load(path, now):
    path = checked(path)
    if not path.is_file() or path.stat().st_size > LIMIT: raise ValueError('Observation missing or oversized')
    raw = path.read_bytes()
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError('Duplicate observation field')
            result[key] = value
        return result
    data = json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite observation')))
    body = data['body']
    if data['sha256'] != hashlib.sha256(encode(body)).hexdigest(): raise ValueError('Observation digest mismatch')
    age = (now.astimezone(timezone.utc) - stamp(body['observed_at'])).total_seconds()
    if age < -30 or age > MAX_AGE: raise ValueError('Observation stale or future dated')
    if body['schema'] != 1 or body['purpose'] != 'REPORTING_ONLY_NO_PROMOTION_AUTHORITY':
        raise ValueError('Unsupported observation contract')
    if public_candle(body['candle']) != body['candle']: raise ValueError('Unexpected public fields')
    if body['market_data']['status'] != 'OBSERVED' or body['market_data']['qualified'] is not False:
        raise ValueError('Unverified data audit')
    return body


def publish(path, observation):
    path = checked(path); checked(path.parent)
    raw = encode(observation)
    if len(raw) > LIMIT: raise ValueError('Observation exceeds bound')
    fd, temporary = tempfile.mkstemp(prefix='.observation-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o644)
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        checked(path); os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--state-dir', type=Path, default=Path('/var/lib/sniper/candle-research'))
    ap.add_argument('--output', type=Path, default=Path('/var/lib/sniper/observations/candle.json'))
    args = ap.parse_args()
    import fcntl
    with checked(args.output.parent / 'producer.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        now = datetime.now(timezone.utc)
        data = build(args.state_dir, now)
        publish(args.output, data)
        load(args.output, now)
    print(json.dumps({'status': 'PUBLISHED', 'sha256': data['sha256'], 'observed_at': now.isoformat()}))


if __name__ == '__main__': main()
