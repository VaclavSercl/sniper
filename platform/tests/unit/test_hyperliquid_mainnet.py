"""Offline mainnet preparation, mandatory admission and no replay on timeout."""
from contextlib import closing
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'gateway'))
import hyperliquid_mainnet as mainnet
import mainnet_admission as admission
from test_hyperliquid_orders import FakeVenue,AGENT,ACCOUNT,NOW

CANDIDATE='a'*64


@unittest.skipUnless(os.name=='posix','Linux durable execution contract')
class MainnetBoundary(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.venue=FakeVenue();self.signatures=[];self.now=NOW
        self.venue.clock=lambda:self.now
        self.authority=patch.object(admission,'current_mandate',return_value={})
        self.authority.start();self.addCleanup(self.authority.stop)
        def sign(*args):self.signatures.append(args);return {'r':'0x1','s':'0x2','v':27}
        self.client=mainnet.MainnetClient(Path(self.tmp.name)/'orders',SimpleNamespace(address=AGENT),ACCOUNT,
            '20','100',NOW+3600000,candidate=CANDIDATE,permit_dir=Path(self.tmp.name)/'permits',
            send=self.venue,signer=sign,clock=lambda:self.now)
    def tearDown(self):self.client.close();self.tmp.cleanup()
    def prepare(self):return self.client.submit('first','spot','HYPE/USDC','buy','20','1')
    def permit(self):
        intent=json.loads(self.client.con.execute('SELECT intent FROM operations WHERE id=?',('order:first',)).fetchone()[0])
        return {'schema':1,'network':'MAINNET','account':ACCOUNT,'agent':AGENT,'candidate':CANDIDATE,
            'action_sha256':admission.ex.sha(intent['action']),'binding_sha256':admission.ex.sha(json.loads(self.client.binding())),
            'issued_ms':self.now,'expires_ms':self.now+5000,'notional':'20','guardian_sha256':'b'*64,
            'qualification_sha256':'c'*64,'mandate_sha256':'d'*64,'purpose':'QUALIFIED_STRATEGY_CANARY'}
    def test_prepare_never_signs_or_sends_and_preserves_network_nonce_binding(self):
        result=self.prepare();self.assertEqual(result['status'],'PREPARED_GUARDIAN_REQUIRED')
        self.assertEqual(self.signatures,[]);self.assertFalse(any(k=='exchange' for k,_ in self.venue.requests))
        binding=json.loads(self.client.binding());self.assertEqual(binding['network'],'mainnet')
        intent=json.loads(self.client.con.execute('SELECT intent FROM operations').fetchone()[0])
        self.assertEqual(intent['action']['orders'][0]['t'],{'limit':{'tif':'Ioc'}})
        self.assertTrue((Path(self.tmp.name)/'orders/mainnet.sqlite3').exists())
    def test_missing_guardian_cannot_dispatch_prepared_order(self):
        self.prepare()
        with self.assertRaises(OSError):self.client.dispatch_authorized('order:first')
        self.assertEqual(self.signatures,[])
    def test_mock_permit_timeout_is_unknown_and_never_replayed(self):
        self.prepare();self.venue.fail=TimeoutError('synthetic')
        with patch.object(admission,'trusted',return_value=self.permit()):
            result=self.client.dispatch_authorized('order:first')
            self.assertEqual(result['status'],'UNKNOWN_REQUIRES_RECONCILIATION')
            with self.assertRaises(ValueError):self.client.dispatch_authorized('order:first')
        self.assertEqual(len(self.signatures),1)
        self.assertEqual(sum(k=='exchange' for k,_ in self.venue.requests),1)
    def test_mutated_expired_or_wrong_candidate_permit_never_signs(self):
        self.prepare()
        for key,value in [('expires_ms',NOW),('candidate','e'*64),('action_sha256','f'*64),('purpose','OWNED_RISK_REDUCTION')]:
            permit={**self.permit(),key:value}
            with patch.object(admission,'trusted',return_value=permit),self.assertRaises(ValueError):
                self.client.dispatch_authorized('order:first')
        self.assertEqual(self.signatures,[])
    def test_perpetual_and_maker_orders_are_not_qualified_spot_model(self):
        with self.assertRaises(ValueError):self.client.submit('perp','perpetual','BTC','buy','20','1')
        with self.assertRaises(ValueError):self.client.submit('maker','spot','HYPE/USDC','buy','20','1',post_only=True)
        self.assertEqual(self.signatures,[])
    def test_network_transfer_action_rejected_before_transport(self):
        with patch.object(mainnet.urllib.request,'build_opener') as network:
            with self.assertRaises(ValueError):mainnet.transport('exchange',{'action':{'type':'usdSend'}})
        network.assert_not_called()
    def test_owned_cancel_is_prepared_and_requires_exact_reduction_permit(self):
        self.prepare()
        with patch.object(admission,'trusted',return_value=self.permit()):self.client.dispatch_authorized('order:first')
        self.venue.acknowledge(self.client,'open')
        self.now=NOW+60001
        result=self.client.cancel('first')
        self.assertEqual(result['status'],'PREPARED_GUARDIAN_REQUIRED')
        intent=json.loads(self.client.con.execute("SELECT intent FROM operations WHERE id='cancel:first'").fetchone()[0])
        permit={**self.permit(),'action_sha256':admission.ex.sha(intent['action']),'notional':'0','purpose':'OWNED_RISK_REDUCTION'}
        with patch.object(admission,'trusted',return_value=permit):self.client.dispatch_authorized('cancel:first')
        self.assertEqual(sum(k=='exchange' for k,_ in self.venue.requests),2)

    def test_permit_expiry_reaches_wire_and_slow_signing_cannot_send(self):
        self.prepare();permit=self.permit()
        def slow(*args):
            self.assertEqual(args[-1],permit['expires_ms']);self.now+=5001
            return {'r':'0x1','s':'0x2','v':27}
        self.client.signer=slow
        with patch.object(admission,'trusted',return_value=permit):
            result=self.client.dispatch_authorized('order:first')
        self.assertEqual(result['status'],'UNKNOWN_REQUIRES_RECONCILIATION')
        self.assertFalse(any(k=='exchange' for k,_ in self.venue.requests))
        self.assertFalse(self.client.con.execute("SELECT 1 FROM events WHERE status='DISPATCH_STARTED'").fetchone())

    def test_mandate_revoked_during_signing_prevents_transport(self):
        self.prepare()
        with patch.object(admission,'trusted',return_value=self.permit()),patch.object(
                admission,'current_mandate',side_effect=[{}, {}, ValueError('revoked')]):
            result=self.client.dispatch_authorized('order:first')
        self.assertEqual(result['status'],'UNKNOWN_REQUIRES_RECONCILIATION')
        self.assertEqual(len(self.signatures),1)
        self.assertFalse(any(k=='exchange' for k,_ in self.venue.requests))

    def test_exact_short_expiry_on_transport(self):
        self.prepare();permit=self.permit()
        with patch.object(admission,'trusted',return_value=permit):self.client.dispatch_authorized('order:first')
        payload=next(p for k,p in self.venue.requests if k=='exchange')
        self.assertEqual(payload['expiresAfter'],permit['expires_ms'])

    def test_consumed_permit_prevalidation_failure_has_explicit_unsent_recovery(self):
        self.prepare();permit=self.permit()
        with self.client.transaction():self.client.risk.invalidate()
        with patch.object(admission,'trusted',return_value=permit),self.assertRaises(ValueError):
            self.client.dispatch_authorized('order:first')
        with self.assertRaises(ValueError):self.client.reconcile_unsent('order:first')
        self.now+=60000  # Fresh request window, within the unchanged risk gap bound.
        result=self.client.reconcile_unsent('order:first')
        self.assertEqual(result['status'],'UNSENT_RECONCILED')
        self.assertEqual(self.client.con.execute('SELECT count(*) FROM guardian_permits').fetchone()[0],1)
        self.assertEqual(self.signatures,[])
        with self.assertRaises(ValueError):self.client.dispatch_authorized('order:first')
        self.assertFalse(any(k=='exchange' for k,_ in self.venue.requests))

    def test_unknown_transport_cannot_be_declared_unsent(self):
        self.prepare();self.venue.fail=TimeoutError()
        with patch.object(admission,'trusted',return_value=self.permit()):self.client.dispatch_authorized('order:first')
        self.now+=10001
        with self.assertRaises(ValueError):self.client.reconcile_unsent('order:first')

    def test_owned_exit_survives_exhausted_entry_cap_and_latched_halt(self):
        self.client.close()
        self.client=mainnet.MainnetClient(Path(self.tmp.name)/'exit',SimpleNamespace(address=AGENT),ACCOUNT,
            '20','20',NOW+3600000,candidate=CANDIDATE,permit_dir=Path(self.tmp.name)/'permits',
            send=self.venue,signer=lambda *_:{'r':'0x1','s':'0x2','v':27},clock=lambda:self.now)
        self.prepare()
        with patch.object(admission,'trusted',return_value=self.permit()):self.client.dispatch_authorized('order:first')
        self.venue.acknowledge(self.client,'filled');self.client.reconcile('first')
        self.now+=60000  # Previous reads leave the rolling request window.
        with self.client.transaction():
            state=self.client.risk.state();state['halted']=True;self.client.risk._save(state)
        result=self.client.submit('exit','spot','HYPE/USDC','sell','30','1')
        self.assertEqual(result['status'],'PREPARED_GUARDIAN_REQUIRED')
        self.assertTrue(self.client.risk.state()['halted'])
        intent=json.loads(self.client.con.execute("SELECT intent FROM operations WHERE id='order:exit'").fetchone()[0])
        permit={**self.permit(),'purpose':'OWNED_RISK_REDUCTION','notional':'30','action_sha256':admission.ex.sha(intent['action'])}
        with patch.object(admission,'trusted',return_value=permit):self.client.dispatch_authorized('order:exit')
        self.assertEqual(sum(k=='exchange' for k,_ in self.venue.requests),2)

    def test_foreign_inventory_cannot_use_exit_cap_exception(self):
        self.venue.balances[1]['total']='10'
        with self.assertRaises(ValueError):self.client.submit('foreign','spot','HYPE/USDC','sell','30','1')
        self.assertFalse(self.client.con.execute('SELECT 1 FROM operations').fetchone())


if __name__=='__main__':unittest.main()
