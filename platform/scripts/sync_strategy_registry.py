#!/usr/bin/env python3
"""Read-only research status; no invented legacy T15 PASS or Git publication."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'research'))
from strategy_lifecycle import CATALOG, report, safe_path
from lifecycle_overview import load_policy, overview

def load_report(state_dir, candle_state_dir=None, candle_observation=None, hyperliquid_state_dir=None):
    if candle_state_dir is not None and candle_observation is not None:
        raise ValueError('Choose direct state or observation')
    policy, policy_hash = load_policy()
    result = {'schema': 2, 'live_eligible': 0, 'paper_qualified': 0,
              'catalog': [{'id': k, 'status': 'UNQUALIFIED', 'reason': v} for k, v in CATALOG.items()],
              'proposals': None, 'legacy_t15': 'INVALIDATED_NOT_QUALIFICATION_EVIDENCE'}
    sources = {'hydra': {'status': 'MISSING'}, 'candle': {'status': 'NOT_REQUESTED'}}
    try:
        path = safe_path(Path(state_dir)/'research.sqlite3')
        if path.is_file():
            con = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)
            try: data = report(con)
            finally: con.close()
            result['proposals'] = data['proposals']
            sources['hydra'] = {'status': 'OBSERVED', 'data': data}
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        sources['hydra'] = {'status': 'FAILED', 'error_type': type(exc).__name__}
    if candle_state_dir is not None:
        try:
            from candle_research import status as candle_status
            data = candle_status(candle_state_dir)
            result['candle_research'] = data
            sources['candle'] = {'status': 'OBSERVED' if data['status'] == 'OBSERVED' else 'MISSING',
                                 'data': data}
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            sources['candle'] = {'status': 'FAILED', 'error_type': type(exc).__name__}
    if candle_observation is not None:
        try:
            from operational_observation import load
            observation = load(candle_observation, datetime.now(timezone.utc))
            data = observation['candle']
            result['candle_research'] = data
            result['market_data'] = observation['market_data']
            result['observation_at'] = observation['observed_at']
            sources['candle'] = {'status': 'OBSERVED' if data['status'] == 'OBSERVED' else 'MISSING', 'data': data}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            sources['candle'] = {'status': 'FAILED', 'error_type': type(exc).__name__}
    result['catalog'].append({'id': 'T1', 'status': 'UNQUALIFIED',
        'reason': 'Exploratory spot candle screen; annual-data and execution gates unmet'})
    observed = sum(s['status'] == 'OBSERVED' for s in sources.values())
    result['status'] = 'OBSERVED' if observed == 2 else ('PARTIAL' if observed else 'BLOCKED')
    result['lifecycle'] = overview(sources, policy, policy_hash)
    if hyperliquid_state_dir is not None:
        try:
            from hyperliquid_history import summary
            result['hyperliquid_history'] = summary(hyperliquid_state_dir)
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            result['hyperliquid_history'] = {'status': 'FAILED', 'error_type': type(exc).__name__, 'qualified': False}
    return result

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--state-dir',type=Path,default=Path('/var/lib/sniper/research'))
    group=ap.add_mutually_exclusive_group()
    group.add_argument('--candle-state-dir',type=Path)
    group.add_argument('--candle-observation',type=Path)
    ap.add_argument('--hyperliquid-state-dir',type=Path,default=Path('/var/lib/sniper/hyperliquid-data'))
    args=ap.parse_args()
    try:
        result=load_report(args.state_dir,args.candle_state_dir,args.candle_observation,args.hyperliquid_state_dir)
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error) as exc:
        result={'status':'BLOCKED','reason':type(exc).__name__}
    print(json.dumps(result,indent=2,allow_nan=False))
    return 2 if result['status'] in ('BLOCKED','PARTIAL') else 0

if __name__=='__main__':
    raise SystemExit(main())
