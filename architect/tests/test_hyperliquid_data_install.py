import copy
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'infra/beroun'))
from install_hyperliquid_data import validate_first


class DataInstallTests(unittest.TestCase):
    def data(self):
        perps=['perpetual:'+c for c in ('BTC','ETH','SOL','HYPE')]
        markets=perps+['spot:UBTC/USDC','spot:HYPE/USDC']
        return {'status':'OBSERVED','qualified':False,'latest_run':{'status':'COLLECTED','run_id':'fixture'},
            'series':[{'market':m,'interval':i,'rows':10} for m in markets for i in ('1m','1h')],
            'funding':[{'market':m,'events':1} for m in perps]}

    def test_capture_coverage_never_claims_trading_qualification(self):
        self.assertEqual(validate_first(self.data()),'fixture')
        data=self.data();data['qualified']=True
        with self.assertRaises(ValueError):validate_first(data)

    def test_missing_empty_or_failed_source_blocks_timer_admission(self):
        for mutate in (lambda d:d['series'].pop(),lambda d:d['funding'].pop(),
            lambda d:d['latest_run'].update(status='BLOCKED'),lambda d:d['series'][0].update(rows=0),
            lambda d:d['funding'][0].update(events=0),lambda d:d['series'][0].update(market='WRONG')):
            data=self.data();mutate(data)
            with self.assertRaises(ValueError):validate_first(data)


if __name__=='__main__':unittest.main()
