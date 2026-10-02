import copy
from decimal import Decimal
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'research'))
import execution_evidence as ex

MARKET={'id':'spot:TEST/USDC','product':'spot','coin':'@123','symbol':'TEST/USDC',
        'size_decimals':3,'asset_id':10123,'base_token_id':'public-base','quote_token_id':'public-usdc','max_leverage':1,'quote':'USDC'}


def snapshot(stamp=102000,depth='100',bid='99.9',ask='100.1'):
    raw={'coin':MARKET['coin'],'time':stamp,'levels':[[{'px':bid,'sz':depth,'n':1}], [{'px':ask,'sz':depth,'n':1}]]}
    return ex.book(raw,MARKET,stamp,stamp+10)


def samples():
    rows=[]
    for i in range(30):
        buy=i%2==0; case=i%3
        limit='100.1' if buy else '99.9'
        if case==2: limit='99' if buy else '101'
        order={'decision_ms':100000+i*10000,'side':'B' if buy else 'A','quantity':'1','limit':limit}
        snap=snapshot(order['decision_ms']+1000,depth='10' if case==1 else '100')
        qty=Decimal('0' if case==2 else '.5' if case==1 else '1'); px=Decimal('100.1' if buy else '99.9')
        raw={'fills':[] if not qty else [{'tid':i,'oid':i,'coin':'@123','side':order['side'],'sz':str(qty),'px':str(px),
             'feeToken':'TEST' if buy else 'USDC','time':order['decision_ms']+700,
             'fee':str(qty*Decimal('.0021') if buy else qty*px*Decimal('.0021'))}]}
        actual={'status':('FILLED','PARTIAL_CANCELLED','REJECTED')[case],'time_ms':order['decision_ms']+700,
                'filled':str(qty),'notional':str(qty*px),'fee_rate':'.0021','raw_response':raw,
                'response_sha256':ex.sha(raw),'request_sha256':ex.sha(order),'oid':i}
        rows.append({'source':'OWNED_VENUE_ORDER_JOURNAL','network':'MAINNET','operation_id':str(i),
                     'order':order,'book':snap,'actual':actual,'cash':'1000','inventory':'10'})
    return rows


class ExecutionTests(unittest.TestCase):
    def order(self,**changes):
        return {'decision_ms':100000,'side':'B','quantity':'1','limit':'101',**changes}

    def test_future_ioc_full_and_base_fee_accounting(self):
        result=ex.ioc(self.order(),snapshot(), '1000','0')
        self.assertEqual(result['status'],'FILLED')
        self.assertEqual(result['cash'],'899.9'); self.assertEqual(result['inventory'],'0.9979')
        self.assertEqual(result['fee_asset'],'BASE'); self.assertEqual(result['fee'],'0.0021')

    def test_partial_fill_cancels_remainder_and_stress_reduces_depth(self):
        normal=ex.ioc(self.order(),snapshot(depth='10'),'1000','0')
        stress=ex.ioc(self.order(),snapshot(depth='10'),'1000','0',True)
        self.assertEqual(normal['filled'],'0.5'); self.assertEqual(normal['cancelled'],'0.5')
        self.assertEqual(stress['filled'],'0.25'); self.assertEqual(stress['fee'],'0.00105')

    def test_no_future_or_stale_book_never_fills(self):
        for snap in (snapshot(100000),snapshot(150000)):
            result=ex.ioc(self.order(),snap,'1000','0')
            self.assertEqual(result['filled'],'0'); self.assertEqual(result['cash'],'1000')
            self.assertEqual(result['reason'],'MISSING_OR_STALE_FUTURE_BOOK')

    def test_minimum_precision_inventory_and_quote_rejections(self):
        cases=[(self.order(quantity='.001'),'1000','0','MINIMUM_NOTIONAL'),
               (self.order(quantity='1.0001'),'1000','0','PRECISION_OR_PRODUCT'),
               (self.order(),'1','0','INSUFFICIENT_QUOTE'),
               (self.order(side='A',limit='99'),'1000','0','INSUFFICIENT_INVENTORY')]
        for order,cash,inventory,reason in cases:
            with self.subTest(reason=reason): self.assertEqual(ex.ioc(order,snapshot(),cash,inventory)['reason'],reason)

    def test_spot_sell_quote_fee_and_marketability(self):
        result=ex.ioc(self.order(side='A',limit='99'),snapshot(),'0','1')
        self.assertEqual(result['cash'],'99.69021'); self.assertEqual(result['inventory'],'0')
        self.assertEqual(ex.ioc(self.order(limit='99'),snapshot(),'1000','0')['reason'],'IOC_NO_LIQUIDITY')

    def test_malformed_orderbook_identity_precision_and_order(self):
        good={'coin':'@123','time':102000,'levels':[[{'px':'99.9','sz':'1','n':1}],[{'px':'100.1','sz':'1','n':1}]]}
        bad=[]
        r=copy.deepcopy(good);r['coin']='BTC';bad.append(r)
        r=copy.deepcopy(good);r['levels'][0][0]['px']='101';bad.append(r)
        r=copy.deepcopy(good);r['levels'][0][0]['sz']='1.0001';bad.append(r)
        r=copy.deepcopy(good);r['levels'][1].append(r['levels'][1][0]);bad.append(r)
        for r in bad:
            with self.assertRaises(ValueError): ex.book(r,MARKET,102000,102010)

    def test_calibration_needs_real_network_cases_and_reconciliation(self):
        valid=samples(); self.assertEqual(ex.calibration(valid,500000)['status'],'PASS')
        self.assertEqual(ex.calibration([],500000)['status'],'BLOCKED')
        self.assertEqual(ex.calibration(valid[:2],500000)['status'],'BLOCKED')
        for field,value in (('network','TESTNET'),('operation_id','1')):
            bad=copy.deepcopy(valid);bad[0][field]=value
            with self.assertRaises(ValueError): ex.calibration(bad,500000)
        bad=copy.deepcopy(valid);bad[0]['actual']['filled']='0.9'
        with self.assertRaises(ValueError): ex.calibration(bad,500000)
        with self.assertRaises(ValueError): ex.calibration(valid,10**12)

    def test_flat_complete_days_and_sparse_equity(self):
        marks=[[i*3600000,'1000'] for i in range(72)]
        metrics=ex.performance(marks,0)
        self.assertEqual(metrics['daily_returns'],['0']*3)
        self.assertEqual(metrics['positive_block_sign_pvalue'],1)
        with self.assertRaises(ValueError): ex.performance(marks[:1]+marks[2:],0)


if __name__=='__main__': unittest.main()
