#!/usr/bin/env python3
"""Read-only account observation. Public identity configuration, no signing key.

An agent's address must be resolved to the actual trading account. These reads
neither grant a mandate nor verify a funded execution adapter or agent expiry.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request

ENDPOINT='https://api.hyperliquid.xyz/info'
ALLOWED={'userRole','clearinghouseState','spotClearinghouseState','openOrders','userFees'}
LIMIT=4*1024*1024


def safe(path):
    path=Path(path).absolute()
    if any(p.is_symlink() for p in (path,*path.parents)):
        raise ValueError('Unsafe account path')
    if path.is_file() and path.stat().st_nlink!=1:raise ValueError('Hardlinked account file')
    return path


def unique(items):
    result={}
    for key,value in items:
        if key in result:raise ValueError('Duplicate account key')
        result[key]=value
    return result


def decode(raw):
    if len(raw)>LIMIT:raise ValueError('Account response exceeds bound')
    def invalid(_):raise ValueError('Nonfinite account JSON')
    return json.loads(raw,object_pairs_hook=unique,parse_constant=invalid)


def address(value):
    if not isinstance(value,str) or not re.fullmatch('0x[0-9a-fA-F]{40}',value) or int(value[2:],16)==0:
        raise ValueError('Invalid public account address')
    return value.lower()


def numeric(value,nonnegative=False):
    if not isinstance(value,str) or len(value)>64:raise ValueError('Invalid account number')
    try:n=Decimal(value)
    except InvalidOperation:raise ValueError('Invalid account number') from None
    if not n.is_finite() or n.as_tuple().exponent < -30 or n.as_tuple().exponent > 15 or n.copy_abs()>Decimal('1e15') or nonnegative and n<0:
        raise ValueError('Unbounded account number')
    return value


def identity(config):
    if (set(config)!={'schema','account','signer','account_role','evidence'} or
            type(config['schema']) is not int or config['schema']!=1 or
            config['account_role'] not in ('user','subAccount') or
            not isinstance(config['evidence'],str) or not re.fullmatch('[0-9a-f]{64}',config['evidence'])):
        raise ValueError('Unverified account configuration')
    result=dict(config);result['account']=address(config['account']);result['signer']=address(config['signer'])
    if result['account']==result['signer']:raise ValueError('This integration requires a delegated agent')
    return result


def post(kind,user):
    if kind not in ALLOWED:raise ValueError('Unsupported account read')
    payload=json.dumps({'type':kind,'user':address(user)},separators=(',',':')).encode()
    request=urllib.request.Request(ENDPOINT,data=payload,headers={'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(request,timeout=20) as response:raw=response.read(LIMIT+1)
    return decode(raw)


def observe(config,now,fetch=post):
    config=identity(config)
    if type(now) is not int or now<=0:raise ValueError('Invalid observation time')
    account=config['account'];signer=config['signer']
    # Never infer an empty account from agent wallet balances, and revalidate
    # the relationship on every run rather than trusting a stale config flag.
    role=fetch('userRole',signer)
    if role.get('role')!='agent' or address(role['data']['user'])!=account:
        raise ValueError('Delegated account association changed')
    actual=fetch('userRole',account)
    if actual.get('role')!=config['account_role']:raise ValueError('Actual account role changed')
    if actual['role']=='subAccount':address(actual['data']['master'])
    perp=fetch('clearinghouseState',account);spot=fetch('spotClearinghouseState',account)
    orders=fetch('openOrders',account);fees=fetch('userFees',account)
    if not isinstance(orders,list) or len(orders)>10000:raise ValueError('Invalid open order observation')
    positions=perp['assetPositions'];balances=spot['balances']
    if not isinstance(positions,list) or len(positions)>10000 or not isinstance(balances,list) or len(balances)>10000:
        raise ValueError('Invalid account observation')
    for position in positions:numeric(position['position']['szi'])
    summary=[];coins=set()
    for balance in balances:
        coin=balance['coin']
        if not isinstance(coin,str) or not re.fullmatch('[A-Za-z0-9._-]{1,64}',coin) or coin in coins:
            raise ValueError('Invalid spot balance identity')
        coins.add(coin);total=numeric(balance['total'],True);hold=numeric(balance['hold'],True)
        if Decimal(hold)>Decimal(total):raise ValueError('Invalid held balance')
        summary.append({'coin':coin,'total':total,'hold':hold})
    rate_keys=('userCrossRate','userAddRate','userSpotCrossRate','userSpotAddRate')
    rates={key:numeric(fees[key]) for key in rate_keys}
    if any(abs(Decimal(v))>1 for v in rates.values()) or any(Decimal(rates[k])<0 for k in (rate_keys[0],rate_keys[2])):
        raise ValueError('Invalid actual fee rates')
    return {'schema':1,'status':'ACCOUNT_OBSERVED_READ_ONLY','observed_ms':now,
        'account_masked':account[:8]+'...'+account[-4:],'account_role':actual['role'],
        'identity_evidence':config['evidence'],'association_revalidated':True,
        'perpetual_equity':numeric(perp['marginSummary']['accountValue']),
        'perpetual_withdrawable':numeric(perp['withdrawable'],True),
        'positions':len(positions),'open_orders':len(orders),'spot_balances':sorted(summary,key=lambda b:b['coin']),
        'fees':rates,'live_enabled':False,'mandate':'NOT_CONFIGURED',
        'agent_expiry_and_signed_execution':'NOT_VERIFIED'}


def write_snapshot(root,result):
    root=safe(root);root.mkdir(mode=0o700,parents=True,exist_ok=True)
    target=safe(root/'account.json');temporary=safe(root/'account.new')
    if temporary.exists():raise ValueError('Interrupted account write requires reconciliation')
    with temporary.open('xb') as stream:
        os.chmod(temporary,0o600)
        stream.write(json.dumps(result,sort_keys=True,allow_nan=False).encode());stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,target)
    fd=os.open(root,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def summary(root,now=None):
    path=safe(Path(root)/'account.json')
    if not path.exists():return {'status':'NOT_STARTED','live_enabled':False}
    data=decode(path.read_bytes());now=int(time.time()*1000) if now is None else now
    if data.get('schema')!=1 or data.get('live_enabled') is not False:
        raise ValueError('Unsupported account observation')
    stamp=data.get('observed_ms')
    if type(stamp) is not int or stamp>now+30000 or now-stamp>3600000:
        return {'status':'STALE','live_enabled':False}
    if data.get('status')=='BLOCKED':return {'status':'BLOCKED','error_type':data.get('error_type'),'live_enabled':False}
    allowed={'schema','status','observed_ms','account_masked','account_role','association_revalidated',
        'perpetual_equity','perpetual_withdrawable','positions','open_orders','spot_balances','fees',
        'live_enabled','mandate','agent_expiry_and_signed_execution'}
    if data.get('status')!='ACCOUNT_OBSERVED_READ_ONLY' or data.get('association_revalidated') is not True:
        raise ValueError('Unverified account observation')
    return {key:data[key] for key in allowed}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action',choices=('observe','status'))
    ap.add_argument('--config',type=Path,default=Path('/etc/sniper/hyperliquid-account.json'))
    ap.add_argument('--state-dir',type=Path,default=Path('/var/lib/sniper/hyperliquid-account'))
    args=ap.parse_args();os.umask(0o077)
    if args.action=='status':result=summary(args.state_dir)
    else:
        import fcntl
        root=safe(args.state_dir);root.mkdir(mode=0o700,parents=True,exist_ok=True)
        with safe(root/'observe.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            now=int(time.time()*1000)
            try:
                config=safe(args.config)
                if config.stat().st_size>4096:raise ValueError('Oversized identity config')
                result=observe(decode(config.read_bytes()),now)
            except (OSError,ValueError,KeyError,TypeError) as exc:
                result={'schema':1,'status':'BLOCKED','observed_ms':now,'live_enabled':False,'error_type':type(exc).__name__}
            write_snapshot(root,result)
    print(json.dumps(result,indent=2,allow_nan=False))
    return 2 if result['status']=='BLOCKED' else 0


if __name__=='__main__':raise SystemExit(main())
