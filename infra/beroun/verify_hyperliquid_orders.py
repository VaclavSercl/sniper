#!/usr/bin/env python3
"""Verify the actual order boundary with the isolated SDK, without network."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'platform/gateway'))
import hyperliquid_orders as orders


def verify():
    from eth_account import Account
    from eth_account.messages import encode_typed_data
    from hyperliquid.utils.signing import action_hash, construct_phantom_agent, l1_payload
    wallet = Account.from_key(bytes([1])*32)
    nonce, expires = 1700000000000, 1700000010000
    # Synthetic metadata, deliberately not a production asset mapping.
    instrument = orders.Instrument('spot', 'FIXTURE/USDC', '@19', 10019, 2)
    action = orders.prepare(instrument, 'buy', '12.3400', '1.20', True, False, '0x'+'1'*32)
    cancel = {'type': 'cancelByCloid', 'cancels': [{'asset': 10019, 'cloid': '0x'+'1'*32}]}
    checks = 0
    for original in (action, cancel):
        signature = orders.sdk_sign(wallet, original, nonce, expires)
        wire_action = json.loads(orders.request_bytes({'action': original}))['action']
        if action_hash(wire_action, None, nonce, expires) != action_hash(original, None, nonce, expires):
            raise ValueError('Wire serialization changes signed action hash')
        vrs = (signature['v'], int(signature['r'], 16), int(signature['s'], 16))
        def recover(candidate=original, n=nonce, expiry=expires, mainnet=False, vault=None):
            message = encode_typed_data(full_message=l1_payload(construct_phantom_agent(
                action_hash(candidate, vault, n, expiry), mainnet)))
            return Account.recover_message(message, vrs=vrs).lower()
        if recover() != wallet.address.lower(): raise ValueError('Signing boundary recovery failed')
        changed = dict(original); changed['type'] = 'noop'
        for recovered in (recover(n=nonce+1), recover(expiry=expires+1), recover(mainnet=True),
                          recover(vault='0x'+'2'*40), recover(candidate=changed)):
            if recovered == wallet.address.lower(): raise ValueError('Mutation accepted by boundary')
        checks += 7
    return {'status': 'ORDER_BOUNDARY_SDK_VERIFIED_OFFLINE', 'checks': checks, 'sdk_version': '0.24.0',
            'synthetic_keys_only': True, 'network_calls': 0, 'testnet_execution': 'NOT_TESTED',
            'live_execution': 'UNAVAILABLE'}


if __name__ == '__main__': print(json.dumps(verify(), indent=2))
