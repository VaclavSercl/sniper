"""Read and reconcile one owned testnet spot account before risk admission.

Independent venue reads are conservative observations, not an atomic exchange
snapshot. Unknown ownership, gaps, cashflows or valuation stop new exposure.
"""
import json
from order_validation import integer, number, wire
from portfolio_risk import MAX_AGE_MS


def observe(client, instrument):
    with client.transaction():
        client.risk.invalidate()
    started = integer(client.clock())
    previous = client.risk.state()
    baseline = previous['start_ms'] if previous else started
    if previous is None and client.con.execute('SELECT count(*) FROM operations').fetchone()[0]:
        raise ValueError('Existing order journal requires explicit risk-epoch reconciliation')
    spot = client.send('info', {'type': 'spotClearinghouseState', 'user': client.account})
    perp = client.send('info', {'type': 'clearinghouseState', 'user': client.account})
    fees = client.send('info', {'type': 'userFees', 'user': client.account})
    orders = client.send('info', {'type': 'openOrders', 'user': client.account})
    flows = client.send('info', {'type': 'userNonFundingLedgerUpdates', 'user': client.account,
                             'startTime': baseline, 'endTime': integer(client.clock())})
    fills = client.send('info', {'type': 'userFillsByTime', 'user': client.account,
                             'startTime': baseline, 'endTime': integer(client.clock()),
                             'aggregateByTime': False})
    if (not isinstance(flows, list) or flows or not isinstance(orders, list) or len(orders) >= 500 or
            not isinstance(fills, list) or len(fills) >= 500 or
            not isinstance(perp['assetPositions'], list) or perp['assetPositions'] or
            number(perp['marginSummary']['accountValue'], False) < 0 or
            number(fees['userSpotCrossRate'], False) > number('0.01')):
        raise ValueError('Unknown cashflows, perpetual risk, fees or incomplete open orders')
    tokens = set(); cash = held = inventory = None
    if not isinstance(spot['balances'], list) or len(spot['balances']) > 10000:
        raise ValueError('Incomplete spot inventory')
    for row in spot['balances']:
        token = integer(row['token'], 0, 100000)
        total, hold = number(row['total'], False), number(row['hold'], False)
        if token in tokens or hold > total:
            raise ValueError('Duplicate/inconsistent spot inventory')
        tokens.add(token)
        if token == instrument.quote_index:
            cash, held = total, hold
        elif token == instrument.base_index:
            inventory = total
        elif total:
            raise ValueError('Other positive inventory requires full measured valuation')
    if cash is None:
        raise ValueError('Actual USDC inventory required')
    inventory = inventory or number('0', False)
    expected_inventory = number('0', False)
    expected_cash = number(previous['initial_equity']) if previous else cash
    seen = set(); quantities = {}
    known = []
    for row in client.con.execute('SELECT intent,result,status FROM operations WHERE kind=\'order\''):
        intent, result = json.loads(row[0]), json.loads(row[1])
        if row[2].startswith('RECONCILED_') and row[2] != 'RECONCILED_TERMINAL':
            known.append((intent, result))
    for fill in fills:
        tid, oid = integer(fill['tid'], 1), integer(fill['oid'], 1)
        if tid in seen or not baseline <= integer(fill['time']) <= integer(client.clock()):
            raise ValueError('Duplicate/out-of-window fill')
        seen.add(tid)
        found = [(i, r) for i, r in known if r.get('oid') == oid and i['instrument'] == instrument.__dict__]
        if len(found) != 1:
            raise ValueError('Fill ownership is missing or ambiguous')
        order = found[0][0]['action']['orders'][0]
        quantity, price, fee = number(fill['sz']), number(fill['px']), number(fill['fee'], False)
        if fill['coin'] != instrument.coin or fill['side'] != ('B' if order['b'] else 'A'):
            raise ValueError('Fill product/side differs from intent')
        quantities[oid] = quantities.get(oid, number('0', False))+quantity
        if quantities[oid] > number(order['s']) or (order['b'] and price > number(order['p'])) or \
                (not order['b'] and price < number(order['p'])):
            raise ValueError('Fill exceeds owned quantity/limit')
        expected_inventory += quantity if order['b'] else -quantity
        expected_cash += -quantity*price if order['b'] else quantity*price
        if fill['feeToken'] == instrument.symbol.split('/')[0]:
            expected_inventory -= fee
        elif fill['feeToken'] == 'USDC':
            expected_cash -= fee
        else:
            raise ValueError('Unvalued fill fee token')
    if inventory != expected_inventory or cash != expected_cash:
        raise ValueError('Account inventory/cash does not reconcile to complete owned fills')
    marked = number('0', False)
    if inventory:
        owned = client.con.execute('SELECT intent FROM operations WHERE kind=\'order\'').fetchall()
        if not owned or any(json.loads(row[0])['instrument'] != instrument.__dict__ for row in owned):
            raise ValueError('Inventory requires exact journal instrument ownership')
        book = client.send('info', {'type': 'l2Book', 'coin': instrument.coin})
        if (book['coin'] != instrument.coin or not 0 <= integer(client.clock())-integer(book['time']) <= MAX_AGE_MS or
                len(book['levels']) != 2 or not book['levels'][0]):
            raise ValueError('Fresh liquidation-side book required')
        left, marked = inventory, number('0', False)
        previous_price = None
        if len(book['levels'][0]) > 20:
            raise ValueError('Unexpected book depth')
        for level in book['levels'][0]:
            price, size = number(level['px']), number(level['sz'], False)
            if previous_price is not None and price >= previous_price:
                raise ValueError('Invalid liquidation-side level ordering')
            previous_price = price
            fill = min(left, size); marked += fill*price; left -= fill
        if left:
            raise ValueError('Insufficient observed book depth for inventory valuation')
    pending = number('0', False)
    for row in orders:
        matches = client.con.execute('SELECT intent,status FROM operations WHERE kind=\'order\'').fetchall()
        found = [json.loads(r[0]) for r in matches if r[1] == 'RECONCILED_OPEN' and
                 json.loads(r[0])['action']['orders'][0]['c'] == row.get('cloid')]
        if len(found) != 1:
            raise ValueError('Foreign/unreconciled open order')
        owned = found[0]['action']['orders'][0]
        if (row['coin'] != instrument.coin or row['side'] != ('B' if owned['b'] else 'A') or
                number(row['limitPx']) != number(owned['p']) or number(row['sz'], False) > number(owned['s'])):
            raise ValueError('Open order identity/size changed')
        if owned['b']:
            pending += number(row['sz'], False)*number(row['limitPx'])*number('1.01')
    if held > pending or pending and held < pending/number('1.01'):
        raise ValueError('Held USDC does not reconcile to observed owned buy orders')
    finished = integer(client.clock())
    if not 0 <= finished-started <= MAX_AGE_MS:
        raise ValueError('Account risk reads exceeded freshness budget')
    snapshot = {'time_ms': started, 'equity': wire(cash+marked), 'available': wire(cash-held),
                'inventory': wire(marked), 'pending_buys': wire(pending),
                'complete': True, 'cashflow_free': True, 'ownership_verified': True}
    with client.transaction():
        return client.risk.observe(snapshot, finished)
