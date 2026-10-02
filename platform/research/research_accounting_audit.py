"""Independent read-only accounting audit of completed frozen research screens.

Reconcile stored assumed fills against a reference cash/inventory ledger. Never
invoke the simulator, evaluate a signal, consume a holdout or qualify execution.
Source/epoch trust remains the existing root-owned research boundary; this is
an independent arithmetic check, not proof against an untrusted state producer.
"""
from contextlib import closing
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
import hashlib
import json
import math
from pathlib import Path
import sqlite3

HOUR = 3600000
DAY = 24 * HOUR
MAX_ATTEMPTS = 36
MAX_RECORD = 8 * 1024 * 1024
COMPLETE_PHASES = {
    'SCREEN_REJECTED_TRAINING': ('train',),
    'SCREEN_REJECTED_VALIDATION': ('train', 'validation'),
    'SCREEN_REJECTED_HOLDOUT': ('train', 'validation', 'holdout'),
    'EXPLORATORY_POSITIVE_UNQUALIFIED': ('train', 'validation', 'holdout'),
}


def number(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or value.strip() != value:
        raise ValueError('Invalid accounting decimal')
    result = Decimal(value)
    if not result.is_finite() or abs(result) > Decimal('1e18') or result.as_tuple().exponent < -64:
        raise ValueError('Unbounded accounting decimal')
    return result


def integer(value):
    if type(value) is not int or value < 0:
        raise ValueError('Invalid accounting integer')
    return value


def unique(items):
    data = {}
    for key, value in items:
        if key in data:
            raise ValueError('Duplicate evidence key')
        data[key] = value
    return data


def decoded(raw):
    if not isinstance(raw, str) or len(raw.encode()) > MAX_RECORD:
        raise ValueError('Research evidence exceeds bound')
    def nonfinite(_):
        raise ValueError('Nonfinite research evidence')
    return json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite)


def stage_fingerprint(stages):
    if not isinstance(stages, list) or len(stages) > MAX_ATTEMPTS:
        raise ValueError('Unbounded research report')
    raw = json.dumps(sorted(stages, key=lambda row: row['variant']),
                     sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def reported_stage(row):
    record = decoded(row[5])
    record.update(status=row[3], holdout_consumed=bool(row[4]))
    return {k: record.get(k) for k in ('variant', 'family', 'market', 'status',
            'holdout_consumed', 'error_type', 'missing_evidence')} | {
        'net_by_phase': {phase: value['normal']['net'] for phase, value in record['phases'].items()},
        'failures_by_phase': {phase: value['failures'] for phase, value in record['phases'].items()},
        'paper_eligible': False, 'live_eligible': False}


def audit_leg(bars, start, end, result, fee, slippage, initial, allocation):
    """Replay only persisted orders; check every hourly mark including flat days."""
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        return _audit_leg(bars, start, end, result, fee, slippage, initial, allocation)


def _audit_leg(bars, start, end, result, fee, slippage, initial, allocation):
    if (type(start) is not int or type(end) is not int or not 48 <= start < end <= len(bars)
            or end - start > 2880):
        raise ValueError('Invalid audited interval')
    fee, slippage, initial, allocation = map(number, (fee, slippage, initial, allocation))
    if not (0 <= fee < Decimal('.1') and 0 <= slippage < Decimal('.1')
            and initial > 0 and 0 < allocation <= 1):
        raise ValueError('Invalid pinned accounting policy')
    if (result['qualified'] is not False or result['execution'] != 'ASSUMED_NEXT_OPEN_UNCALIBRATED'
            or result['equity_resolution'] != '1h_closes_not_tick_drawdown'):
        raise ValueError('Unsupported accounting evidence scope')
    if (number(result['initial_capital']) != initial
            or integer(result['start_ms']) != bars[start]['time']
            or integer(result['end_ms']) != bars[end - 1]['time'] + HOUR):
        raise ValueError('Accounting window differs')
    orders, equity = result['orders'], result['equity']
    if (not isinstance(orders, list) or not isinstance(equity, list)
            or len(orders) > end - start or len(equity) != end - start):
        raise ValueError('Incomplete accounting chronology')
    by_time = {}
    previous_time = None
    for order in orders:
        t = integer(order['time'])
        if t in by_time or previous_time is not None and t <= previous_time:
            raise ValueError('Duplicated or unordered fills')
        if not result['start_ms'] <= t < result['end_ms'] or t % HOUR:
            raise ValueError('Fill outside audited interval')
        by_time[t] = order
        previous_time = t
    cash, inventory, total_fees = initial, Decimal(0), Decimal(0)
    peak, worst, round_trips = initial, Decimal(0), 0
    day_points, day_baseline, previous_equity, daily = 0, None, None, []
    for index in range(start, end):
        row = bars[index]
        t = integer(row['time'])
        if t != result['start_ms'] + (index - start) * HOUR or t % HOUR:
            raise ValueError('Noncontiguous audited bars')
        opened, closed = number(row['open']), number(row['close'])
        if min(opened, closed) <= 0:
            raise ValueError('Invalid audited prices')
        order = by_time.get(t)
        if order is not None:
            side = order['side']
            price, quantity, cost = map(number, (order['price'], order['quantity'], order['fee']))
            if min(price, quantity) <= 0 or cost < 0:
                raise ValueError('Invalid assumed fill')
            expected_price = opened * (1 + slippage if side == 'buy' else 1 - slippage)
            if side not in ('buy', 'sell') or price != expected_price or cost != quantity * price * fee:
                raise ValueError('Fill price or fee differs from pinned assumptions')
            gross = quantity * price
            if side == 'buy':
                budget = min(cash, initial * allocation)
                if (inventory != 0 or index == end - 1 or budget < 10
                        or quantity != budget / (price * (1 + fee))):
                    raise ValueError('Unfunded or inconsistent assumed entry')
                cash -= gross + cost
                inventory = quantity
            else:
                if inventory <= 0 or quantity != inventory:
                    raise ValueError('Unowned or partial assumed exit')
                cash += gross - cost
                inventory = Decimal(0)
                round_trips += 1
            total_fees += cost
        if cash < Decimal('-0.00000001') or inventory < 0:
            raise ValueError('Invalid reference inventory')
        value = cash + inventory * closed
        point = equity[index - start]
        if integer(point['time']) != t or number(point['equity']) != value:
            raise ValueError('Equity differs from independent ledger')
        peak = max(peak, value)
        worst = max(worst, (peak - value) / peak)
        if t % DAY == 0:
            day_points, day_baseline = 0, previous_equity
        day_points += 1
        if t % DAY == 23 * HOUR and day_points == 24 and day_baseline is not None:
            if day_baseline <= 0:
                raise ValueError('Nonpositive daily baseline')
            daily.append((t // DAY, value / day_baseline - 1))
        previous_equity = value
    if inventory != 0:
        raise ValueError('Terminal inventory not closed')
    expected = {'final_equity': cash, 'net': cash - initial, 'fees': total_fees,
                'maximum_closing_drawdown': worst}
    if any(number(result[key]) != value for key, value in expected.items()):
        raise ValueError('Stored accounting metric differs')
    if integer(result['round_trips']) != round_trips:
        raise ValueError('Round-trip count differs')
    days = result['daily_returns']
    if not isinstance(days, list) or len(days) != len(daily):
        raise ValueError('Incomplete daily equity evidence')
    for point, (day, value) in zip(days, daily):
        if integer(point['day']) != day or number(point['return']) != value:
            raise ValueError('Daily return differs')
    positive = sum(value > 0 for _, value in daily)
    n = len(daily)
    # Compute the exact binomial tail independently of protocol.sign_tail.
    term, tail = 1, 0
    for k in range(n + 1):
        if k >= positive:
            tail += term
        if k < n:
            term = term * (n - k) // (k + 1)
    probability = tail / (2 ** n)
    observed = result['sign_test_p']
    if type(observed) not in (int, float) or not math.isfinite(observed) or observed != probability:
        raise ValueError('Stored sign-test probability differs')
    return {'status': 'PASS', 'hourly_marks': end - start, 'orders': len(orders),
            'complete_days': len(daily), 'qualified': False}


def audit_attempt(epoch, epoch_hash, model_hash, row):
    variant, family, market, status, consumed, raw = row
    record = decoded(raw)
    if (variant != family + '/' + market or market not in epoch['series']
            or family not in {b['id'] for b in epoch['preregistration']['blueprints']}
            or consumed not in (0, 1)
            or any(record.get(k) != v for k, v in (
                ('variant', variant), ('family', family), ('market', market),
                ('status', status), ('epoch_sha256', epoch_hash), ('model_sha256', model_hash)))
            or record['holdout_consumed'] is not bool(consumed)
            or any(record.get(k) is not False for k in
                   ('historical_qualified', 'paper_eligible', 'live_eligible'))):
        raise ValueError('Attempt provenance differs')
    if status == 'BLOCKED_PRODUCT_MODEL':
        if not market.startswith('perpetual:') or record['phases'] or consumed:
            raise ValueError('Invalid blocked product evidence')
        return {'status': 'NOT_APPLICABLE', 'reason': 'PRODUCT_MODEL_BLOCKED', 'legs': 0}
    if status not in COMPLETE_PHASES:
        return {'status': 'BLOCKED', 'reason': 'NO_COMPLETED_ACCOUNTING_EVIDENCE', 'legs': 0}
    phases = COMPLETE_PHASES[status]
    if (not market.startswith('spot:') or set(record['phases']) != set(phases)
            or bool(consumed) != ('holdout' in phases)):
        raise ValueError('Phase or holdout provenance differs')
    bars = epoch['series'][market]
    rules = epoch['preregistration']['rules']
    parameters = epoch['preregistration']['parameters']
    n = len(bars)
    windows = {'train': (rules['warmup_hours'], n * 3 // 5),
               'validation': (n * 3 // 5, n * 4 // 5), 'holdout': (n * 4 // 5, n)}
    legs = []
    for phase in phases:
        data = record['phases'][phase]
        if data['qualified'] is not False:
            raise ValueError('Stored screen incorrectly qualified')
        for stressed in (False, True):
            with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
                multiplier = Decimal(rules['cost_stress_multiplier']) if stressed else Decimal(1)
                fee = str(number(epoch['spot_fee']) * multiplier)
                slippage = str(number(rules['slippage']) * multiplier)
            result = data['stress' if stressed else 'normal']
            benchmarks = data['stress_benchmarks' if stressed else 'benchmarks']
            if set(benchmarks) != {'cash', 'buy_hold', 'simple_ma'}:
                raise ValueError('Missing same-cohort accounting benchmark')
            if benchmarks['cash']['orders']:
                raise ValueError('Cash benchmark unexpectedly trades')
            hold_orders = benchmarks['buy_hold']['orders']
            expected_hold = ([(bars[windows[phase][0]]['time'], 'buy'),
                              (bars[windows[phase][1] - 1]['time'], 'sell')]
                if number(rules['hypothetical_capital']) * number(parameters['allocation']) >= 10
                and windows[phase][1] - windows[phase][0] > 1 else [])
            if [(o['time'], o['side']) for o in hold_orders] != expected_hold:
                raise ValueError('Buy-hold benchmark timing differs')
            for leg in (result, *(benchmarks[k] for k in sorted(benchmarks))):
                legs.append(audit_leg(bars, *windows[phase], leg, fee, slippage,
                                      rules['hypothetical_capital'], parameters['allocation']))
    return {'status': 'PASS', 'phases': list(phases), 'legs': len(legs),
            'hourly_marks': sum(leg['hourly_marks'] for leg in legs),
            'orders': sum(leg['orders'] for leg in legs), 'qualified': False}


def audit(root):
    """One consistent read-only snapshot; failures never become a healthy count."""
    import research_cycle as cycle
    path = cycle.history.safe(Path(root) / 'cycle.sqlite3')
    if not path.is_file():
        return {'status': 'BLOCKED', 'reason': 'RESEARCH_STATE_MISSING', 'qualified': False}
    if path.stat().st_size > cycle.MAX_REGISTRY:
        raise ValueError('Research registry exceeds bound')
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=3)) as con:
        con.execute('PRAGMA query_only=ON')
        con.execute('BEGIN')
        if con.execute("SELECT value FROM settings WHERE key='schema'").fetchone() != ('1',):
            raise ValueError('Unknown research schema')
        epoch, epoch_hash = cycle.load_epoch(con)
        model_hash = cycle.model_hash()
        rows = con.execute('SELECT a.variant,v.family,v.market,a.status,a.holdout_consumed,a.record '
                           'FROM attempts a LEFT JOIN variants v ON v.id=a.variant ORDER BY a.variant '
                           'LIMIT ?', (MAX_ATTEMPTS + 1,)).fetchall()
        if len(rows) > MAX_ATTEMPTS:
            raise ValueError('Too many research attempts')
        results = []
        for row in rows:
            # Exceptions deliberately block the entire report; never skip a bad row.
            result = audit_attempt(epoch, epoch_hash, model_hash, row)
            results.append({'variant': row[0], **result})
        snapshot_hash = stage_fingerprint([reported_stage(row) for row in rows])
    passed = sum(r['status'] == 'PASS' for r in results)
    blocked = sum(r['status'] == 'BLOCKED' for r in results)
    return {'status': 'BLOCKED' if blocked or not passed else 'PASS',
            'scope': 'INDEPENDENT_ACCOUNTING_OF_STORED_ASSUMED_FILLS_ONLY',
            'epoch_sha256': epoch_hash, 'model_sha256': model_hash,
            'reported_stages_sha256': snapshot_hash,
            'auditor_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'attempts_checked': len(results), 'completed_accounting_passed': passed,
            'product_models_not_applicable': sum(r['status'] == 'NOT_APPLICABLE' for r in results),
            'blocked_attempts': blocked, 'legs_checked': sum(r['legs'] for r in results),
            'hourly_marks_checked': sum(r.get('hourly_marks', 0) for r in results),
            'results': results, 'qualified': False,
            'limitations': ['Signals and realistic fills are not independently verified',
                            'No new trial or unconsumed holdout is evaluated',
                            'Accounting PASS is not historical/paper/live qualification']}
