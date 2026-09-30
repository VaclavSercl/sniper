from decimal import Decimal
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'research'))
import research_protocol as p


def bars(n=2880):
    return [{'time':i*p.HOUR,'close_time':(i+1)*p.HOUR-1,'open':'100','high':'100',
        'low':'100','close':'100','volume':'100','trades':1} for i in range(n)]


class ProtocolTests(unittest.TestCase):
    def test_costs_at_causal_next_open_with_independent_cash_calculation(self):
        data=bars(72);data[47].update(low='90',high='102',close='101')
        for i in range(48,len(data)):data[i].update(open='200',high='200',low='200',close='200')
        out=p.simulate(data,48,72,'failed_downside_breakout','0.002','0.001')
        expected=Decimal(250)/(Decimal('200.2')*Decimal('1.002'))*Decimal('199.8')*Decimal('0.998')-250
        self.assertAlmostEqual(float(out['net']),float(expected),places=10)
        self.assertEqual(out['round_trips'],1);self.assertEqual(out['orders'][0]['time'],48*p.HOUR)
        self.assertEqual(out['orders'][0]['price'],'200.200')
        self.assertFalse(out['qualified']);self.assertEqual(len(out['equity']),24)
        changed=[dict(b) for b in data];changed[60]['close']='300'
        after=p.simulate(changed,48,72,'failed_downside_breakout','0.002','0.001')
        self.assertEqual(out['orders'][0],after['orders'][0])

    def test_cash_flat_days_and_fee_only_buy_hold_loss(self):
        data=bars();flat=p.simulate(data,48,2880,'cash','0.0021','0.001')
        self.assertEqual(Decimal(flat['net']),0);self.assertGreater(len(flat['daily_returns']),100)
        self.assertTrue(all(Decimal(d['return'])==0 for d in flat['daily_returns']))
        self.assertEqual(flat['sign_test_p'],1)
        held=p.simulate(data,48,2880,'buy_hold','0.0021','0.001')
        self.assertLess(Decimal(held['net']),0);self.assertEqual(held['round_trips'],1)

    def test_exact_sign_tail_and_invalid_samples(self):
        self.assertEqual(p.sign_tail(3,3),.125);self.assertEqual(p.sign_tail(0,3),1)
        self.assertEqual(p.sign_tail(0,0),1)
        for pair in ((4,3),(-1,3),(1,1001),(True,2)):
            with self.assertRaises(ValueError):p.sign_tail(*pair)

    def test_fixed_partition_sample_cost_and_no_shortcuts(self):
        data=bars();self.assertEqual(p.partitions(data),(48,1728,2304,2880))
        with self.assertRaises(ValueError):p.partitions(data[:-1])
        result=p.segment(data,2304,2880,'active_volume_continuation','0.0021',holdout=True)
        self.assertIn('INSUFFICIENT_TRADES',result['failures'])
        self.assertIn('PLANNED_TRIAL_MULTIPLICITY',result['failures'])
        self.assertFalse(result['qualified']);self.assertFalse(result['exploratory_positive'])
        for fee in ('NaN','-0.1','0.5'):
            with self.assertRaises(ValueError):p.simulate(data,48,72,'cash',fee,'0.001')

    def test_signal_cannot_use_current_unclosed_bar(self):
        data=bars(72);data[47].update(low='90',high='102',close='101')
        before=p.simulate(data,48,60,'failed_downside_breakout','0','0')
        data[48].update(high='1000',close='1000',volume='999999')
        after=p.simulate(data,48,60,'failed_downside_breakout','0','0')
        self.assertEqual(before['orders'][0],after['orders'][0])


if __name__=='__main__':unittest.main()
