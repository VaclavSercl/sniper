#!/usr/bin/env python3
"""Public funding monitor. Observations are not settlements or profit evidence."""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys
import time

from hyperliquid_connector import HyperliquidReadOnly

COINS = ('BTC', 'ETH', 'SOL', 'HYPE')
HIGH_RATE = 0.00005  # 0.005 percent per hour, expressed as a fraction.
NEGATIVE_RATE = -0.001  # Preserve the legacy negative alert threshold.


def number(value, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (str, float, int)):
        raise ValueError('INVALID_NUMBER')
    value = float(value)
    if not math.isfinite(value) or (minimum is not None and value < minimum):
        raise ValueError('INVALID_NUMBER')
    return value


def funding_rows(meta, contexts):
    universe = meta['universe']
    if not isinstance(universe, list) or not isinstance(contexts, list) or len(universe) != len(contexts):
        raise ValueError('INVALID_CONTEXT_ALIGNMENT')
    rows = []
    seen = set()
    for asset, ctx in zip(universe, contexts):
        coin = asset['name']
        if coin not in COINS:
            continue
        if coin in seen or asset.get('isDelisted', False):
            raise ValueError('DUPLICATE_OR_DELISTED_MARKET')
        seen.add(coin)
        rate = number(ctx['funding'])
        mark = number(ctx['markPx'], 0)
        oi = number(ctx['openInterest'], 0)
        if not mark:
            raise ValueError('ZERO_MARK')
        rows.append({'coin': coin, 'rate': rate, 'mark_price': mark, 'open_interest': oi,
                     'open_interest_usd': number(mark * oi, 0),
                     'kind': 'SNAPSHOT_NOT_SETTLEMENT', 'source_time_ms': None})
    if seen != set(COINS):
        raise ValueError('MISSING_REQUIRED_MARKETS')
    return sorted(rows, key=lambda row: (-row['open_interest_usd'], row['coin']))


def spot_usdc(data):
    balances = data['balances']
    if not isinstance(balances, list): raise ValueError('INVALID_BALANCES')
    matches = [row for row in balances if row['coin'] == 'USDC']
    if len(matches) > 1: raise ValueError('DUPLICATE_USDC')
    # Valid successful empty account response differs from a failed request.
    return number(matches[0]['total'], 0) if matches else 0.0


def observe(client, account=None, clock=time.time):
    started = clock()
    result = {'schema_version': 2, 'observed_at': datetime.fromtimestamp(started, timezone.utc).isoformat(),
              'status': 'OBSERVED', 'funding': None, 'spot_balance': None,
              'account_status': 'NOT_CONFIGURED', 'alerts': [], 'errors': [],
              'qualification': 'UNQUALIFIED', 'source': 'hyperliquid_public',
              'freshness': 'RECEIPT_WINDOW_ONLY_SOURCE_TIMESTAMP_UNAVAILABLE'}
    try:
        result['funding'] = funding_rows(*client.meta_and_asset_contexts())
        for row in result['funding']:
            if row['rate'] > HIGH_RATE or row['rate'] < NEGATIVE_RATE:
                result['alerts'].append({'type': 'HIGH_FUNDING' if row['rate'] > 0 else 'NEGATIVE_FUNDING',
                                         'coin': row['coin'], 'rate': row['rate']})
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        result['errors'].append({'source': 'funding', 'error': type(exc).__name__})
    if account is not None:
        if not re.fullmatch(r'0x[0-9a-fA-F]{40}', account):
            raise ValueError('INVALID_ACCOUNT')
        try:
            result['spot_balance'] = spot_usdc(client._post_info({'type': 'spotClearinghouseState', 'user': account}))
            result['account_status'] = 'OBSERVED'
            if result['spot_balance'] < 1:
                result['alerts'].append({'type': 'LOW_BALANCE', 'balance': result['spot_balance']})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result['account_status'] = 'FAILED'
            result['errors'].append({'source': 'account', 'error': type(exc).__name__})
    elapsed = clock() - started
    result['elapsed_seconds'] = elapsed
    if not math.isfinite(elapsed) or not 0 <= elapsed <= 30:
        result['errors'].append({'source': 'clock', 'error': 'INVALID_RECEIPT_WINDOW'})
    if result['errors']:
        result['status'] = 'FAILED'
        result['alerts'] = []  # Never issue actionable alerts from a failed snapshot.
    return result


def save_new(directory, result):
    # Existing owned directory only, no symlink traversal or hourly overwrite.
    directory = Path(directory).absolute()
    if not directory.is_dir() or any(p.is_symlink() for p in (directory, *directory.parents)):
        raise ValueError('UNSAFE_OUTPUT_DIRECTORY')
    import uuid
    path = directory / ('monitor_v2_' + uuid.uuid4().hex + '.json')
    with path.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, allow_nan=False)
        stream.write('\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account')
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args(argv)
    try:
        result = observe(HyperliquidReadOnly(), args.account)
        if args.output_dir: save_new(args.output_dir, result)
        print(json.dumps(result, allow_nan=False))
        return 1 if result['errors'] else 0
    except (OSError, ValueError) as exc:
        print(json.dumps({'status': 'FAILED', 'error': type(exc).__name__, 'qualification': 'UNQUALIFIED'}))
        return 1


if __name__ == '__main__': sys.exit(main())
