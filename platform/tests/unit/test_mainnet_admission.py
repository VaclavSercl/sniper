"""Independent cap checks and real unqualified epoch rejection, without money."""
from contextlib import closing
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'gateway'))
import mainnet_admission as guard
from portfolio_risk import PROFILE
from test_hyperliquid_orders import AGENT,ACCOUNT,NOW
from test_execution_evidence import MARKET

CANDIDATE='a'*64


def observed():
    return {'schema':1,'network':'MAINNET','account':ACCOUNT,'agent':AGENT,'candidate':CANDIDATE,
        'observed_ms':NOW,'profile':PROFILE,'budget':'900','allocation':'180','order_cap':'45',
        'daily_loss':'0','drawdown':'0','halted':False,'ownership_verified':True,
        'equity':'1000','available':'1000','inventory_quantity':'0','pending_notional':'0','owned_cloids':[],
        'reconciliation_complete':True,'transport_proof_sha256':'b'*64}


class Admission(unittest.TestCase):
    def test_present_unavailable_mandate_blocks_entry_but_matching_reduction_survives(self):
        config={**guard.mandate.DEFAULTS,'account_binding_sha256':'e'*64}
        binding={'account':ACCOUNT,'agent':AGENT}
        permit={'mandate_sha256':guard.ex.sha(config),'purpose':'QUALIFIED_STRATEGY_CANARY'}
        with patch.object(guard.mandate,'load',return_value=config),patch.object(
                guard,'trusted',return_value={'account':ACCOUNT,'signer':AGENT}):
            with self.assertRaises(ValueError):guard.current_mandate(permit,binding)
            guard.current_mandate({**permit,'purpose':'OWNED_RISK_REDUCTION'},binding)
            with self.assertRaises(ValueError):guard.current_mandate({**permit,'mandate_sha256':'f'*64},binding)

    def test_deleted_or_changed_root_identity_revokes_reduction_too(self):
        config={**guard.mandate.DEFAULTS,'account_binding_sha256':'e'*64}
        permit={'mandate_sha256':guard.ex.sha(config),'purpose':'OWNED_RISK_REDUCTION'}
        with patch.object(guard.mandate,'load',side_effect=FileNotFoundError()),self.assertRaises(OSError):
            guard.current_mandate(permit,{'account':ACCOUNT,'agent':AGENT})
        with patch.object(guard.mandate,'load',return_value=config),patch.object(
                guard,'trusted',return_value={'account':ACCOUNT,'signer':'0x'+'3'*40}),self.assertRaises(ValueError):
            guard.current_mandate(permit,{'account':ACCOUNT,'agent':AGENT})

    def test_issuer_refuses_unavailable_even_with_mock_qualified_chain(self):
        config={**guard.mandate.DEFAULTS,'account_binding_sha256':'e'*64}
        binding={'network':'mainnet','candidate':CANDIDATE,'account':ACCOUNT,'agent':AGENT}
        intent={'action':{'type':'order','orders':[{'b':True}]}}
        with patch.object(guard.os,'geteuid',return_value=0),patch.object(guard.mandate,'load',return_value=config),patch.object(
                guard,'trusted',return_value={'account':ACCOUNT,'signer':AGENT}),patch.object(
                guard,'qualification',return_value={'qualified':True,'calibration':{'status':'PASS'}}),self.assertRaisesRegex(ValueError,'no reviewed live'):
            guard.issue('/unused',CANDIDATE,intent,binding,NOW,guardian_path='/unused',
                        transport_proof='/unused',mandate_path='/unused',identity_path='/unused')

    def test_90pct_allocation_order_minimum_and_loss_bounds(self):
        guard.validate_guardian(observed(),ACCOUNT,AGENT,CANDIDATE,NOW)
        for key,value in [('budget','901'),('allocation','181'),('order_cap','46'),('order_cap','9'),
                          ('daily_loss','9'),('drawdown','27'),('observed_ms',NOW+1),('halted',True),
                          ('ownership_verified',False)]:
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                guard.validate_guardian({**observed(),key:value},ACCOUNT,AGENT,CANDIDATE,NOW)
    def test_reduction_does_not_allow_stale_or_unowned_guardian(self):
        guard.validate_guardian({**observed(),'halted':True,'daily_loss':'100'},ACCOUNT,AGENT,CANDIDATE,NOW,reducing=True)
        with self.assertRaises(ValueError):
            guard.validate_guardian({**observed(),'observed_ms':NOW-6000},ACCOUNT,AGENT,CANDIDATE,NOW,reducing=True)
    def test_real_new_epoch_cannot_supply_historical_paper_or_calibration_pass(self):
        with tempfile.TemporaryDirectory() as directory,closing(guard.pipeline.connect(directory)) as con:
            markets=[MARKET,{**MARKET,'id':'other','coin':'@999','symbol':'OTHER/USDC'}]
            guard.pipeline.generate(con,markets,NOW)
            candidate=con.execute('SELECT id FROM candidates LIMIT 1').fetchone()[0]
            result=guard.qualification(directory,candidate,NOW)
            self.assertFalse(result['qualified']);self.assertEqual(result['paper']['status'],'NOT_ADMITTED')
            self.assertEqual(result['calibration']['status'],'BLOCKED')
    @unittest.skipUnless(os.name=='posix','Linux ownership contract')
    def test_user_owned_file_is_not_a_guardian_permission(self):
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'permit.json';file.write_text('{}');file.chmod(0o640)
            with self.assertRaises(ValueError):guard.trusted(file)


if __name__=='__main__':unittest.main()
