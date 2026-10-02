#!/usr/bin/env python3
"""Owner's 90% account ceiling with AI-selected conservative initial policy.

Read-only budget evidence. This does not implement funded risk enforcement or
create historical/paper/execution qualification. No private key or network use.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import stat

DEFAULTS = {'schema': 1, 'mandate_id': 'hyperliquid-owner-90pct-ai-canary-v1',
    'authority': 'OWNER_CAPITAL_FRACTION_AND_DELEGATED_AI_RISK_2026_10_01',
    'capital_fraction': '0.9', 'reserve_fraction': '0.1',
    'risk_decision': 'AI_CONSERVATIVE_INITIAL_CANARY',
    'strategy_fraction': '0.2', 'order_fraction_of_strategy': '0.25',
    'daily_loss_fraction': '0.01', 'maximum_drawdown_fraction': '0.03',
    'perpetual_leverage': 1, 'max_concurrent_strategies': 1,
    'relaxation': 'NEW_REVIEWED_POLICY_AND_REQUALIFICATION',
    'live_executor': 'UNAVAILABLE'}


def decimal(value, nonnegative=True):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError('Exact decimal string required')
    try: n = Decimal(value)
    except InvalidOperation: raise ValueError('Invalid mandate number') from None
    if not n.is_finite() or abs(n) > Decimal('1e15') or n.as_tuple().exponent < -30 or nonnegative and n < 0:
        raise ValueError('Invalid bounded mandate value')
    return n


def text(n):
    value = format(n, 'f')
    return value.rstrip('0').rstrip('.') if '.' in value else value


def safe(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Unsafe mandate path')
    if path.is_file() and path.stat().st_nlink != 1:
        raise ValueError('Hardlinked mandate')
    return path


def validate(config):
    if set(config) != set(DEFAULTS) | {'account_binding_sha256'}:
        raise ValueError('Unknown mandate fields')
    if any(type(config.get(key)) is not type(value) or config.get(key) != value for key, value in DEFAULTS.items()):
        raise ValueError('Unreviewed mandate/profile change')
    digest = config['account_binding_sha256']
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('Invalid account binding')
    return dict(config)


def load(path, identity_path=Path('/etc/sniper/hyperliquid-account.json')):
    if os.name != 'posix':
        raise ValueError('Root-owned mandate validation requires Linux')
    path, identity_path = safe(path), safe(identity_path)
    for p in (path, identity_path):
        metadata = p.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o640:
            raise ValueError('Untrusted mandate/identity ownership or mode')
        if any(parent.stat().st_uid != 0 or parent.stat().st_mode & 0o022 for parent in p.parents):
            raise ValueError('Untrusted configuration parent')
        if metadata.st_size > 8192:
            raise ValueError('Oversized mandate/identity')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError('Duplicate mandate JSON')
            result[key] = value
        return result
    config = validate(json.loads(path.read_bytes(), object_pairs_hook=pairs))
    if hashlib.sha256(identity_path.read_bytes()).hexdigest() != config['account_binding_sha256']:
        raise ValueError('Mandate bound to a different account configuration')
    return config


def evaluate(config, account, now_ms=None):
    config = validate(config)
    now_ms = int(datetime.now(timezone.utc).timestamp()*1000) if now_ms is None else now_ms
    if type(now_ms) is not int or now_ms < 0:
        raise ValueError('Invalid observation clock')
    observed = account.get('observed_ms')
    if (type(observed) is not int or not 0 <= now_ms-observed <= 3600000 or
            account.get('status') != 'ACCOUNT_OBSERVED_READ_ONLY' or
            account.get('association_revalidated') is not True):
        raise ValueError('Fresh associated account required')
    balances = account['spot_balances']
    if not isinstance(balances, list) or len(balances) > 10000:
        raise ValueError('Invalid account inventory')
    coins = set(); usdc = None
    for row in balances:
        if row['coin'] in coins: raise ValueError('Duplicate account inventory')
        coins.add(row['coin'])
        total, hold = decimal(row['total']), decimal(row['hold'])
        if hold > total: raise ValueError('Held inventory exceeds equity')
        if row['coin'] == 'USDC': usdc = (total, hold)
        elif total != 0:
            raise ValueError('Non-USDC holdings require fresh measured valuation')
    if usdc is None: raise ValueError('USDC equity unavailable')
    perp, withdrawable = decimal(account['perpetual_equity']), decimal(account['perpetual_withdrawable'])
    if withdrawable > perp: raise ValueError('Inconsistent perpetual account availability')
    equity = usdc[0]+perp
    ceiling = equity*decimal(config['capital_fraction'])
    deployable = min(ceiling, usdc[0]-usdc[1]+withdrawable)
    strategy_cap = deployable*decimal(config['strategy_fraction'])
    order_cap = strategy_cap*decimal(config['order_fraction_of_strategy'])
    positions, orders = account['positions'], account['open_orders']
    if type(positions) is not int or type(orders) is not int or positions < 0 or orders < 0:
        raise ValueError('Unverified exposure counts')
    reasons = ['FUNDED_EXECUTOR_UNAVAILABLE', 'HISTORICAL_AND_30_REAL_DAY_PAPER_QUALIFICATION_REQUIRED',
               'TESTNET_EXECUTION_NOT_VERIFIED', 'LOSS_AND_EXPOSURE_ENFORCEMENT_NOT_IMPLEMENTED']
    if positions or orders:
        reasons.append('EXISTING_EXPOSURE_REQUIRES_COMPLETE_RECONCILIATION')
    if order_cap < 10:
        reasons.append('ORDER_CAP_BELOW_DOCUMENTED_DEFAULT_10_USDC_MINIMUM')
    return {'status': 'OWNER_MANDATE_OBSERVED_POLICY_ONLY', 'mandate_id': config['mandate_id'],
        'authority': config['authority'], 'account_masked': account['account_masked'],
        'account_observed_ms': observed, 'equity_usdc': text(equity),
        'capital_fraction': config['capital_fraction'], 'capital_ceiling_usdc': text(ceiling),
        'reserve_floor_usdc': text(equity-ceiling), 'deployable_ceiling_usdc': text(deployable),
        'strategy_cap_usdc': text(strategy_cap), 'order_cap_usdc': text(order_cap),
        'daily_loss_cap_usdc': text(deployable*decimal(config['daily_loss_fraction'])),
        'maximum_drawdown_fraction': config['maximum_drawdown_fraction'],
        'drawdown_cap_usdc': text(deployable*decimal(config['maximum_drawdown_fraction'])),
        'perpetual_leverage': config['perpetual_leverage'],
        'max_concurrent_strategies': config['max_concurrent_strategies'],
        'enforcement': 'CONFIGURED_POLICY_ONLY_NO_FUNDED_EXECUTOR',
        'venue_minimum_scope': 'DOCUMENTED_DEFAULT_REQUIRES_ASSET_AND_EXIT_VERIFICATION',
        'live_eligible': False, 'blockers': reasons}
