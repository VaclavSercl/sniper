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

def load_report(state_dir, candle_state_dir=None, candle_observation=None, hyperliquid_state_dir=None, account_state_dir=None, research_state_dir=None, mandate_path=None, forward_state_dir=None):
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
    if account_state_dir is not None:
        try:
            from hyperliquid_account import summary as account_summary
            result['hyperliquid_account'] = account_summary(account_state_dir)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result['hyperliquid_account'] = {'status': 'FAILED', 'error_type': type(exc).__name__, 'live_enabled': False}
    if research_state_dir is not None:
        try:
            from research_cycle import status as cycle_status
            data = cycle_status(research_state_dir)
            result['hyperliquid_research'] = data
            if data['status'] == 'RESEARCH_OBSERVED':
                counts = result['lifecycle']['counts']
                counts.update(new_economic_blueprints_registered=data['blueprints_registered'],
                    new_market_variants=data['registered_market_variants'],
                    new_completed_screens=data['completed_screens'],
                    new_product_models_blocked=data['blocked_product_models'])
                result['lifecycle']['implemented_capacity'].update(
                    new_strategy_generator=data['generator'],
                    new_blueprints_per_utc_day=data['new_blueprints_per_utc_day'],
                    max_variants_per_blueprint=data['max_variants_per_blueprint'],
                    max_primary_test_bundles_per_utc_day=data['max_primary_test_bundles_per_utc_day'],
                    blueprints_remaining=data['blueprints_remaining'],exhaustion_policy=data['exhaustion_policy'])
                if counts['registered_families_with_runs'] is not None:
                    counts['registered_families_with_runs'] += data['blueprints_registered']
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            result['hyperliquid_research'] = {'status': 'FAILED', 'error_type': type(exc).__name__, 'live_eligible': 0}
        try:
            from research_accounting_audit import audit as accounting_audit, stage_fingerprint
            accounting = accounting_audit(research_state_dir)
            source = result['hyperliquid_research']
            if (source['status'] != 'RESEARCH_OBSERVED'
                    or accounting.get('epoch_sha256') != source.get('epoch_sha256')
                    or accounting.get('attempts_checked') != len(source.get('stages', []))
                    or accounting.get('reported_stages_sha256') != stage_fingerprint(source.get('stages', []))):
                raise ValueError('Research accounting snapshot differs from report')
            result['research_accounting'] = accounting
        except (OSError, ValueError, KeyError, TypeError, ArithmeticError, sqlite3.Error) as exc:
            result['research_accounting'] = {'status': 'FAILED', 'error_type': type(exc).__name__,
                                             'qualified': False}
    if mandate_path is not None:
        try:
            from hyperliquid_mandate import load as load_mandate, evaluate as evaluate_mandate
            config = load_mandate(mandate_path)
            data = evaluate_mandate(config, result.get('hyperliquid_account', {}))
            result['hyperliquid_mandate'] = data
            result['hyperliquid_account']['mandate'] = data['enforcement']
            result['lifecycle']['owner_mandate'] = data
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result['hyperliquid_mandate'] = {'status':'FAILED','error_type':type(exc).__name__,'live_eligible':False}
    if forward_state_dir is not None:
        try:
            from forward_pipeline import report as forward_report
            data=forward_report(forward_state_dir)
            result['forward_research']=data
            if data['status']=='OBSERVED':
                counts=result['lifecycle']['counts']
                counts.update(forward_economic_mechanisms=data['counts']['families'],
                    forward_market_variants=data['counts']['candidates'],
                    forward_known_data_screens=data['counts']['screens'],
                    forward_historical_qualified=data['historical_qualified'],
                    forward_paper_running=sum(p['runtime_status']=='RUNNING' for p in data['paper_results']),
                    paper_qualified=data['paper_qualified'])
                result['paper_qualified']=data['paper_qualified']
                result['lifecycle']['automatic_paper_admission']='IMPLEMENTED_MEASURED_BOOK_HISTORICAL_GATES'
                result['lifecycle']['qualification_counts_basis']='VALIDATED_VERSIONED_FORWARD_REGISTRY'
                result['lifecycle']['implemented_capacity']['forward_generator']=data['generator']
                result['lifecycle']['implemented_capacity']['forward_mechanisms_remaining']=data['mechanisms_remaining']
                result['lifecycle']['forward_calibration']=data['calibration']
        except (OSError,ValueError,KeyError,TypeError,ArithmeticError,sqlite3.Error) as exc:
            result['forward_research']={'status':'FAILED','error_type':type(exc).__name__,'live_eligible':0}
    requested = [key for key in ('hyperliquid_history','hyperliquid_account','hyperliquid_research','hyperliquid_mandate','forward_research') if key in result]
    problems = [key for key in requested if result[key]['status'] not in
                ('OBSERVED','ACCOUNT_OBSERVED_READ_ONLY','RESEARCH_OBSERVED','OWNER_MANDATE_OBSERVED_POLICY_ONLY')]
    if 'research_accounting' in result and result['research_accounting']['status'] != 'PASS':
        problems.append('research_accounting')
    result['integration_health'] = {'status':'PARTIAL' if problems else ('OBSERVED' if requested else 'NOT_REQUESTED'),
                                  'unavailable_or_stale':problems}
    return result

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--state-dir',type=Path,default=Path('/var/lib/sniper/research'))
    group=ap.add_mutually_exclusive_group()
    group.add_argument('--candle-state-dir',type=Path)
    group.add_argument('--candle-observation',type=Path)
    ap.add_argument('--hyperliquid-state-dir',type=Path,default=Path('/var/lib/sniper/hyperliquid-data'))
    ap.add_argument('--account-state-dir',type=Path,default=Path('/var/lib/sniper/hyperliquid-account'))
    ap.add_argument('--research-state-dir',type=Path,default=Path('/var/lib/sniper/hyperliquid-research'))
    ap.add_argument('--mandate-path',type=Path,default=Path('/etc/sniper/hyperliquid-mandate.json'))
    ap.add_argument('--forward-state-dir',type=Path,default=Path('/var/lib/sniper/forward-research-v1'))
    args=ap.parse_args()
    try:
        result=load_report(args.state_dir,args.candle_state_dir,args.candle_observation,args.hyperliquid_state_dir,args.account_state_dir,args.research_state_dir,args.mandate_path,args.forward_state_dir)
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error) as exc:
        result={'status':'BLOCKED','reason':type(exc).__name__}
    print(json.dumps(result,indent=2,allow_nan=False))
    return 2 if result['status'] in ('BLOCKED','PARTIAL') or result.get('integration_health',{}).get('status')=='PARTIAL' else 0

if __name__=='__main__':
    raise SystemExit(main())
