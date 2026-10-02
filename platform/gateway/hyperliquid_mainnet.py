"""Prepared mainnet spot orders with mandatory root-issued operation admission.

No CLI, private-key reader, funded daemon, promotion shortcut or automatic retry.
Call submit to PREPARE only; a separately issued permit is needed for dispatch.
This library has offline tests; actual signed exchange execution stays unverified.
"""
from importlib.metadata import version
from decimal import Decimal
import json
from pathlib import Path
import re
import urllib.request

import hyperliquid_orders as orders
import mainnet_admission as admission
from order_validation import integer

MAINNET='https://api.hyperliquid.xyz'


def sdk_sign(wallet, action, nonce, expires):
    if version('hyperliquid-python-sdk')!='0.24.0': raise ValueError('Unverified SDK version')
    from hyperliquid.utils.signing import sign_l1_action
    return sign_l1_action(wallet,action,None,nonce,expires,True)


def transport(kind,payload):
    if kind not in ('info','exchange'): raise ValueError('Unsupported mainnet endpoint')
    if kind=='info' and payload.get('type') not in (
        'meta','spotMeta','userRole','spotClearinghouseState','clearinghouseState',
        'orderStatus','openOrders','userFees','l2Book','userNonFundingLedgerUpdates','userFillsByTime'):
        raise ValueError('Unsupported read-only mainnet action')
    if kind=='exchange' and payload.get('action',{}).get('type') not in ('order','cancelByCloid'):
        raise ValueError('Transfers/approvals unsupported')
    raw=orders.request_bytes(payload)
    if len(raw)>8192:raise ValueError('Oversized mainnet request')
    request=urllib.request.Request(MAINNET+'/'+kind,raw,{'Content-Type':'application/json'},method='POST')
    with urllib.request.build_opener(orders.NoRedirect()).open(request,timeout=10) as response:
        if response.geturl()!=MAINNET+'/'+kind or response.status!=200:raise ValueError('Unexpected exchange endpoint')
        data=response.read(orders.LIMIT+1)
    if len(data)>orders.LIMIT:raise ValueError('Oversized exchange response')
    return orders.decode(data)


class MainnetClient(orders.TestnetClient):
    journal_name='mainnet.sqlite3'

    @staticmethod
    def prepare_action(instrument,side,price,quantity,post_only,reduce_only,cloid):
        if post_only:raise ValueError('Qualified spot model requires IOC, not maker orders')
        action=orders.prepare(instrument,side,price,quantity,post_only,reduce_only,cloid)
        action['orders'][0]['t']['limit']['tif']='Ioc'
        return action

    def __init__(self,root,wallet,account,order_cap,session_cap,expires_ms,*,candidate,
                 permit_dir,send=transport,signer=sdk_sign,clock=None):
        if not isinstance(candidate,str) or not re.fullmatch('[0-9a-f]{64}',candidate):
            raise ValueError('Exact qualified candidate identity required')
        self.candidate=candidate
        self.permit_dir=admission.mandate.safe(permit_dir)
        super().__init__(root,wallet,account,order_cap,session_cap,expires_ms,
                         send=send,signer=signer,clock=clock)

    def binding(self):
        value=json.loads(super().binding());value['network']='mainnet';value['candidate']=self.candidate
        return orders.encode(value)

    def submit(self,*args,**kwargs):
        product=kwargs.get('product',args[1] if len(args)>1 else None)
        if product!='spot':raise ValueError('Mainnet perpetual qualification is unavailable')
        kwargs.setdefault('post_only',False)
        return super().submit(*args,**kwargs)

    def dispatch(self,operation,action,nonce,expires):
        # Parent reserves the intent/nonce/risk transaction before calling this.
        # Preparing an order/cancel never signs or contacts /exchange.
        return {'operation':operation,'status':'PREPARED_GUARDIAN_REQUIRED',
                'network':'MAINNET','exchange_write':False,'live_eligible':False}

    def dispatch_authorized(self,operation):
        row=self.con.execute('SELECT nonce,intent,status,result FROM operations WHERE id=?',(operation,)).fetchone()
        if row is None or row[2]!='PREPARED':raise ValueError('Only an unused exact prepared operation')
        intent=json.loads(row[1]);result=json.loads(row[3]);action=intent['action']
        filename=__import__('hashlib').sha256(operation.encode()).hexdigest()+'.json'
        permit=admission.check_permit(self.permit_dir/filename,action,json.loads(self.binding()),integer(self.clock()))
        expires=min(result['expires_ms'],permit['expires_ms'])
        with self.transaction():
            self.con.execute('CREATE TABLE IF NOT EXISTS guardian_permits (sha TEXT PRIMARY KEY,operation TEXT UNIQUE)')
            self.con.execute('INSERT INTO guardian_permits VALUES(?,?)',(admission.ex.sha(permit),operation))
            self.record(operation,'PREPARED',{'nonce':row[0],'expires_ms':expires,
                                             'permit_sha256':admission.ex.sha(permit)})
        # Permit consumption survives failed signing/transport and cannot be replayed.
        return super().dispatch(operation,action,row[0],expires)

    def check_dispatch_authority(self,operation,action,nonce,expires):
        filename=__import__('hashlib').sha256(operation.encode()).hexdigest()+'.json'
        now=integer(self.clock())
        permit=admission.check_permit(self.permit_dir/filename,action,json.loads(self.binding()),now)
        used=self.con.execute('SELECT sha FROM guardian_permits WHERE operation=?',(operation,)).fetchone()
        if (used!=(admission.ex.sha(permit),) or expires>permit['expires_ms'] or now>=expires):
            raise ValueError('Consumed exact permit expired or changed')

    def check_submission_limits(self,product,side,notional):
        if product!='spot':raise ValueError('Only qualified spot preparation')
        if side=='sell':return  # Subsequent complete owned-fill proof is mandatory.
        spent=sum((orders.number(json.loads(r[0])['notional']) for r in self.con.execute(
            "SELECT intent FROM operations WHERE kind='order'") if json.loads(r[0])['action']['orders'][0]['b']),Decimal(0))
        if notional>self.order_cap or spent+notional>self.session_cap:
            raise ValueError('Mainnet new-entry order/lifetime limit')

    def observe_submission(self,instrument,side):
        # The complete fill/account proof must run for sells too: a free balance
        # alone could be a foreign deposit. RiskBook.observe preserves any halt.
        self.observe_risk(instrument)

    def reconcile_unsent(self,operation):
        """Only expired PREPARED with no dispatch ever; caller owns writer lock.

        Opening the client acquires the journal lock, so a previous signer must
        have stopped. Unknown/started writes can never use this recovery route.
        A fresh complete owned-fill/account proof is retained, with no resubmit.
        """
        row=self.con.execute('SELECT kind,intent,status FROM operations WHERE id=?',(operation,)).fetchone()
        if row is None or row[2]!='PREPARED':raise ValueError('Only provably unsent PREPARED recovery')
        if self.con.execute("SELECT 1 FROM events WHERE operation=? AND status='DISPATCH_STARTED'",(operation,)).fetchone():
            raise ValueError('Dispatch evidence forbids unsent recovery')
        cuts=[json.loads(r[0])['expires_ms'] for r in self.con.execute(
            "SELECT evidence FROM events WHERE operation=? AND status='PREPARED'",(operation,))]
        if not cuts or integer(self.clock())<=max(cuts):raise ValueError('Original signing window still active')
        intent=json.loads(row[1])
        if row[0]=='cancel':
            client=self.con.execute('SELECT client FROM operations WHERE id=?',(operation,)).fetchone()[0]
            intent=json.loads(self.con.execute('SELECT intent FROM operations WHERE id=?',('order:'+client,)).fetchone()[0])
        self.check_role()
        instrument=orders.Instrument(**intent['instrument'])
        if self.instrument(instrument.product,instrument.symbol)!=instrument:raise ValueError('Recovery instrument changed')
        state=self.observe_risk(instrument)
        with self.transaction():
            self.record(operation,'UNSENT_RECONCILED',{'account_sha256':admission.ex.sha(state),
                'observed_ms':state['time_ms'],'original_expiry_ms':max(cuts),'replayed':False})
        return {'status':'UNSENT_RECONCILED','exchange_write':False}
