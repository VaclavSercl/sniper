"""Preregistered causal DSL and bounded economic-mechanism generator.

No eval, executable model output, threshold retuning or paid provider invocation.
Condition interactions are distinct mechanisms; market/wording/threshold changes
are variants. A finite declared grammar is reported honestly on exhaustion.
"""
from decimal import Decimal
from itertools import combinations
from execution_evidence import sha
from hyperliquid_history import integer, number

D=Decimal
TRIGGERS=('momentum','reversal','breakout','pullback_trend')
LOOKBACKS={t:((6,24) if t=='pullback_trend' else (1,6,24)) for t in TRIGGERS}
FILTERS=('trend_up','trend_down','volume_high','volume_low','volatility_high',
         'volatility_low','session_asia','session_europe','session_us')
PAIRS={frozenset(p) for p in (('trend_up','trend_down'),('volume_high','volume_low'),
                             ('volatility_high','volatility_low'),('session_asia','session_europe'),
                             ('session_asia','session_us'),('session_europe','session_us'))}
GRAMMAR=[{'trigger':t,'filters':list(f)} for t in TRIGGERS
         for n in (0,1,2,3) for f in combinations(FILTERS,n) if not any(p<=frozenset(f) for p in PAIRS)]
CAMPAIGN={'id':'spot-book-forward-v1','history_days':120,'warmup_hours':48,
          'train_fraction':'.5','validation_fraction':'.1','minimum_orders':10,
          'minimum_holdout_days':40,'paper_days':30,'max_drawdown':'.03',
          'daily_loss':'.01','capital_fraction':'.9','strategy_fraction':'.2',
          'order_fraction':'.25','max_trials':sum(len(LOOKBACKS[g['trigger']])*2 for g in GRAMMAR),
          'alpha':'.05','mechanisms_per_day':2,'variants_per_mechanism':6,
          'bundles_per_day':12,'paper_per_week':2,'hypothetical_capital':'1000'}


def validate(recipe):
    if not isinstance(recipe,dict) or set(recipe)!= {'schema','trigger','filters','lookback_hours','holding_hours','rationale','failure_rule'}:
        raise ValueError('Invalid recipe fields')
    if recipe['schema']!=1 or type(recipe['schema']) is not int or recipe['trigger'] not in TRIGGERS:
        raise ValueError('Unknown recipe grammar')
    filters=recipe['filters']
    if not isinstance(filters,list) or len(filters)>3 or len(set(filters))!=len(filters) or any(f not in FILTERS for f in filters):
        raise ValueError('Invalid recipe filters')
    if any(p<=frozenset(filters) for p in PAIRS): raise ValueError('Contradictory recipe filters')
    integer(recipe['lookback_hours'],1,24)
    if recipe['lookback_hours'] not in LOOKBACKS[recipe['trigger']]: raise ValueError('Undeclared parameter variant')
    integer(recipe['holding_hours'],1,24)
    for key in ('rationale','failure_rule'):
        if not isinstance(recipe[key],str) or not 20 <= len(recipe[key]) <= 500 or any(ord(c)<32 for c in recipe[key]):
            raise ValueError('Missing bounded economic explanation')
    return recipe


def economic_id(recipe):
    validate(recipe)
    return sha({'trigger':recipe['trigger'],'filters':sorted(recipe['filters'])})


def proposed(index):
    integer(index,0,len(GRAMMAR)-1); g=GRAMMAR[index]
    return validate({'schema':1,**g,'lookback_hours':6,'holding_hours':6,
        'rationale':f"Test whether {g['trigger']} predicts a continuation/reversal specifically under {', '.join(g['filters']) or 'all observed regimes'}, after spot execution costs.",
        'failure_rule':'Reject if training or validation fails after stressed costs, loses to same-risk benchmarks, breaches risk bounds or lacks enough independent observations.'})


def signal(recipe,bars,decision_ms):
    validate(recipe); integer(decision_ms)
    if len(bars)<48: return False
    rows=bars[-48:]
    for i,r in enumerate(rows):
        if r['time']+3600000>decision_ms or (i and r['time']!=rows[i-1]['time']+3600000):
            raise ValueError('Future or gapped signal input')
        for key in ('open','high','low','close'): number(r[key],positive=True)
        number(r['volume'],nonnegative=True)
    closes=[D(r['close']) for r in rows]; volumes=[D(r['volume']) for r in rows]
    recent=sum(closes[-6:])/6; average=sum(closes[-24:])/24
    vol=sum(volumes[-24:])/24
    ranges=[(D(r['high'])-D(r['low']))/D(r['close']) for r in rows]
    threshold=D('.005'); trigger=recipe['trigger']
    h=recipe['lookback_hours']
    if trigger in ('momentum','reversal'):
        ret=closes[-1]/closes[-1-h]-1
        enter=ret>threshold if trigger=='momentum' else ret < -threshold
    elif trigger=='breakout': enter=closes[-1]>max(D(r['high']) for r in rows[-h-1:-1])
    else:
        fast=sum(closes[-h:])/h;slow=sum(closes[-min(48,h*4):])/min(48,h*4)
        enter=fast>slow and closes[-1]<fast and closes[-1]>slow
    hour=decision_ms//3600000%24
    tests={'trend_up':recent>average,'trend_down':recent<average,
           'volume_high':volumes[-1]>vol*D('1.5'),'volume_low':volumes[-1]<vol*D('.5'),
           'volatility_high':sum(ranges[-6:])/6>sum(ranges[-24:])/24*D('1.5'),
           'volatility_low':sum(ranges[-6:])/6<sum(ranges[-24:])/24*D('.5'),
           'session_asia':0<=hour<8,'session_europe':8<=hour<16,'session_us':16<=hour<24}
    return bool(enter and all(tests[f] for f in recipe['filters']))
