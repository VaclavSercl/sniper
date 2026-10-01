"""Independent Decimal reference ledger for stored historical replay legs.

Does not call the production fill, limit, accounting or performance functions.
Signals remain the pinned shared DSL; this checks causal intent/execution and
accounting reproduction, not independent authorship of an economic hypothesis.
"""
from decimal import Decimal, ROUND_DOWN
import hashlib
import json
import math

import strategy_recipes as recipes
from hyperliquid_history import encode

D=Decimal
HOUR=3600000


def digest(value): return hashlib.sha256(encode(value)).hexdigest()
def text(value): return format(value,'f').rstrip('0').rstrip('.') if '.' in format(value,'f') else format(value,'f')


def reference_fill(order,snap,cash,inventory,stress=False):
    """Separate explicit reference calculation; no production fill call."""
    market=snap['market']; size=D(order['quantity']); limit=D(order['limit'])
    buy=order['side']=='B'; factor=2 if stress else 1; decimals=market['size_decimals']
    quantum=D(1).scaleb(-decimals); reason=None
    normalized=limit.normalize()
    if (market['product']!='spot' or size!=size.quantize(quantum,rounding=ROUND_DOWN) or
        (limit!=limit.to_integral_value() and (len(normalized.as_tuple().digits)>5 or normalized.as_tuple().exponent<-(8-decimals)))):
        reason='PRECISION_OR_PRODUCT'
    arrival=order['decision_ms']+1000*factor
    if snap['sent_ms']<arrival or snap['venue_ms']<arrival or snap['received_ms']-snap['venue_ms']>15000 or snap['received_ms']-arrival>30000:
        reason='MISSING_OR_STALE_FUTURE_BOOK'
    if size*limit<D('10'): reason='MINIMUM_NOTIONAL'
    if buy and size*limit>cash: reason='INSUFFICIENT_QUOTE'
    if not buy and size>inventory: reason='INSUFFICIENT_INVENTORY'
    remaining=size; parts=[]
    if not reason:
        for px,depth in snap['levels'][1 if buy else 0]:
            if (buy and D(px)>limit) or (not buy and D(px)<limit): break
            amount=min(remaining,(D(depth)*D('.05')/factor).quantize(quantum,rounding=ROUND_DOWN))
            if amount<=0: continue
            parts.append({'quantity':text(amount),'price':px}); remaining-=amount
            if not remaining: break
        if not parts: reason='IOC_NO_LIQUIDITY'
    filled=size-remaining; ntl=sum((D(f['quantity'])*D(f['price']) for f in parts),D(0))
    fee=(filled if buy else ntl)*D('.0021')*factor
    if buy: cash-=ntl; inventory+=filled-fee
    else: cash+=ntl-fee; inventory-=filled
    from execution_evidence import MODEL
    return {'status':'REJECTED' if reason else ('FILLED' if filled==size else 'PARTIAL_CANCELLED'),
            'reason':reason,'filled':text(filled),'cancelled':text(remaining),'notional':text(ntl),
            'fee':text(fee),'fee_asset':'BASE' if buy else 'USDC','fills':parts,
            'cash':text(cash),'inventory':text(inventory),'arrival_ms':arrival,
            'book_sha256':digest(snap),'model_sha256':digest(MODEL),'stress':stress}


def paper_ledger(con,identity,lane,value,state):
    from forward_pipeline import future_book,latest_book
    stress=lane=='stress'; cash=D('1000'); inventory=D(0); fills=0; events=[]
    for decision,side,request,result in con.execute('SELECT decision,side,request,result FROM orders WHERE candidate=? AND lane=? ORDER BY decision',(identity,lane)):
        order=json.loads(request)
        if order['decision_ms']!=decision or order['side']!=side: raise ValueError('Paper intent index differs')
        if result is None: continue
        actual=json.loads(result)
        if actual['status'] in ('CANCELLED_FEED_GAP','CANCELLED_STOP_OR_GAP','CANCELLED_WORKER_STOP'):
            if D(actual['filled'])!=0 or D(actual['cancelled'])!=D(order['quantity']): raise ValueError('Cancelled paper intent changed')
            continue
        snap=future_book(con,value['market']['id'],decision,stress)
        expected=reference_fill(order,snap,cash,inventory,stress)
        if actual!=expected: raise ValueError('Independent paper fill/accounting differs')
        cash=D(expected['cash']);inventory=D(expected['inventory'])
        fills+=int(D(expected['filled'])>0)
        events.append((snap['received_ms'],cash,inventory))
    prefix='stress_' if stress else ''
    if D(state[prefix+'cash'])!=cash or D(state[prefix+'inventory'])!=inventory or state[prefix+'filled_orders']!=fills:
        raise ValueError('Paper balances differ from durable fill journal')
    cash=D('1000');inventory=D(0);index=0
    for period,eq,observed,book_sha in con.execute('SELECT time,equity,observed,book_sha FROM marks WHERE candidate=? AND lane=? ORDER BY time',(identity,lane)):
        if not period+HOUR<=observed<=period+HOUR+30000: raise ValueError('Paper mark chronology differs')
        snap=latest_book(con,value['market']['id'],observed)
        if digest(snap)!=book_sha: raise ValueError('Paper mark book changed')
        while index<len(events) and events[index][0]<=observed:
            _,cash,inventory=events[index];index+=1
        if D(eq)!=cash+inventory*D(snap['levels'][0][0][0]): raise ValueError('Independent paper equity differs')
    return {'status':'PASS','filled_orders':fills}


def verify(con,value,bars,start,end,result,stress=False,benchmark=None,minima=None,maxima=None):
    # Import only the raw-source accessors; simulation/accounting are separate.
    from forward_pipeline import latest_book, future_book
    cash=D('1000'); inventory=D(0); entered=None; fills=0; marks=[]
    peak=cash; daily=cash; day=None; risk=False; journal=[]
    model=value['model']; recipe=value['recipe']; market=value['market']
    q=D(1).scaleb(-market['size_decimals']); factor=2 if stress else 1
    for i,bar in enumerate(bars):
        t=bar['time']
        if not start<=t<end: continue
        quote=latest_book(con,market['id'],t); bid=D(quote['levels'][0][0][0])
        eq=cash+inventory*bid; peak=max(peak,eq)
        if day!=t//86400000: daily=cash+inventory*bid; day=t//86400000
        risk=risk or peak-eq>D('27') or daily-eq>D('9') or inventory*bid>D('180')
        buy=False; sell=False
        if inventory*bid>=D('10'): sell=risk or (benchmark!='buy_hold' and t-entered>=recipe['holding_hours']*HOUR) or t==end-HOUR
        elif i>=48 and not risk and t<end-recipe['holding_hours']*HOUR:
            if benchmark=='buy_hold': buy=entered is None
            elif benchmark=='ma': buy=sum(D(r['close']) for r in bars[i-6:i])/6>sum(D(r['close']) for r in bars[i-24:i])/24
            elif benchmark!='cash': buy=recipes.signal(recipe,bars[max(0,i-48):i],t)
        if buy or sell:
            top=D(quote['levels'][1 if buy else 0][0][0])*(D('1.005') if buy else D('.995'))
            places=min(8-market['size_decimals'],max(0,4-top.adjusted()))
            limit=top.quantize(D(1).scaleb(-places),rounding=ROUND_DOWN)
            size=(D('45')/limit if buy else inventory).quantize(q,rounding=ROUND_DOWN)
            if size:
                order={'decision_ms':t,'side':'B' if buy else 'A','limit':text(limit),'quantity':text(size)}
                snap=future_book(con,market['id'],t,stress); remaining=size; parts=[]
                if size*limit>=D('10') and (size*limit<=cash if buy else size<=inventory):
                    for px,depth in snap['levels'][1 if buy else 0]:
                        price=D(px)
                        if (buy and price>limit) or (not buy and price<limit): break
                        amount=min(remaining,(D(depth)*D('.05')/factor).quantize(q,rounding=ROUND_DOWN))
                        if amount<=0: continue
                        parts.append({'quantity':text(amount),'price':px}); remaining-=amount
                        if not remaining: break
                amount=sum((D(f['quantity']) for f in parts),D(0))
                ntl=sum((D(f['quantity'])*D(f['price']) for f in parts),D(0))
                fee=(amount if buy else ntl)*D('.0021')*factor
                if buy: cash-=ntl; inventory+=amount-fee
                else: cash+=ntl-fee; inventory-=amount
                if amount: fills+=1; entered=t if buy else entered
                index=len(journal)
                if index>=len(result['journal']): raise ValueError('Missing reference order')
                production=result['journal'][index]
                outcome=production['outcome']
                if production['order']!=order or outcome['fills']!=parts or any(D(outcome[k])!=n for k,n in
                    (('filled',amount),('cancelled',remaining),('notional',ntl),('fee',fee),('cash',cash),('inventory',inventory))):
                    raise ValueError('Reference execution/accounting differs')
                if outcome['stress']!=stress or outcome['book_sha256']!=digest(snap) or outcome['model_sha256']!=digest(model):
                    raise ValueError('Reference provenance differs')
                journal.append(production)
        adverse=cash+inventory*(minima or {}).get(t,bid); peak=max(peak,cash+inventory*(maxima or {}).get(t,bid))
        risk=risk or peak-adverse>D('27') or daily-adverse>D('9')
        close=latest_book(con,market['id'],t+HOUR)
        marks.append([t,text(cash if t==end-HOUR else cash+inventory*D(close['levels'][0][0][0]))])
    if len(journal)!=len(result['journal']) or marks!=result['equity'] or digest(journal)!=result['journal_sha256'] or digest(marks)!=result['equity_sha256']:
        raise ValueError('Reference chronology differs')
    peak=D('1000'); dd=D(0); days={}
    for t,eq in marks:
        eq=D(eq); peak=max(peak,eq); dd=max(dd,(peak-eq)/peak); days.setdefault(t//86400000,[]).append(eq)
    returns=[]; prior=D('1000')
    for values in days.values():
        if len(values)==24: returns.append(text(values[-1]/prior-1))
        prior=values[-1]
    blocks=[sum((D(x) for x in returns[i:i+3]),D(0)) for i in range(0,len(returns)-2,3)]
    n=len(blocks); positive=sum(x>0 for x in blocks)
    probability=sum(math.comb(n,k) for k in range(positive,n+1))/2**n if n else 1
    if (result['net_return']!=text(D(marks[-1][1])/D('1000')-1) or result['max_drawdown']!=text(dd)
        or result['daily_returns']!=returns or result['complete_days']!=len(returns) or result['filled_orders']!=fills
        or result['risk_breached']!=risk or D(result['remaining_inventory'])!=inventory or
        result['positive_block_sign_pvalue']!=probability):
        raise ValueError('Reference metrics differ')
    return {'status':'PASS','orders':len(journal),'marks':len(marks),'result_sha256':digest(result)}
