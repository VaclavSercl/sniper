"""Finite offline economic blueprints and causal hourly spot research models.

Market variants are not new economic hypotheses. All fills remain assumptions;
no result from this module can qualify historical, paper or funded execution.
"""
from decimal import Decimal
import math

HOUR=3600000
DAY=24*HOUR
BLUEPRINTS=(
    {'id':'active_volume_continuation','hypothesis':'An unusually active upward hour retains positive drift after costs.',
     'failure':'Next-hour execution after high volume does not beat cash and the same-risk baseline.'},
    {'id':'failed_downside_breakout','hypothesis':'A rejected downside range break reflects temporary selling pressure.',
     'failure':'Buying only after the completed rejection loses after fees or lacks enough independent trades.'},
    {'id':'compression_release','hypothesis':'A completed quiet range followed by an upward break persists briefly.',
     'failure':'Completed compression/breakout observations do not generalize to validation and holdout.'},
    {'id':'thin_volume_decline_reversal','hypothesis':'An unusually inactive decline reverses as selling activity fades.',
     'failure':'Low-activity declines continue or transaction costs consume their subsequent reversal.'},
    {'id':'utc_session_drift','hypothesis':'A fixed UTC session following positive daily drift has persistent net return.',
     'failure':'The preregistered session advantage disappears in chronological unseen periods.'},
    {'id':'low_volatility_carry','hypothesis':'Low realized hourly volatility following positive daily drift supports long exposure.',
     'failure':'The low-volatility regime delivers insufficient net return or adverse drawdown.'},
)
IDS={p['id'] for p in BLUEPRINTS}
PARAMETERS={'lookback':24,'holding_hours':6,'allocation':'0.25'}
RULES={'schema':1,'history_days':120,'warmup_hours':48,'train_fraction':'0.6','validation_end_fraction':'0.8',
    'hypothetical_capital':'1000','spot_fee_floor':'0.0021','slippage':'0.001',
    'cost_stress_multiplier':2,'minimum_segment_trades':10,'minimum_holdout_complete_days':20,
    'maximum_closing_drawdown':'0.10','familywise_alpha':'0.05','planned_market_cost_trials':72,
    'new_blueprints_per_utc_day':2,'variants_per_blueprint':6,'primary_test_bundles_per_utc_day':12,
    'paper_minimum_calendar_days':30,'scope':'EXPLORATORY_UNCALIBRATED_NOT_QUALIFICATION'}


def signal(family,history):
    if family not in IDS:raise ValueError('Unknown economic blueprint')
    if len(history)<PARAMETERS['lookback']+1:return False
    last=history[-1];past=history[-25:-1]
    close=float(last['close']);prior=float(past[-1]['close']);low=float(last['low']);high=float(last['high'])
    if close<=0 or prior<=0:raise ValueError('Invalid signal price')
    volume=float(last['volume']);mean_volume=sum(float(b['volume']) for b in past)/24
    ranges=[(float(b['high'])-float(b['low']))/float(b['close']) for b in past]
    mean_range=sum(ranges)/24
    if family=='active_volume_continuation':return close>prior and mean_volume>0 and volume>1.5*mean_volume
    if family=='failed_downside_breakout':
        edge=min(float(b['low']) for b in past);return low<edge and close>edge and close>float(last['open'])
    if family=='compression_release':
        short=sum(ranges[-6:])/6
        return mean_range>0 and short<mean_range*.65 and close>max(float(b['high']) for b in past)
    if family=='thin_volume_decline_reversal':return close<prior*.995 and mean_volume>0 and volume<mean_volume*.5
    positive_day=close>float(past[0]['close'])
    if family=='utc_session_drift':return last['time']//HOUR%24==7 and positive_day
    recent=sum(ranges[-6:])/6
    return positive_day and mean_range>0 and recent<mean_range*.6


def sign_tail(positives,n):
    """Exact one-sided sign-test tail. Zero/negative days count against success."""
    if type(positives) is not int or type(n) is not int or not 0<=positives<=n or n>1000:
        raise ValueError('Invalid sign-test sample')
    if not n:return 1.0
    return sum(math.comb(n,k) for k in range(positives,n+1))/2**n


def daily_returns(equity):
    days={}
    previous=None
    for point in equity:
        t=point['time'];day=t//DAY
        entry=days.setdefault(day,{'points':[],'previous':previous})
        entry['points'].append(point);previous=point
    returns=[]
    for day,value in sorted(days.items()):
        rows=value['points'];before=value['previous']
        if (len(rows)==24 and rows[0]['time']==day*DAY and rows[-1]['time']==day*DAY+23*HOUR and
                before and before['time']==rows[0]['time']-HOUR):
            initial=Decimal(before['equity']);final=Decimal(rows[-1]['equity'])
            if initial<=0:raise ValueError('Nonpositive equity')
            returns.append({'day':day,'return':str(final/initial-1)})
    return returns


def simulate(bars,start,end,family,fee,slippage):
    """Long-only assumed spot fills at next open, with complete hourly equity.

Cash, buy/hold and MA baseline use the same allocation, costs and window.
Future volume cannot be used to grant an open fill. Liquidity is uncalibrated.
"""
    if family not in IDS|{'cash','buy_hold','simple_ma'}:raise ValueError('Unknown model')
    if type(start) is not int or type(end) is not int or not 48<=start<end<=len(bars):
        raise ValueError('Invalid evaluation window')
    fee=Decimal(fee);slippage=Decimal(slippage)
    if not fee.is_finite() or not slippage.is_finite() or not 0<=fee<Decimal('.1') or not 0<=slippage<Decimal('.1'):
        raise ValueError('Invalid assumed costs')
    initial=Decimal(RULES['hypothetical_capital']);cash=initial;quantity=Decimal(0)
    allocation=Decimal(PARAMETERS['allocation']);trade_budget=initial*allocation
    entered=None;trades=0;equity=[];peak=initial;drawdown=Decimal(0);fees=Decimal(0);orders=[]
    for i in range(start,end):
        row=bars[i];price=Decimal(row['open'])
        if not price.is_finite() or price<=0:raise ValueError('Invalid execution price')
        # The signal decision is based on the preceding closed hour only.
        history=bars[max(0,i-48):i]
        if family=='cash':want=False
        elif family=='buy_hold':want=True
        elif family=='simple_ma':
            want=sum(Decimal(b['close']) for b in history[-12:])/12>sum(Decimal(b['close']) for b in history[-24:])/24
        else:want=signal(family,history)
        terminal=i==end-1
        due=quantity>0 and (terminal or family=='simple_ma' and not want or
            family in IDS and i-entered>=PARAMETERS['holding_hours'])
        if due:
            fill=price*(1-slippage);gross=quantity*fill;cost=gross*fee
            cash+=gross-cost;fees+=cost;trades+=1
            orders.append({'time':row['time'],'side':'sell','price':str(fill),'quantity':str(quantity),'fee':str(cost)})
            quantity=Decimal(0);entered=None
        elif quantity==0 and want and not terminal:
            budget=min(cash,trade_budget)
            if budget>=10:
                fill=price*(1+slippage);quantity=budget/(fill*(1+fee))
                gross=quantity*fill;cost=gross*fee;cash-=gross+cost;fees+=cost;entered=i
                orders.append({'time':row['time'],'side':'buy','price':str(fill),'quantity':str(quantity),'fee':str(cost)})
        close=Decimal(row['close'])
        if not close.is_finite() or close<=0 or cash<Decimal('-0.00000001') or quantity<0:
            raise ValueError('Invalid hypothetical inventory')
        value=cash+quantity*close
        peak=max(peak,value);drawdown=max(drawdown,(peak-value)/peak)
        equity.append({'time':row['time'],'equity':str(value)})
    if quantity!=0:raise ValueError('Terminal inventory not closed')
    days=daily_returns(equity);positive=sum(Decimal(d['return'])>0 for d in days)
    return {'initial_capital':str(initial),'final_equity':str(cash),'net':str(cash-initial),'fees':str(fees),
        'round_trips':trades,'maximum_closing_drawdown':str(drawdown),'daily_returns':days,
        'sign_test_p':sign_tail(positive,len(days)),'equity':equity,'orders':orders,
        'start_ms':bars[start]['time'],'end_ms':bars[end-1]['time']+HOUR,
        'execution':'ASSUMED_NEXT_OPEN_UNCALIBRATED','equity_resolution':'1h_closes_not_tick_drawdown',
        'qualified':False}


def partitions(bars):
    if len(bars)!=RULES['history_days']*24:raise ValueError('Incomplete preregistered history')
    n=len(bars)
    return (RULES['warmup_hours'],n*3//5,n*4//5,n)


def segment(bars,start,end,family,fee,holdout=False):
    normal=simulate(bars,start,end,family,fee,RULES['slippage'])
    stressed=simulate(bars,start,end,family,str(Decimal(fee)*2),str(Decimal(RULES['slippage'])*2))
    benchmarks={name:simulate(bars,start,end,name,fee,RULES['slippage']) for name in ('cash','buy_hold','simple_ma')}
    stress_benchmarks={name:simulate(bars,start,end,name,str(Decimal(fee)*2),str(Decimal(RULES['slippage'])*2))
                       for name in ('cash','buy_hold','simple_ma')}
    failures=[]
    if Decimal(normal['net'])<=0 or Decimal(stressed['net'])<=0:failures.append('NONPOSITIVE_NET_OR_COST_STRESS')
    if min(normal['round_trips'],stressed['round_trips'])<RULES['minimum_segment_trades']:failures.append('INSUFFICIENT_TRADES')
    if max(Decimal(normal['maximum_closing_drawdown']),Decimal(stressed['maximum_closing_drawdown']))>Decimal(RULES['maximum_closing_drawdown']):
        failures.append('CLOSING_DRAWDOWN_LIMIT')
    if any(Decimal(normal['net'])<=Decimal(b['net']) for b in benchmarks.values()):failures.append('DOES_NOT_BEAT_IDENTICAL_COHORT_BASELINES')
    if any(Decimal(stressed['net'])<=Decimal(b['net']) for b in stress_benchmarks.values()):failures.append('DOES_NOT_BEAT_STRESSED_COHORT_BASELINES')
    if holdout:
        if len(normal['daily_returns'])<RULES['minimum_holdout_complete_days']:failures.append('INSUFFICIENT_COMPLETE_DAYS')
        if normal['sign_test_p']>float(Decimal(RULES['familywise_alpha'])/RULES['planned_market_cost_trials']):
            failures.append('PLANNED_TRIAL_MULTIPLICITY')
    return {'normal':normal,'stress':stressed,'benchmarks':benchmarks,'stress_benchmarks':stress_benchmarks,'failures':failures,
        'exploratory_positive':not failures,'qualified':False}
