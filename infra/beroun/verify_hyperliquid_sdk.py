#!/usr/bin/env python3
"""Offline official SDK signature regression. No wallet secrets or network.

Execute with the separately hash-locked SDK interpreter. This verifies signing
primitives, not transport, testnet execution, fill reconciliation or live safety.
"""
import importlib.metadata
import json


def verify():
    from eth_account import Account
    from eth_account.messages import encode_typed_data
    from hyperliquid.utils.signing import action_hash,construct_phantom_agent,l1_payload,sign_l1_action
    if importlib.metadata.version('hyperliquid-python-sdk')!='0.24.0':
        raise ValueError('Unreviewed SDK version')
    wallet=Account.from_key(bytes([1])*32)
    nonce=1700000000000
    actions=[{'type':'cancel','cancels':[{'a':5,'o':1}]},
        {'type':'order','orders':[{'a':5,'b':True,'p':'150','s':'1','r':False,'t':{'limit':{'tif':'Ioc'}}}],
         'grouping':'na'}]
    checks=0
    for action in actions:
        signature=sign_l1_action(wallet,action,None,nonce,None,True)
        vrs=(signature['v'],int(signature['r'],16),int(signature['s'],16))
        def recover(candidate,candidate_nonce=nonce,mainnet=True,vault=None):
            message=encode_typed_data(full_message=l1_payload(construct_phantom_agent(
                action_hash(candidate,vault,candidate_nonce,None),mainnet)))
            return Account.recover_message(message,vrs=vrs).lower()
        if recover(action)!=wallet.address.lower():raise ValueError('Signature recovery failed')
        if recover(action,nonce+1)==wallet.address.lower():raise ValueError('Nonce mutation accepted')
        if recover(action,mainnet=False)==wallet.address.lower():raise ValueError('Network mutation accepted')
        if recover(action,vault='0x'+'12'*20)==wallet.address.lower():raise ValueError('Account routing mutation accepted')
        changed=dict(action);changed['type']='noop'
        if recover(changed)==wallet.address.lower():raise ValueError('Action mutation accepted')
        checks+=5
    return {'status':'SDK_PRIMITIVES_VERIFIED_OFFLINE','checks':checks,'sdk_version':'0.24.0',
        'synthetic_keys_only':True,'network_calls':0,'signed_execution_adapter':'NOT_VERIFIED',
        'testnet_execution':'NOT_TESTED','live_execution':'DISABLED'}


if __name__=='__main__':print(json.dumps(verify(),indent=2))
