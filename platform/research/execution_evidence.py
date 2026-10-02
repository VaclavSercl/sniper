"""Conservative spot IOC replay. Public books are evidence, not real fills.

Only a target-network owned order journal can calibrate this model. Displayed
depth is capped; a missing future book never becomes a midpoint/OHLC fill.
"""
from decimal import Decimal, ROUND_DOWN
import hashlib
import json
import math

from hyperliquid_history import encode, integer, number

D = Decimal
MODEL = {'schema': 1, 'network': 'MAINNET', 'product': 'spot', 'tif': 'Ioc',
         'latency_ms': 1000, 'max_book_age_ms': 15000, 'max_gap_ms': 30000,
         'participation': '0.05', 'fee_rate': '0.0021', 'stress_multiplier': 2,
         'minimum_notional': '10', 'price_protection': '0.005',
         'fee_asset_buy': 'BASE', 'fee_asset_sell': 'USDC',
         'calibration_samples': 30, 'calibration_max_age_ms': 7*86400000}


def sha(value):
    return hashlib.sha256(encode(value)).hexdigest()


def wire(value):
    return number(format(value, 'f'))


def lot(value, decimals):
    return value.quantize(D(1).scaleb(-integer(decimals, 0, 8)), rounding=ROUND_DOWN)


def price_ok(value, decimals):
    value = D(number(value, positive=True))
    normalized = value.normalize()
    return (value == value.to_integral_value() or
            (len(normalized.as_tuple().digits) <= 5 and
             normalized.as_tuple().exponent >= -(8-decimals)))


def book(raw, market, sent_ms, received_ms):
    """Validate an unaggregated twenty-level public response and its chronology."""
    integer(sent_ms); integer(received_ms)
    if not sent_ms <= received_ms <= sent_ms+30000:
        raise ValueError('Invalid request chronology')
    if raw['coin'] != market['coin'] or market['product'] != 'spot':
        raise ValueError('Wrong book identity')
    stamp = integer(raw['time'])
    if not sent_ms-15000 <= stamp <= received_ms+1000:
        raise ValueError('Stale or future venue book')
    levels = raw['levels']
    if not isinstance(levels, list) or len(levels) != 2:
        raise ValueError('Invalid book sides')
    sides = []
    for side, rows in enumerate(levels):
        if not isinstance(rows, list) or not 1 <= len(rows) <= 20:
            raise ValueError('Missing or oversized depth')
        out = []
        for row in rows:
            px = number(row['px'], positive=True); sz = number(row['sz'], positive=True)
            integer(row['n'], 1, 100000000)
            if not price_ok(px, market['size_decimals']) or lot(D(sz), market['size_decimals']) != D(sz):
                raise ValueError('Invalid venue precision')
            if out and (D(px) >= D(out[-1][0]) if side == 0 else D(px) <= D(out[-1][0])):
                raise ValueError('Unordered or duplicate depth')
            out.append([px, sz])
        sides.append(out)
    if D(sides[0][0][0]) >= D(sides[1][0][0]):
        raise ValueError('Crossed book')
    return {'market': market, 'venue_ms': stamp, 'sent_ms': sent_ms,
            'received_ms': received_ms, 'levels': sides, 'raw_sha256': sha(raw)}


def ioc(order, snapshot, cash, inventory, stress=False, model=MODEL):
    """Pure deterministic fill/accounting function, shared by replay and paper."""
    quantity = D(number(order['quantity'], positive=True))
    limit = D(number(order['limit'], positive=True))
    cash = D(number(cash, nonnegative=True)); inventory = D(number(inventory, nonnegative=True))
    decision = integer(order['decision_ms']); side = order['side']
    if side not in ('B', 'A'): raise ValueError('Invalid side')
    market = snapshot['market']; decimals = integer(market['size_decimals'], 0, 8)
    factor = model['stress_multiplier'] if stress else 1
    fee = D(model['fee_rate'])*factor
    rejection = None
    if (market['product'] != 'spot' or quantity != lot(quantity, decimals) or
            not price_ok(order['limit'], decimals)):
        rejection = 'PRECISION_OR_PRODUCT'
    arrival = decision+model['latency_ms']*factor
    # The selected request must start AFTER modeled arrival. Receipt time alone
    # would permit a pre-decision observation to masquerade as a future fill.
    if (snapshot['sent_ms'] < arrival or snapshot['venue_ms'] < arrival or
            snapshot['received_ms']-snapshot['venue_ms'] > model['max_book_age_ms'] or
            snapshot['received_ms']-arrival > model['max_gap_ms']):
        rejection = 'MISSING_OR_STALE_FUTURE_BOOK'
    if quantity*limit < D(model['minimum_notional']): rejection = 'MINIMUM_NOTIONAL'
    if side == 'B' and quantity*limit > cash: rejection = 'INSUFFICIENT_QUOTE'
    if side == 'A' and quantity > inventory: rejection = 'INSUFFICIENT_INVENTORY'
    fills = []
    if not rejection:
        remaining = quantity
        for px_text, depth_text in snapshot['levels'][1 if side == 'B' else 0]:
            px = D(px_text)
            if px > limit if side == 'B' else px < limit: break
            available = lot(D(depth_text)*D(model['participation'])/factor, decimals)
            amount = min(remaining, available)
            if amount <= 0: continue
            fills.append({'quantity': wire(amount), 'price': px_text})
            remaining -= amount
            if remaining == 0: break
        if not fills: rejection = 'IOC_NO_LIQUIDITY'
    filled = sum((D(f['quantity']) for f in fills), D(0))
    notional = sum((D(f['quantity'])*D(f['price']) for f in fills), D(0))
    fee_amount = filled*fee if side == 'B' else notional*fee
    if side == 'B': cash -= notional; inventory += filled-fee_amount
    else: cash += notional-fee_amount; inventory -= filled
    if cash < 0 or inventory < 0: raise ValueError('Accounting overdraw')
    return {'status': 'REJECTED' if rejection else ('FILLED' if filled == quantity else 'PARTIAL_CANCELLED'),
            'reason': rejection, 'filled': wire(filled), 'cancelled': wire(quantity-filled),
            'notional': wire(notional), 'fee': wire(fee_amount),
            'fee_asset': 'BASE' if side == 'B' else 'USDC', 'fills': fills,
            'cash': wire(cash), 'inventory': wire(inventory), 'arrival_ms': arrival,
            'book_sha256': sha(snapshot), 'model_sha256': sha(model), 'stress': stress}


def limit_price(snapshot, buy, model=MODEL):
    px = D(snapshot['levels'][1 if buy else 0][0][0])
    px *= 1+D(model['price_protection']) if buy else 1-D(model['price_protection'])
    # Round toward a less aggressive valid limit; never expand price protection.
    places = min(8-snapshot['market']['size_decimals'], max(0, 4-px.adjusted()))
    return wire(px.quantize(D(1).scaleb(-places), rounding=ROUND_DOWN))


def calibration(samples, now_ms, model=MODEL):
    """Recompute conservative error bounds from linked raw owned order outcomes.

    Caller supplies a read-only journal snapshot, never a boolean certificate.
    Host/application ownership is the trust boundary, not resistance to a hostile
    host administrator forging exchange responses. No actual samples -> BLOCKED.
    """
    integer(now_ms)
    if not samples: return {'status': 'BLOCKED', 'reason': 'NO_OWNED_REAL_EXECUTION_EVIDENCE', 'samples': 0}
    if len(samples) > 10000: raise ValueError('Unbounded calibration')
    seen = set(); sides = set(); cases = set(); times = []; latencies = []
    for sample in samples:
        if sample['network'] != model['network'] or sample['source'] != 'OWNED_VENUE_ORDER_JOURNAL':
            raise ValueError('Wrong calibration provenance')
        order = sample['order']; snapshot = sample['book']; actual = sample['actual']
        identity = sample['operation_id']
        if not isinstance(identity, str) or not 1 <= len(identity) <= 128 or identity in seen:
            raise ValueError('Duplicate calibration order')
        seen.add(identity); sides.add(order['side']); cases.add(actual['status'])
        stamp = integer(actual['time_ms']); times.append(stamp)
        latency = stamp-integer(order['decision_ms']); latencies.append(latency)
        if not 0 <= latency <= model['latency_ms'] or not now_ms-model['calibration_max_age_ms'] <= stamp <= now_ms:
            raise ValueError('Calibration latency or freshness failure')
        if actual['request_sha256'] != sha(order) or actual['response_sha256'] != sha(actual['raw_response']):
            raise ValueError('Unlinked raw execution evidence')
        predicted = ioc(order, snapshot, sample['cash'], sample['inventory'], model=model)
        real_qty = D(number(actual['filled'], nonnegative=True))
        real_ntl = D(number(actual['notional'], nonnegative=True))
        real_fee = D(number(actual['fee_rate'], nonnegative=True))
        if real_fee > D(model['fee_rate']) or D(predicted['filled']) > real_qty:
            raise ValueError('Optimistic quantity or fees')
        if real_qty:
            worst = max((D(f['price']) for f in predicted['fills']), default=D(order['limit'])) if order['side']=='B' else min((D(f['price']) for f in predicted['fills']), default=D(order['limit']))
            if real_ntl/real_qty > worst if order['side']=='B' else real_ntl/real_qty < worst:
                raise ValueError('Optimistic execution price')
        fills = actual['raw_response']['fills']
        if len({f['tid'] for f in fills}) != len(fills): raise ValueError('Duplicate venue fill')
        if (sum((D(number(f['sz'], positive=True)) for f in fills), D(0)) != real_qty or
                sum((D(number(f['sz'], positive=True))*D(number(f['px'], positive=True)) for f in fills), D(0)) != real_ntl or
                any(f['coin'] != snapshot['market']['coin'] or f['side'] != order['side'] or
                    f['oid'] != actual['oid'] for f in fills)):
            raise ValueError('Actual fill reconciliation failed')
        if actual['status'] not in required_statuses(): raise ValueError('Unknown actual order outcome')
        requested=D(order['quantity'])
        if ((actual['status']=='FILLED' and real_qty!=requested) or
            (actual['status']=='PARTIAL_CANCELLED' and not 0<real_qty<requested) or
            (actual['status']=='REJECTED' and real_qty!=0)):
            raise ValueError('Actual terminal quantity differs')
        asset=snapshot['market']['symbol'].split('/')[0] if order['side']=='B' else 'USDC'
        if any(f['feeToken']!=asset or integer(f['time'])>stamp or integer(f['time'])<order['decision_ms'] for f in fills):
            raise ValueError('Actual fee asset or timing differs')
        total_fee=sum((D(number(f['fee'],nonnegative=True)) for f in fills),D(0))
        denominator=real_qty if order['side']=='B' else real_ntl
        if (denominator and total_fee/denominator!=real_fee) or (not denominator and total_fee!=0):
            raise ValueError('Actual fee reconciliation failed')
    required = {'FILLED', 'PARTIAL_CANCELLED', 'REJECTED'}
    if len(samples) < model['calibration_samples'] or sides != {'B','A'} or not required <= cases:
        return {'status': 'BLOCKED', 'reason': 'INSUFFICIENT_SIDES_OR_OUTCOME_CASES', 'samples': len(samples)}
    return {'status': 'PASS', 'samples': len(samples), 'network': model['network'],
            'evidence_sha256': sha(samples), 'model_sha256': sha(model),
            'last_ms': max(times), 'p95_latency_ms': sorted(latencies)[math.ceil(len(latencies)*.95)-1]}


def required_statuses(): return {'FILLED','PARTIAL_CANCELLED','REJECTED'}


def performance(equity, fills, initial='1000'):
    """Full chronological marks and complete daily returns; no trade-only curve."""
    if not equity: raise ValueError('Missing equity')
    previous = None; peak = D(initial); dd = D(0); days = {}
    for stamp, value in equity:
        integer(stamp); value = D(number(value, positive=True))
        if previous is not None and stamp != previous+3600000:
            raise ValueError('Incomplete hourly equity')
        previous = stamp; peak = max(peak, value); dd = max(dd, (peak-value)/peak)
        days.setdefault(stamp//86400000, []).append(value)
    returns = []; prior = D(initial)
    for day, values in sorted(days.items()):
        if len(values) == 24: returns.append(wire(values[-1]/prior-1))
        prior = values[-1]
    positives = sum(D(r)>0 for r in returns); n = len(returns)
    pvalue = sum(math.comb(n,k) for k in range(positives,n+1))/2**n if n else 1
    blocks=[sum((D(r) for r in returns[i:i+3]),D(0)) for i in range(0,len(returns)-2,3)]
    bn=len(blocks); bp=sum(r>0 for r in blocks)
    block_pvalue=sum(math.comb(bn,k) for k in range(bp,bn+1))/2**bn if bn else 1
    return {'net_return': wire(D(equity[-1][1])/D(initial)-1), 'max_drawdown': wire(dd),
            'daily_returns': returns, 'complete_days': n, 'filled_orders': fills,
            'positive_day_sign_pvalue': pvalue, 'three_day_blocks':bn,
            'positive_block_sign_pvalue':block_pvalue}
