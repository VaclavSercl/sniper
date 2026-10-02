"""Root-issued, exact-operation mainnet permits; qualification is recomputed.

This issuer is not a background guardian. It requires independent fresh guardian
evidence, actual testnet verification and root-owned mandate before any permit.
Nothing creates these prerequisites from a flag or from mock test results.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'research'))
import forward_pipeline as pipeline
import execution_evidence as ex
import hyperliquid_mandate as mandate
from order_validation import integer, address, number, wire
from portfolio_risk import PROFILE, MAX_AGE_MS

MAX_PERMIT_MS = 5000
MANDATE_PATH = Path('/etc/sniper/hyperliquid-mandate.json')
IDENTITY_PATH = Path('/etc/sniper/hyperliquid-account.json')


def trusted(path, limit=65536):
    path = mandate.safe(path); info = path.stat()
    if os.name != 'posix' or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o640:
        raise ValueError('Root-owned read-only guardian evidence required')
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise ValueError('Unbounded guardian evidence')
    if any(p.stat().st_uid != 0 or p.stat().st_mode & 0o022 for p in path.parents):
        raise ValueError('Unsafe guardian evidence parent')
    from hyperliquid_orders import decode
    return decode(path.read_bytes())


def qualification(epoch, candidate_id, now):
    if not isinstance(candidate_id,str) or not re.fullmatch('[0-9a-f]{64}',candidate_id):
        raise ValueError('Exact candidate identity required')
    path = mandate.safe(Path(epoch)/'forward.sqlite3')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as con:
        con.execute('BEGIN'); pipeline.validate_store(con)
        value = pipeline.candidate(con,candidate_id)
        result = pipeline.paper_result(con,candidate_id,now)
        return {'candidate':candidate_id,'source_sha256':value['source_sha256'],
                'market':value['market'],'paper':result,
                'calibration':pipeline.calibration_snapshot(con,now),
                'qualified':result['qualified'] is True}


def validate_guardian(value, account, agent, candidate_id, now, reducing=False):
    required={'schema','network','account','agent','candidate','observed_ms','profile',
              'budget','allocation','order_cap','daily_loss','drawdown','halted',
              'equity','available','inventory_quantity','pending_notional','owned_cloids',
              'ownership_verified','reconciliation_complete','transport_proof_sha256'}
    if set(value)!=required or type(value['schema']) is not int or value['schema']!=1:
        raise ValueError('Unsupported guardian observation')
    if value['network']!='MAINNET' or value['profile']!=PROFILE:
        raise ValueError('Guardian network/risk profile differs')
    if address(value['account'])!=account or address(value['agent'])!=agent or value['candidate']!=candidate_id:
        raise ValueError('Guardian account/signer/candidate binding differs')
    if not 0<=now-integer(value['observed_ms'])<=MAX_AGE_MS:
        raise ValueError('Guardian observation stale or future')
    if any(value[k] is not True for k in ('ownership_verified','reconciliation_complete')) or type(value['halted']) is not bool or value['halted'] and not reducing:
        raise ValueError('Guardian blocks new risk')
    budget=number(value['budget']); allocation=number(value['allocation']); cap=number(value['order_cap'])
    equity=number(value['equity']); available=number(value['available'],False)
    number(value['inventory_quantity'],False); pending=number(value['pending_notional'],False)
    if budget>equity*number(PROFILE['capital']) or available>equity or pending>allocation or allocation>budget*number(PROFILE['strategy']) or cap>allocation*number(PROFILE['order']):
        raise ValueError('Guardian exceeds pinned allocation limits')
    if not isinstance(value['owned_cloids'],list) or len(value['owned_cloids'])>100 or any(not re.fullmatch('0x[0-9a-f]{32}',c) for c in value['owned_cloids']):
        raise ValueError('Invalid independently owned order inventory')
    if not reducing and (number(value['daily_loss'],False)>=budget*number(PROFILE['daily_loss']) or number(value['drawdown'],False)>=budget*number(PROFILE['drawdown'])):
        raise ValueError('Guardian loss/drawdown breach')
    if not reducing and cap<10: raise ValueError('No order fits venue minimum and current risk cap')
    if not re.fullmatch('[0-9a-f]{64}',value['transport_proof_sha256']):
        raise ValueError('Missing transport evidence identity')
    return value


def testnet_evidence(root_proof, now):
    proof=trusted(root_proof)
    if set(proof)!={'schema','journal','journal_sha256','observed_ms','sdk_version','sdk_boundary_sha256'} or proof['schema']!=1:
        raise ValueError('Actual testnet journal proof required')
    if proof['sdk_version']!='0.24.0' or not 0<=now-integer(proof['observed_ms'])<=7*86400000:
        raise ValueError('Stale or unverified transport evidence')
    path=mandate.safe(proof['journal'])
    if path.stat().st_size>32*1024*1024 or hashlib.sha256(path.read_bytes()).hexdigest()!=proof['journal_sha256']:
        raise ValueError('Pinned actual testnet journal changed')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as con:
        binding=json.loads(con.execute("SELECT value FROM settings WHERE key='binding'").fetchone()[0])
        if binding['network']!='testnet':raise ValueError('Wrong verification network')
        rows=con.execute('SELECT kind,status FROM operations').fetchall()
        if ('order','RECONCILED_FILLED') not in rows or ('cancel','RECONCILED_TERMINAL') not in rows:
            raise ValueError('Actual filled and canceled testnet cases unavailable')
        counts=con.execute('SELECT count(*),count(DISTINCT nonce) FROM operations').fetchone()
        if counts[0]!=counts[1]:
            raise ValueError('Testnet nonce integrity differs')
        if any(not status.startswith('RECONCILED_') for _,status in rows):
            raise ValueError('Unresolved actual testnet operation')
    if not re.fullmatch('[0-9a-f]{64}',proof['sdk_boundary_sha256']):
        raise ValueError('Offline signing boundary evidence missing')
    return ex.sha(proof)


def issue(epoch, candidate_id, intent, binding, now, *, guardian_path,
          transport_proof, mandate_path, identity_path):
    if os.geteuid()!=0: raise ValueError('Separate privileged permit issuer required')
    config=mandate.load(mandate_path,identity_path)
    identity=trusted(identity_path)
    account=address(binding['account']); agent=address(binding['agent'])
    if binding['network']!='mainnet' or binding['candidate']!=candidate_id or account!=address(identity['account']) or agent!=address(identity['signer']):
        raise ValueError('Account mandate differs from operation journal')
    chain=qualification(epoch,candidate_id,now)
    action=intent['action']
    cancel=action['type']=='cancelByCloid'
    reducing=cancel or (action['type']=='order' and len(action['orders'])==1 and action['orders'][0]['b'] is False)
    if not reducing and config['live_executor'] != 'QUALIFIED_SPOT_IOC_V1':
        raise ValueError('Current mandate has no reviewed live entry executor')
    if not reducing and (not chain['qualified'] or chain['calibration']['status']!='PASS'):
        raise ValueError('Historical/calibration/30 real-day paper chain not qualified')
    guardian=validate_guardian(trusted(guardian_path),account,agent,candidate_id,now,reducing)
    if testnet_evidence(transport_proof,now)!=guardian['transport_proof_sha256']:
        raise ValueError('Guardian transport proof differs')
    if cancel:
        if len(action['cancels'])!=1 or action['cancels'][0]['asset']!=chain['market']['asset_id'] or action['cancels'][0]['cloid'] not in guardian['owned_cloids']:
            raise ValueError('Only exact owned spot cancellation')
        notional=number('0',False)
    else:
        instrument=intent['instrument']
        if instrument['product']!='spot' or instrument['symbol']!=chain['market']['symbol'] or instrument['asset']!=chain['market']['asset_id']:
            raise ValueError('Instrument not qualified')
        if action['type']!='order' or len(action['orders'])!=1 or action['orders'][0]['r'] is not False or action['orders'][0]['t']!={'limit':{'tif':'Ioc'}}:
            raise ValueError('Only exact model-compatible spot IOC orders')
        notional=number(intent['notional'])
        if not reducing and (not 10<=notional<=number(guardian['order_cap']) or notional>number(binding['order_cap'])):
            raise ValueError('Exact order exceeds independent cap')
        if reducing and number(action['orders'][0]['s'])>number(guardian['inventory_quantity'],False):
            raise ValueError('Exit exceeds independently observed owned inventory')
        if not reducing and (notional*number('1.01')>number(guardian['available'],False) or notional+number(guardian['pending_notional'],False)>number(guardian['allocation'])):
            raise ValueError('Independent free cash/exposure unavailable')
    return {'schema':1,'network':'MAINNET','account':account,'agent':agent,'candidate':candidate_id,
            'action_sha256':ex.sha(action),'binding_sha256':ex.sha(binding),
            'issued_ms':now,'expires_ms':now+MAX_PERMIT_MS,'notional':wire(notional),
            'guardian_sha256':ex.sha(guardian),'qualification_sha256':ex.sha(chain),
            'mandate_sha256':ex.sha(config),'purpose':'OWNED_RISK_REDUCTION' if reducing else 'QUALIFIED_STRATEGY_CANARY'}


def current_mandate(permit, binding):
    """Recheck current owner policy and identity, including withdrawal of consent.

    Frozen v1 is UNAVAILABLE; enabling entries requires a reviewed migration of
    its validator. Reductions still need the present matching mandate/identity.
    """
    config=mandate.load(MANDATE_PATH,IDENTITY_PATH)
    identity=trusted(IDENTITY_PATH)
    if (ex.sha(config)!=permit['mandate_sha256'] or
        address(identity['account'])!=binding['account'] or
        address(identity['signer'])!=binding['agent']):
        raise ValueError('Current mandate revoked or account binding changed')
    if permit['purpose']=='QUALIFIED_STRATEGY_CANARY' and config['live_executor']!='QUALIFIED_SPOT_IOC_V1':
        raise ValueError('Current mandate has no reviewed live entry executor')
    return config


def check_permit(path, action, binding, now):
    permit=trusted(path)
    required={'schema','network','account','agent','candidate','action_sha256','binding_sha256',
              'issued_ms','expires_ms','notional','guardian_sha256','qualification_sha256','mandate_sha256','purpose'}
    if set(permit)!=required or permit['schema']!=1 or permit['network']!='MAINNET' or permit['purpose'] not in ('QUALIFIED_STRATEGY_CANARY','OWNED_RISK_REDUCTION'):
        raise ValueError('Unsupported operation permit')
    if not integer(permit['issued_ms'])<=now<integer(permit['expires_ms'])<=permit['issued_ms']+MAX_PERMIT_MS:
        raise ValueError('Expired/future/unbounded operation permit')
    if permit['action_sha256']!=ex.sha(action) or permit['binding_sha256']!=ex.sha(binding) or permit['candidate']!=binding['candidate']:
        raise ValueError('Permit differs from exact action/journal/candidate')
    if address(permit['account'])!=binding['account'] or address(permit['agent'])!=binding['agent']:
        raise ValueError('Permit account/signer differs')
    for k in ('guardian_sha256','qualification_sha256','mandate_sha256'):
        if not re.fullmatch('[0-9a-f]{64}',permit[k]): raise ValueError('Missing permit provenance')
    cancel=action['type']=='cancelByCloid'
    order=None if cancel else action['orders'][0]
    if permit['purpose']=='OWNED_RISK_REDUCTION' and not (cancel or order['b'] is False):
        raise ValueError('Risk-reduction permit cannot authorize entry')
    expected=number('0',False) if cancel else number(order['p'])*number(order['s'])
    if number(permit['notional'],False)!=expected:
        raise ValueError('Permit amount differs')
    current_mandate(permit,binding)
    return permit
