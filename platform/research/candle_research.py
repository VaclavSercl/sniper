#!/usr/bin/env python3
"""Bounded T1 spot research. No exchange connection, order or promotion API.

One fixed baseline gets one historical holdout; subsequent daily hypotheses
only see the frozen training interval. This short candle screen cannot satisfy
the legacy annual-data gate or establish executable performance.
"""
import argparse
import csv
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import io
import itertools
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess

BASELINE = {'n_breakout': 240, 'vol_q': 0.5, 'trail': 2.0}
GRID = [BASELINE] + [dict(n_breakout=n, vol_q=q, trail=t)
    for n, q, t in itertools.product((120, 240, 480), (0.5, 0.7), (1.5, 2.0, 3.0))
    if dict(n_breakout=n, vol_q=q, trail=t) != BASELINE]
MINUTE = 60000
DAY = 86400000
FIELDS = ['ts', 'open', 'high', 'low', 'close', 'volume']
MODEL_PATH = Path(__file__).resolve()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def safe(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Symlink refused')
    return path


def durable_new(path, raw):
    path = safe(path)
    with path.open('xb') as stream:
        os.chmod(path, 0o600)
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    if os.name == 'posix':
        fd = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)


def retain(root, raw, suffix):
    folder = safe(root / 'inputs'); folder.mkdir(mode=0o700, exist_ok=True)
    name = digest(raw) + suffix
    target = safe(folder / name)
    if target.exists():
        if target.read_bytes() != raw: raise ValueError('Retained input corrupted')
    else: durable_new(target, raw)
    return name


def stamp(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.utcoffset() is None or dt.utcoffset().total_seconds() != 0:
        raise ValueError('Explicit UTC interval required')
    ms = int(dt.timestamp() * 1000)
    if ms % DAY or dt.microsecond: raise ValueError('UTC day boundary required')
    return ms


def policy(raw, now):
    p = json.loads(raw)
    required = {'schema', 'source', 'symbol', 'start', 'training_end', 'holdout_end',
        'initial_quote_capital', 'fee_per_side', 'slippage_per_side',
        'position_fraction', 'risk_fraction', 'cost_basis', 'scope'}
    if set(p) != required or p['schema'] != 1 or p['scope'] != 'EXPLORATORY_ONLY':
        raise ValueError('Unsupported explicit research policy')
    if p['source'] != 'binance_klines' or p['symbol'] != 'BTCUSDT':
        raise ValueError('T1 requires exact Binance BTCUSDT spot identity')
    for key in ('initial_quote_capital', 'fee_per_side', 'slippage_per_side', 'position_fraction', 'risk_fraction'):
        v = p[key]
        if isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v):
            raise ValueError('Invalid policy number')
    if not (p['initial_quote_capital'] > 0 and 0 <= p['fee_per_side'] < 0.02
            and 0 <= p['slippage_per_side'] < 0.02
            and 0 < p['position_fraction'] <= 1 and 0 < p['risk_fraction'] <= 0.01):
        raise ValueError('Invalid research capital/cost/risk assumptions')
    if not isinstance(p['cost_basis'], str) or not p['cost_basis'].strip():
        raise ValueError('Missing cost provenance')
    start, middle, end = (stamp(p[k]) for k in ('start', 'training_end', 'holdout_end'))
    if not (21 * DAY <= middle - start <= 60 * DAY and 7 * DAY <= end - middle <= 14 * DAY):
        raise ValueError('Unsupported bounded training/holdout window')
    if end > int(now.timestamp() * 1000): raise ValueError('Future holdout')
    return p


def parse_candles(raw, start, end):
    if len(raw) > 32_000_000: raise ValueError('Oversized market snapshot')
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8')))
    if reader.fieldnames != FIELDS: raise ValueError('Unexpected market schema')
    rows = []
    for item in reader:
        if set(item) != set(FIELDS) or None in item.values(): raise ValueError('Malformed row')
        ts = int(item['ts'])
        if ts != start + len(rows) * MINUTE or ts >= end:
            raise ValueError('Missing, duplicate, unaligned or unordered minute')
        row = {'ts': ts}
        for key in FIELDS[1:]:
            value = float(item[key])
            if not math.isfinite(value) or value < 0 or (key != 'volume' and value == 0):
                raise ValueError('Invalid market number')
            row[key] = value
        if row['low'] > min(row['open'], row['close']) or row['high'] < max(row['open'], row['close']):
            raise ValueError('Invalid OHLC')
        rows.append(row)
    if len(rows) != (end - start) // MINUTE: raise ValueError('Incomplete historical window')
    return rows


def export_candles(p, end):
    start = stamp(p['start'])
    # Only constants and validated integers enter SQL; no credential or account query.
    query = """BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout='30s'; SET LOCAL timezone='UTC';
COPY (SELECT (extract(epoch FROM open_time)*1000)::bigint AS ts,
open,high,low,close,volume FROM public.market_klines
WHERE src='binance_klines' AND symbol='BTCUSDT'
AND open_time>=to_timestamp(%d/1000.0) AND open_time<to_timestamp(%d/1000.0)
ORDER BY open_time LIMIT %d) TO STDOUT WITH (FORMAT CSV, HEADER true);
COMMIT;
""" % (start, end, (end - start) // MINUTE + 1)
    r = subprocess.run(['psql', '-X', '-w', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=1',
        '--host', '/var/run/postgresql', '--username', 'beroun',
        '-d', 'beroun', '-f', '-'], input=query.encode(), capture_output=True, timeout=40)
    if r.returncode: raise RuntimeError('Read-only market export failed; exit ' + str(r.returncode))
    parse_candles(r.stdout, start, end)
    return r.stdout


def signals(rows, params):
    """T1 legacy signal definition, with cached range averages; closed bars only."""
    n = params['n_breakout']; q = params['vol_q']
    if n not in (120, 240, 480) or q not in (0.5, 0.7) or params['trail'] not in (1.5, 2.0, 3.0):
        raise ValueError('Unregistered hypothesis')
    prefix = [0.0]
    for r in rows: prefix.append(prefix[-1] + r['high'] - r['low'])
    atr = [(prefix[i + 1] - prefix[i + 1 - 60]) / 60 if i >= 59 else None for i in range(len(rows))]
    result = set()
    for i in range(max(n, 1440), len(rows) - 1):
        if rows[i]['close'] <= max(r['high'] for r in rows[i - n:i]) * 1.001: continue
        sample = [atr[j] for j in range(i - 1440, i + 1, 60)]
        if any(a is None for a in sample): continue
        sample.sort()
        if atr[i] > sample[min(int(q * len(sample)), len(sample) - 1)]: result.add(i)
    return result, atr


def simulate(rows, params, p, start, end):
    """Long-only, next-open execution and stop levels fixed before each candle.

    No leverage or rebates. Stop gaps fill at the worse open. A current high can
    only tighten the next candle's stop. All equity dates, including flat days,
    stay in chronological order. Terminal inventory is liquidated with costs.
    """
    sigs, atr = signals(rows, params)
    cash = initial = float(p['initial_quote_capital'])
    fee, slip = p['fee_per_side'], p['slippage_per_side']
    quantity = 0.0; stop = best = 0.0; entry_i = -1; entry_cost = 0.0
    fees = turnover = 0.0; trades = []; equity = []; peak = initial; max_dd = 0.0
    sell_next = False
    indexes = [i for i, r in enumerate(rows) if start <= r['ts'] < end]
    if not indexes or rows[indexes[0]]['ts'] != start or rows[indexes[-1]]['ts'] != end - MINUTE:
        raise ValueError('Partition not covered')

    def mark(value):
        nonlocal peak, max_dd
        peak = max(peak, value); max_dd = max(max_dd, peak - value)

    def close_position(reference, i, reason):
        nonlocal cash, quantity, fees, turnover, sell_next
        price = reference * (1 - slip)
        amount = quantity * price; charge = amount * fee
        cash += amount - charge; fees += charge; turnover += amount
        trades.append({'entry_ms': rows[entry_i]['ts'], 'exit_ms': rows[i]['ts'],
            'entry_cost': entry_cost, 'exit_net': amount - charge,
            'pnl': amount - charge - entry_cost, 'reason': reason,
            'exit_reference': reference, 'quantity': quantity})
        quantity = 0.0; sell_next = False; mark(cash)

    for i in indexes:
        r = rows[i]; exited = False
        if quantity and sell_next:
            close_position(r['open'], i, 'NEXT_OPEN_EXIT'); exited = True
        if not quantity and not exited and i > 0 and i - 1 in sigs:
            a = atr[i - 1]
            stop = rows[i - 1]['close'] - params['trail'] * a
            price = r['open'] * (1 + slip)
            # A gap below the already-known protective stop invalidates entry.
            if stop > 0 and r['open'] > stop:
                loss_per_unit = price * (1 + fee) - stop * (1 - slip) * (1 - fee)
                quantity = min(cash * p['position_fraction'] / (price * (1 + fee)),
                    cash * p['risk_fraction'] / loss_per_unit)
                entry_cost = quantity * price * (1 + fee)
                cash -= entry_cost; fees += quantity * price * fee; turnover += quantity * price
                entry_i = i; best = r['open']
        if quantity:
            if r['low'] <= stop:
                close_position(min(r['open'], stop), i, 'STOP'); exited = True
            else:
                mark(cash + quantity * r['low'] * (1 - slip) * (1 - fee))
                best = max(best, r['high'])
                if atr[i] is not None: stop = max(stop, best - params['trail'] * atr[i])
                prior_low = min(x['low'] for x in rows[max(0, i - 120):i]) if i else r['low']
                sell_next = r['close'] < prior_low or i - entry_i + 1 >= 1440
        value = cash + quantity * r['close'] * (1 - slip) * (1 - fee)
        mark(value); equity.append((r['ts'], value))
        if cash < -1e-8 or not math.isfinite(value): raise RuntimeError('Invalid simulated capital')
    if quantity:
        close_position(rows[indexes[-1]]['close'], indexes[-1], 'PARTITION_END')
        equity[-1] = (equity[-1][0], cash)
    daily = {}
    for ts, value in equity: daily[ts // DAY] = value
    daily_returns = []; previous = initial
    for day, value in sorted(daily.items()):
        daily_returns.append({'day': day, 'return': value / previous - 1, 'equity': value})
        previous = value
    net = cash - initial
    if abs(sum(t['pnl'] for t in trades) - net) > 1e-7: raise RuntimeError('PnL reconciliation failed')
    return {'net_pnl_quote': net, 'return_fraction': net / initial, 'final_quote_equity': cash,
        'fees_quote': fees, 'turnover_quote': turnover, 'max_drawdown_quote': max_dd,
        'trades': trades, 'trade_count': len(trades), 'daily_returns': daily_returns,
        'candles': len(indexes), 'start_ms': start, 'end_ms': end,
        'net_positive_under_assumptions': net > 0, 'paper_eligible': False, 'live_eligible': False}


def evaluate(raw, p, params, baseline):
    first, middle, end = (stamp(p[k]) for k in ('start', 'training_end', 'holdout_end'))
    rows = parse_candles(raw, first, end if baseline else middle)
    # Never expose holdout candles to the training simulator.
    training = [r for r in rows if r['ts'] < middle]
    result = {'scope': 'EXPLORATORY_ONLY', 'source': p['source'], 'symbol': p['symbol'],
        'params': params, 'assumptions': p, 'paper_eligible': False, 'live_eligible': False,
        'training': simulate(training, params, p, first + 2 * DAY, middle),
        'holdout': None, 'limitations': ['SHORT_HISTORY_ANNUAL_GATE_NOT_MET',
            'UNVERIFIED_CANDLE_EXECUTION', 'HYPOTHETICAL_COSTS_NOT_ACCOUNT_FEES',
            'NO_INDEPENDENT_STATISTICAL_QUALIFICATION']}
    if baseline:
        # Warmup ends before the partition; positions start flat in each partition.
        result['holdout'] = simulate(rows, params, p, middle, end)
        result['status'] = 'BASELINE_SCREEN_COMPLETE'
    else: result['status'] = 'TRAINING_SCREEN_COMPLETE'
    return result


def connect(root):
    root = safe(root); root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = safe(root / 'candle-research.sqlite3')
    con = sqlite3.connect(path, timeout=2)
    con.execute('PRAGMA synchronous=FULL')
    con.executescript('''CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs(day TEXT PRIMARY KEY,candidate INTEGER UNIQUE NOT NULL,
        status TEXT NOT NULL,record TEXT NOT NULL);''')
    return con


def cycle(root, policy_raw, now, exporter=export_candles):
    """Caller holds process lock. STARTED survives a crash; no automatic adoption."""
    p = policy(policy_raw, now); root = safe(root)
    with closing(connect(root)) as con:
        fingerprint = digest(policy_raw)
        prior = con.execute("SELECT value FROM settings WHERE key='policy'").fetchone()
        if prior and prior[0] != fingerprint: raise ValueError('Pinned policy changed; separate review required')
        unfinished = con.execute("SELECT day FROM runs WHERE status='STARTED'").fetchone()
        if unfinished: raise ValueError('Interrupted run requires reconciliation: ' + unfinished[0])
        day = now.date().isoformat()
        row = con.execute('SELECT status,record FROM runs WHERE day=?', (day,)).fetchone()
        if row: return {'status': 'ALREADY_RECORDED', 'record': json.loads(row[1])}
        used = {r[0] for r in con.execute('SELECT candidate FROM runs')}
        candidate = next((n for n in range(len(GRID)) if n not in used), None)
        if candidate is None: return {'status': 'SEARCH_SPACE_EXHAUSTED', 'count': len(GRID)}
        # A failed baseline must be reconciled, not silently bypassed by another day.
        if candidate and con.execute("SELECT 1 FROM runs WHERE candidate=0 AND status='BLOCKED'").fetchone():
            raise ValueError('Blocked baseline requires explicit repair/reconciliation')
        model = MODEL_PATH.read_bytes()
        record = {'schema': 1, 'day': day, 'candidate': candidate, 'params': GRID[candidate],
            'started_at': now.isoformat(), 'policy_sha256': fingerprint,
            'model_sha256': digest(model), 'holdout_used': candidate == 0,
            'status': 'STARTED', 'paper_eligible': False, 'live_eligible': False}
        con.execute('INSERT OR IGNORE INTO settings VALUES(?,?)', ('policy', fingerprint))
        con.execute('INSERT INTO runs VALUES(?,?,?,?)', (day, candidate, 'STARTED', encode(record).decode()))
        con.commit()
        try:
            record['policy_input'] = retain(root, policy_raw, '.json')
            record['model_input'] = retain(root, model, '.py')
            end = stamp(p['holdout_end'] if candidate == 0 else p['training_end'])
            raw = exporter(p, end)
            record['data_input'] = retain(root, raw, '.csv')
            record['result'] = evaluate(raw, p, GRID[candidate], candidate == 0)
            record['status'] = record['result']['status']
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            record['status'] = 'BLOCKED'; record['error_type'] = type(exc).__name__
            # Fixed error categories; do not retain connection strings or secrets.
        record['finished_at'] = datetime.now(timezone.utc).isoformat()
        report = safe(root / ('result-' + day + '.json'))
        durable_new(report, encode(record))
        con.execute('UPDATE runs SET status=?,record=? WHERE day=?', (record['status'], encode(record).decode(), day))
        con.commit()
        return record


def status(root):
    path = safe(Path(root) / 'candle-research.sqlite3')
    if not path.is_file(): return {'status': 'NOT_STARTED', 'runs': [], 'live_eligible': 0}
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as con:
        rows = [json.loads(r[0]) for r in con.execute('SELECT record FROM runs ORDER BY day')]
    return {'status': 'OBSERVED', 'runs': rows, 'live_eligible': 0, 'paper_qualified': 0}


def reproduce(root, day):
    record = next(r for r in status(root)['runs'] if r['day'] == day)
    if record['status'] not in ('BASELINE_SCREEN_COMPLETE', 'TRAINING_SCREEN_COMPLETE'):
        raise ValueError('No completed result to reproduce')
    if digest(MODEL_PATH.read_bytes()) != record['model_sha256']:
        raise ValueError('Use the recorded model revision in an isolated checkout')
    inputs = {}
    for key in ('policy_input', 'model_input', 'data_input'):
        name = record[key]
        if Path(name).name != name: raise ValueError('Unsafe recorded input')
        raw = safe(Path(root) / 'inputs' / name).read_bytes()
        if digest(raw) != name.split('.')[0]: raise ValueError('Retained input changed')
        inputs[key] = raw
    if digest(inputs['policy_input']) != record['policy_sha256']: raise ValueError('Policy record mismatch')
    p = policy(inputs['policy_input'], datetime.fromisoformat(record['started_at']))
    result = evaluate(inputs['data_input'], p, record['params'], record['holdout_used'])
    if encode(result) != encode(record['result']): raise ValueError('Reproduction differs')
    return {'status': 'REPRODUCED', 'day': day, 'result_sha256': digest(encode(result))}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=['cycle', 'status', 'reproduce'])
    ap.add_argument('--state-dir', type=Path, required=True)
    ap.add_argument('--policy', type=Path); ap.add_argument('--day')
    args = ap.parse_args(); os.umask(0o077)
    root = safe(args.state_dir)
    if args.action == 'cycle':
        if not args.policy: ap.error('--policy required')
        import fcntl  # Production scheduler is Linux; no cross-platform claim.
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with safe(root / 'cycle.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = cycle(root, safe(args.policy).read_bytes(), datetime.now(timezone.utc))
    elif args.action == 'status': result = status(root)
    else:
        if not args.day: ap.error('--day required')
        result = reproduce(root, args.day)
    print(json.dumps(result, indent=2, allow_nan=False))
    state = result.get('record', result).get('status')
    return 2 if state in ('BLOCKED', 'STARTED') else 0


if __name__ == '__main__': raise SystemExit(main())
