"""Diverse causal proposals within the existing immutable recipe campaign.

New proposal order is versioned separately from frozen signal/model code. This
does not add trials beyond the registered 1,848, change existing candidates or
claim that parameter/context variants are independent statistical discoveries.
External model proposals are data-only strict DSL, never generated Python.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import execution_evidence as ex
import forward_pipeline as pipeline
import strategy_recipes as recipes
from hyperliquid_history import encode, integer, safe

VERSION = 'balanced-economic-trigger-proposals-v1'


def identity():
    return hashlib.sha256(safe(Path(__file__).resolve()).read_bytes()).hexdigest()


def external(raw):
    if not isinstance(raw, bytes) or len(raw)>16384: raise ValueError('Bounded proposal bytes required')
    def pairs(rows):
        data={}
        for key,value in rows:
            if key in data: raise ValueError('Duplicate proposal field')
            data[key]=value
        return data
    def invalid(_): raise ValueError('Nonfinite proposal')
    value=json.loads(raw,object_pairs_hook=pairs,parse_constant=invalid)
    recipes.validate(value)
    if value['holding_hours']!=6 or value['lookback_hours']!=6:
        raise ValueError('Proposal cannot change registered parameter protocol')
    if not any(g['trigger']==value['trigger'] and set(g['filters'])==set(value['filters']) for g in recipes.GRAMMAR):
        raise ValueError('Unregistered economic mechanism')
    return {**value,'filters':sorted(value['filters'])}


def generate(con, markets, now, proposals=()):
    integer(now); day=datetime.fromtimestamp(now/1000,timezone.utc).date().isoformat()
    spots=[m for m in markets if m['product']=='spot']
    if len(spots)!=2 or len({m['id'] for m in spots})!=2: raise ValueError('Exact two spot identities required')
    if not isinstance(proposals,(tuple,list)) or len(proposals)>2: raise ValueError('At most two validated input proposals')
    offered=[external(encode(p)) for p in proposals]
    created=[]; variants=0
    with con:
        con.execute('BEGIN IMMEDIATE'); pipeline.validate_store(con)
        known={i:json.loads(body) for i,body in con.execute('SELECT id,recipe FROM families')}
        used=con.execute('SELECT count(*) FROM families WHERE day=?',(day,)).fetchone()[0]
        while used<recipes.CAMPAIGN['mechanisms_per_day']:
            counts={t:sum(r['trigger']==t for r in known.values()) for t in recipes.TRIGGERS}
            available=[recipes.proposed(i) for i in range(len(recipes.GRAMMAR))
                       if recipes.economic_id(recipes.proposed(i)) not in known]
            if not available: break
            unconsumed=[p for p in offered if recipes.economic_id(p) not in known]
            recipe=(unconsumed[0] if unconsumed else min(available,key=lambda r:
                    (counts[r['trigger']],len(r['filters']),recipes.TRIGGERS.index(r['trigger']),tuple(r['filters']))))
            economic=recipes.economic_id(recipe)
            origin={'schema':1,'proposer':VERSION,'source_sha256':identity(),
                    'input_kind':'VALIDATED_EXTERNAL_DSL' if unconsumed else 'DETERMINISTIC_BALANCED_GRAMMAR',
                    'rationale_sha256':ex.sha(recipe['rationale']), 'no_paid_provider_invoked':True}
            con.execute('INSERT INTO families VALUES(?,?,?,?)',(economic,day,now,encode(recipe).decode()))
            start=(now//pipeline.DAY+1)*pipeline.DAY; end=start+recipes.CAMPAIGN['history_days']*pipeline.DAY
            for market in spots:
                for lookback in recipes.LOOKBACKS[recipe['trigger']]:
                    value={'schema':1,'family':economic,'recipe':{**recipe,'lookback_hours':lookback},'market':market,
                           'created_ms':now,'historical_start_ms':start,'historical_end_ms':end,
                           'campaign':recipes.CAMPAIGN,'model':ex.MODEL,'source_sha256':pipeline.source_hash(),
                           'variant_scope':'MARKET_AND_LOOKBACK_NOT_NEW_ECONOMIC_MECHANISM','proposal':origin}
                    con.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?,?,?)',
                        (ex.sha(value),economic,market['id'],lookback,now,start,end,encode(value).decode()))
                    variants+=1
            known[economic]=recipe; created.append(economic); used+=1
    return {'status':'GENERATED' if created else ('EXHAUSTED' if len(known)==len(recipes.GRAMMAR) else 'DAILY_QUOTA'),
            'new_economic_mechanisms':len(created),'new_market_variants':variants,
            'proposer':VERSION,'economic_trigger_counts':{t:sum(r['trigger']==t for r in known.values()) for t in recipes.TRIGGERS},
            'finite_grammar_size':len(recipes.GRAMMAR),'scheduled_llm':'NOT_CONFIGURED'}
