import copy
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'research'))
import hyperliquid_mandate as mandate

NOW=1790800000000


def observation():
    return {'status':'ACCOUNT_OBSERVED_READ_ONLY','observed_ms':NOW,'association_revalidated':True,
        'account_masked':'0x111111...1111','spot_balances':[{'coin':'USDC','total':'6.26','hold':'0'}],
        'perpetual_equity':'0','perpetual_withdrawable':'0','positions':0,'open_orders':0}


class MandateTests(unittest.TestCase):
    def setUp(self): self.config=mandate.DEFAULTS|{'account_binding_sha256':'a'*64}

    def test_90_percent_not_balance_as_full_capital(self):
        result=mandate.evaluate(self.config,observation(),NOW)
        self.assertEqual(result['capital_ceiling_usdc'],'5.634')
        self.assertEqual(result['reserve_floor_usdc'],'0.626')
        self.assertEqual(result['strategy_cap_usdc'],'1.1268')
        self.assertEqual(result['order_cap_usdc'],'0.2817')
        self.assertEqual(result['daily_loss_cap_usdc'],'0.05634')
        self.assertEqual(result['drawdown_cap_usdc'],'0.16902')
        self.assertFalse(result['live_eligible'])
        self.assertIn('LOSS_AND_EXPOSURE_ENFORCEMENT_NOT_IMPLEMENTED',result['blockers'])
        self.assertIn('ORDER_CAP_BELOW_DOCUMENTED_DEFAULT_10_USDC_MINIMUM',result['blockers'])

    def test_held_and_perpetual_funds_not_double_counted(self):
        account=observation();account['spot_balances'][0].update(total='100',hold='80')
        account.update(perpetual_equity='50',perpetual_withdrawable='10')
        result=mandate.evaluate(self.config,account,NOW)
        self.assertEqual(result['equity_usdc'],'150');self.assertEqual(result['capital_ceiling_usdc'],'135')
        self.assertEqual(result['deployable_ceiling_usdc'],'30')
        self.assertEqual(result['strategy_cap_usdc'],'6')

    def test_policy_cannot_silently_relax(self):
        for key,value in [('capital_fraction','1'),('daily_loss_fraction','0.02'),
                          ('perpetual_leverage',10),('schema',True),('max_concurrent_strategies',True),
                          ('live_executor','AVAILABLE')]:
            with self.subTest(key=key),self.assertRaises(ValueError):
                mandate.evaluate(self.config|{key:value},observation(),NOW)

    def test_stale_future_wrong_role_and_nonfinite_evidence(self):
        for values in ({'observed_ms':NOW-3600001},{'observed_ms':NOW+1},{'association_revalidated':False},
                       {'perpetual_equity':'NaN'},{'perpetual_equity':'-1'},
                       {'perpetual_withdrawable':'1'},{'positions':True}):
            with self.subTest(values=values),self.assertRaises(ValueError):
                mandate.evaluate(self.config,observation()|values,NOW)

    def test_unvalued_holdings_duplicate_and_hold_mismatch_block(self):
        for balances in (
            [{'coin':'USDC','total':'6','hold':'7'}],
            [{'coin':'USDC','total':'6','hold':'0'}]*2,
            [{'coin':'USDC','total':'6','hold':'0'},{'coin':'HYPE','total':'1','hold':'0'}],
            [{'coin':'USDC','total':'6','hold':'0'},{'coin':'USDT','total':'1','hold':'0'}],[]):
            with self.subTest(balances=balances),self.assertRaises(ValueError):
                mandate.evaluate(self.config,observation()|{'spot_balances':balances},NOW)

    def test_existing_exposure_does_not_become_ready(self):
        result=mandate.evaluate(self.config,observation()|{'positions':1,'open_orders':1},NOW)
        self.assertIn('EXISTING_EXPOSURE_REQUIRES_COMPLETE_RECONCILIATION',result['blockers'])
        self.assertFalse(result['live_eligible'])

    def test_report_preserves_other_sources_if_mandate_fails(self):
        scripts=Path(__file__).resolve().parents[2]/'scripts';sys.path.insert(0,str(scripts))
        import sync_strategy_registry as registry
        with patch('hyperliquid_mandate.load',side_effect=ValueError('untrusted policy')):
            result=registry.load_report('/no-owned-test-state',mandate_path='/no-policy')
        self.assertEqual(result['hyperliquid_mandate']['status'],'FAILED')
        self.assertEqual(result['integration_health']['status'],'PARTIAL')
        self.assertIn('catalog',result)
        self.assertEqual(result['live_eligible'],0)

    def test_report_uses_actual_mandate_without_rewriting_account_observation(self):
        scripts=Path(__file__).resolve().parents[2]/'scripts';sys.path.insert(0,str(scripts))
        import sync_strategy_registry as registry
        account=observation()|{'mandate':'NOT_CONFIGURED'}
        with patch('hyperliquid_mandate.load',return_value=self.config), \
                patch('hyperliquid_account.summary',return_value=copy.deepcopy(account)), \
                patch('hyperliquid_mandate.datetime') as clock:
            clock.now.return_value.timestamp.return_value=NOW/1000
            result=registry.load_report('/no-owned-test-state',account_state_dir='/unused',mandate_path='/unused')
        self.assertEqual(result['hyperliquid_mandate']['capital_ceiling_usdc'],'5.634')
        self.assertEqual(result['hyperliquid_account']['mandate'],'CONFIGURED_POLICY_ONLY_NO_FUNDED_EXECUTOR')
        self.assertEqual(account['mandate'],'NOT_CONFIGURED')
        self.assertEqual(result['live_eligible'],0)


if __name__=='__main__':unittest.main()
