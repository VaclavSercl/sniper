"""Validated research policy and bounded read-only registry summary.

This module cannot qualify, schedule or deploy a strategy. Policy targets are
not observed throughput. Missing source evidence is UNKNOWN, never zero runs.
"""
import hashlib
import json
from pathlib import Path

POLICY = Path(__file__).with_name('lifecycle_policy.json')
COMPLETE = {'BASELINE_SCREEN_COMPLETE', 'TRAINING_SCREEN_COMPLETE'}


def unique_object(items):
    result = {}
    for key, value in items:
        if key in result: raise ValueError('Duplicate policy key')
        result[key] = value
    return result


def load_policy(path=POLICY):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Unsafe policy path')
    raw = path.read_bytes()
    if len(raw) > 65536: raise ValueError('Policy too large')
    def nonfinite(_): raise ValueError('Nonfinite policy number')
    p = json.loads(raw, object_pairs_hook=unique_object, parse_constant=nonfinite)
    if (p['schema'] != 1 or type(p['schema']) is not int
            or p['primary_live_venue'] != 'hyperliquid'
            or p['products'] != ['spot', 'perpetual']
            or p['implementation_boundary'] != 'BOUNDED_OFFLINE_RESEARCH_AND_REPORTING_NO_PROMOTION_EXECUTOR'
            or p['live']['implementation'] != 'UNAVAILABLE'):
        raise ValueError('Unsupported lifecycle policy')
    capacity = p['capacity_proposal']
    if capacity['status'] != 'BOUNDED_OFFLINE_IMPLEMENTED_RUNTIME_REQUIRES_OBSERVATION':
        raise ValueError('Policy target is not an installed scheduler')
    for key in ('new_hypotheses_per_day', 'max_variants_per_hypothesis',
                'max_training_tests_per_day', 'comparison_interval_days',
                'max_paper_admissions_per_week'):
        if type(capacity[key]) is not int or not 1 <= capacity[key] <= 1000:
            raise ValueError('Invalid capacity proposal')
    limits={'new_hypotheses_per_day':2,'max_variants_per_hypothesis':6,
            'max_training_tests_per_day':12,'comparison_interval_days':7,'max_paper_admissions_per_week':2}
    if any(capacity[key]!=value for key,value in limits.items()):
        raise ValueError('Capacity cannot exceed or misstate the implemented bounded protocol')
    paper = p['paper_evidence']
    if (type(paper['minimum_calendar_days']) is not int or paper['minimum_calendar_days'] < 30
            or paper['parameter_change_restarts_epoch'] is not True
            or paper['missing_or_insufficient_sample'] != 'BLOCKED'):
        raise ValueError('Invalid paper evidence rule')
    historical = p['historical_evidence']
    days = historical['strategy_minimum_history_days']['T1']
    if (type(days) is not int or days < 365
            or historical['preserve_stricter_strategy_rules'] is not True
            or historical['missing_evidence'] != 'BLOCKED'
            or 'single_use_final_holdout' not in historical['required']):
        raise ValueError('Historical evidence requirement weakened')
    # This version intentionally has no validated mandate or promotion executor.
    # Filling JSON values is not a replacement for implementing those integrations.
    for key in ('account_identity', 'total_capital', 'per_strategy_capital',
                'daily_loss_limit', 'maximum_drawdown', 'perpetual_leverage_limit'):
        if p['live'][key] is not None:
            raise ValueError('Live mandate integration is not implemented')
    forward=p['forward_pipeline']
    if (forward['campaign']!='spot-book-forward-v1' or forward['historical_book_days']!=120 or
        forward['holdout_fraction']!='0.4' or forward['planned_market_trials']!=1848 or
        forward['economic_grammar_size']!=336 or forward['actual_execution_calibration_required'] is not True or
        forward['runtime_status_requires_observation'] is not True or forward['mainnet_transport']!='UNAVAILABLE'):
        raise ValueError('Forward evidence policy changed')
    return p, hashlib.sha256(raw).hexdigest()


def overview(sources, policy, policy_hash):
    candle = sources['candle']
    hydra = sources['hydra']
    counts = {'catalog_entries': 12, 'candle_variants_registered': None,
              'candle_screens_complete': None, 'candle_screens_negative': None,
              'candle_runs_blocked': None, 'candle_runs_interrupted': None,
              't1_variants_not_yet_registered': None, 'hydra_proposals': None,
              'hydra_evaluated': None, 'registered_families_with_runs': None,
              'paper_qualified': 0, 'live_eligible': 0,
              'legacy_paper_running': None}
    stages = []
    if candle['status'] == 'OBSERVED':
        runs = candle['data']['runs']
        identities, days = set(), set()
        for run in runs:
            identity = run['candidate']
            if (type(identity) is not int or not 0 <= identity < 18
                    or identity in identities or run['day'] in days):
                raise ValueError('Duplicate or invalid candle run identity')
            identities.add(identity); days.add(run['day'])
            state = run['status']
            if state not in COMPLETE | {'BLOCKED', 'STARTED'}:
                raise ValueError('Unknown candle run status')
            negative = None
            if state in COMPLETE:
                parts = [run['result']['training']]
                if run['holdout_used'] is True: parts.append(run['result']['holdout'])
                if any(type(p['net_positive_under_assumptions']) is not bool for p in parts):
                    raise ValueError('Invalid completed screen')
                negative = any(p['net_positive_under_assumptions'] is False for p in parts)
            stages.append({'family': 'T1', 'candidate': identity, 'day': run['day'],
                           'stage': 'SCREEN_REJECTED' if negative else state,
                           'negative_under_assumptions': negative,
                           'paper_eligible': False, 'live_eligible': False})
        counts.update(candle_variants_registered=len(runs),
                      candle_screens_complete=sum(r['status'] in COMPLETE for r in runs),
                      candle_screens_negative=sum(s['negative_under_assumptions'] is True for s in stages),
                      candle_runs_blocked=sum(r['status'] == 'BLOCKED' for r in runs),
                      candle_runs_interrupted=sum(r['status'] == 'STARTED' for r in runs),
                      t1_variants_not_yet_registered=18-len(runs))
    if hydra['status'] == 'OBSERVED':
        proposals = hydra['data']['proposals']
        if len({p['id'] for p in proposals}) != len(proposals):
            raise ValueError('Duplicate proposal identity')
        if any(p['bot'] != 'hydra' or p['status'] not in
               {'PROPOSED', 'BLOCKED', 'RESEARCH_REJECTED', 'RESEARCH_PASS'} for p in proposals):
            raise ValueError('Unsupported proposal record')
        counts['hydra_proposals'] = len(proposals)
        counts['hydra_evaluated'] = sum(p['status'] != 'PROPOSED' for p in proposals)
    if all(sources[k]['status'] == 'OBSERVED' for k in ('candle', 'hydra')):
        counts['registered_families_with_runs'] = int(counts['candle_variants_registered'] > 0) + int(counts['hydra_proposals'] > 0)
    return {'schema': 1, 'policy_id': policy['policy_id'], 'policy_sha256': policy_hash,
            'primary_live_venue': policy['primary_live_venue'], 'products': policy['products'],
            'counts': counts, 'candle_stages': stages,
            'coverage': {name: {k: v for k, v in src.items() if k != 'data'} for name, src in sources.items()},
            'qualification_scope': 'REGISTERED_RESEARCH_ONLY_NOT_ALL_HOST_PROCESSES',
            'qualification_counts_basis': 'NO_IMPLEMENTED_QUALIFICATION_ADAPTERS',
            'legacy_paper_scope': 'NOT_QUERIED_REQUIRES_SEPARATE_HOST_AND_DB_AUDIT',
            'implemented_capacity': {'new_strategy_generator': 'BOUNDED_OFFLINE_CODE_AVAILABLE_RUNTIME_NOT_QUERIED',
                                     't1_variants_per_utc_day': 1, 't1_family_limit': 18,
                                     'scheduler_health': 'NOT_QUERIED'},
            'capacity_proposal': policy['capacity_proposal'],
            'automatic_paper_admission': 'NOT_IMPLEMENTED',
            'automatic_live_promotion': 'NOT_IMPLEMENTED',
            'enforcement_boundary': policy['implementation_boundary']}
