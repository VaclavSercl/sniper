import csv
from contextlib import closing
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'research'))
import candle_research as cr


def rows(count=6, start=0):
    return [dict(ts=start+i*cr.MINUTE, open=100., high=101., low=99., close=100., volume=2.) for i in range(count)]


def csv_bytes(data):
    stream = io.StringIO(); writer = csv.DictWriter(stream, cr.FIELDS)
    writer.writeheader(); writer.writerows(data)
    return stream.getvalue().encode()


class CandleResearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.p = dict(schema=1, source='binance_klines', symbol='BTCUSDT',
            start='2026-08-26T00:00:00Z', training_end='2026-09-23T00:00:00Z',
            holdout_end='2026-09-30T00:00:00Z', initial_quote_capital=1000,
            fee_per_side=.001, slippage_per_side=.0008, position_fraction=.2,
            risk_fraction=.01, cost_basis='Explicit test assumptions', scope='EXPLORATORY_ONLY')
        self.now = datetime(2026, 9, 30, 8, tzinfo=timezone.utc)

    def simulation(self, data, sigs={0}, atr=None):
        with patch.object(cr, 'signals', return_value=(sigs, atr or [1.] * len(data))):
            return cr.simulate(data, cr.BASELINE, self.p, data[0]['ts'], data[-1]['ts']+cr.MINUTE)

    def test_both_leg_costs_and_cash_reconcile(self):
        data = rows(2); data[1]['high'] = 100.1
        result = self.simulation(data)
        quantity = 200 / (100 * 1.0008 * 1.001)
        proceeds = quantity * 100 * .9992 * .999
        self.assertAlmostEqual(result['net_pnl_quote'], proceeds-200)
        self.assertAlmostEqual(result['final_quote_equity'], 800+proceeds)
        self.assertAlmostEqual(result['fees_quote'], quantity*100*1.0008*.001+quantity*100*.9992*.001)
        self.assertEqual(result['trades'][0]['entry_ms'], cr.MINUTE)
        self.assertFalse(result['live_eligible'])

    def test_gap_stop_uses_worse_open(self):
        data = rows(3); data[1].update(high=100.1, low=99., close=100.)
        data[2].update(open=90., high=92., low=89., close=91.)
        result = self.simulation(data)
        self.assertEqual(result['trades'][0]['reason'], 'STOP')
        self.assertEqual(result['trades'][0]['exit_reference'], 90.)

    def test_current_high_does_not_raise_stop_before_same_low(self):
        data = rows(2); data[1].update(high=120., low=99., close=119.)
        result = self.simulation(data)
        self.assertEqual(result['trades'][0]['reason'], 'PARTITION_END')
        self.assertEqual(result['trades'][0]['exit_reference'], 119.)

    def test_entry_gap_below_known_stop_is_skipped(self):
        data = rows(2); data[1].update(open=90., high=92., low=89., close=91.)
        self.assertEqual(self.simulation(data)['trade_count'], 0)

    def test_flat_days_remain_in_returns(self):
        data = rows(2*1440)
        result = self.simulation(data, sigs=set())
        self.assertEqual([r['return'] for r in result['daily_returns']], [0., 0.])
        self.assertEqual(result['max_drawdown_quote'], 0.)

    def test_schema_gap_duplicate_nan_and_ohlc_refused(self):
        for data in (rows(3)[:2], [rows(3)[0]]*3, rows(3)[::-1]):
            with self.assertRaises(ValueError): cr.parse_candles(csv_bytes(data), 0, 3*cr.MINUTE)
        for field, value in [('close', float('nan')), ('volume', -1), ('low', 101), ('high', 98)]:
            data = rows(3); data[1][field] = value
            with self.assertRaises(ValueError): cr.parse_candles(csv_bytes(data), 0, 3*cr.MINUTE)

    def test_policy_rejects_identity_future_implicit_costs_and_leverage(self):
        for key, value in [('source','bitfinex_candles'), ('symbol','BTCUSD'), ('position_fraction',2),
                           ('risk_fraction',.02), ('fee_per_side',-.001), ('slippage_per_side',True)]:
            p = dict(self.p); p[key] = value
            with self.assertRaises(ValueError): cr.policy(cr.encode(p), self.now)
        with self.assertRaises(ValueError): cr.policy(cr.encode(self.p), self.now-timedelta(days=2))

    def test_causal_signal_matches_existing_t1_and_future_change(self):
        spec = importlib.util.spec_from_file_location('legacy_t1', ROOT/'legacy/research/strategies/t1_volatility_breakout.py')
        legacy = importlib.util.module_from_spec(spec); spec.loader.exec_module(legacy)
        data = rows(1900)
        for i in range(1500, len(data)):
            price = 100+(i-1500)*.1
            data[i].update(open=price, high=price+.1, low=price-.1, close=price)
        data[1600].update(open=110, high=130, low=100, close=129)
        actual, _ = cr.signals(data, cr.BASELINE)
        expected = set(legacy.generate_signals(data, n_breakout=240, vol_q=.5))
        self.assertEqual(actual, expected)
        changed = [dict(r) for r in data]
        changed[1800].update(high=1000, close=999)
        future, _ = cr.signals(changed, cr.BASELINE)
        self.assertEqual({x for x in actual if x<1800}, {x for x in future if x<1800})

    def test_training_never_receives_holdout_rows(self):
        first, middle, end = (cr.stamp(self.p[k]) for k in ('start','training_end','holdout_end'))
        data = rows((end-first)//cr.MINUTE, first)
        received=[]
        def simulated(candles, params, policy, start, stop):
            received.append((max(r['ts'] for r in candles),start,stop));return {'stub':'partition-isolation-fixture'}
        with patch.object(cr,'simulate',side_effect=simulated): cr.evaluate(csv_bytes(data),self.p,cr.BASELINE,True)
        self.assertLess(received[0][0], middle)
        self.assertEqual(received[1][1:], (middle,end))

    def test_daily_idempotence_and_single_holdout(self):
        ends=[]
        def export(p, end): ends.append(end);return b'fixture'
        def evaluate(raw,p,params,baseline):
            return {'status':'BASELINE_SCREEN_COMPLETE' if baseline else 'TRAINING_SCREEN_COMPLETE',
                    'params':params,'holdout':baseline}
        with patch.object(cr,'evaluate',side_effect=evaluate):
            first=cr.cycle(self.root,cr.encode(self.p),self.now,export)
            again=cr.cycle(self.root,cr.encode(self.p),self.now,export)
            second=cr.cycle(self.root,cr.encode(self.p),self.now+timedelta(days=1),export)
        self.assertEqual(again['status'],'ALREADY_RECORDED')
        self.assertEqual(len(ends),2)
        self.assertEqual(ends,[cr.stamp(self.p['holdout_end']),cr.stamp(self.p['training_end'])])
        self.assertTrue(first['holdout_used']);self.assertFalse(second['holdout_used'])
        self.assertEqual(len(cr.status(self.root)['runs']),2)

    def test_failed_baseline_is_preserved_and_not_bypassed(self):
        def failed(p,end): raise RuntimeError('Fixture failure')
        result=cr.cycle(self.root,cr.encode(self.p),self.now,failed)
        self.assertEqual(result['status'],'BLOCKED')
        with self.assertRaises(ValueError): cr.cycle(self.root,cr.encode(self.p),self.now+timedelta(days=1),failed)
        self.assertEqual(cr.status(self.root)['runs'][0]['status'],'BLOCKED')

    def test_policy_change_and_interruption_refused(self):
        with patch.object(cr,'evaluate',return_value={'status':'BASELINE_SCREEN_COMPLETE'}):
            cr.cycle(self.root,cr.encode(self.p),self.now,lambda p,e:b'fixture')
        changed=dict(self.p,fee_per_side=.002)
        with self.assertRaises(ValueError): cr.cycle(self.root,cr.encode(changed),self.now)
        with closing(sqlite3.connect(self.root/'candle-research.sqlite3')) as con:
            con.execute("UPDATE runs SET status='STARTED'")
            con.commit()
        with self.assertRaises(ValueError): cr.cycle(self.root,cr.encode(self.p),self.now)

    def test_reproduction_and_tampered_input(self):
        def fake_eval(raw,p,params,baseline): return {'status':'BASELINE_SCREEN_COMPLETE','digest':cr.digest(raw)}
        with patch.object(cr,'evaluate',side_effect=fake_eval):
            record=cr.cycle(self.root,cr.encode(self.p),self.now,lambda p,e:b'fixture')
            self.assertEqual(cr.reproduce(self.root,self.now.date().isoformat())['status'],'REPRODUCED')
            (self.root/'inputs'/record['data_input']).write_bytes(b'changed')
            with self.assertRaises(ValueError): cr.reproduce(self.root,self.now.date().isoformat())

    def test_export_is_read_only_and_exact_source(self):
        start=cr.stamp(self.p['start']); data=csv_bytes(rows(3,start))
        class Result: returncode=0;stdout=data
        with patch.object(cr.subprocess,'run',return_value=Result()) as run:
            self.assertEqual(cr.export_candles(self.p,start+3*cr.MINUTE),data)
        kwargs=run.call_args.kwargs;argv=run.call_args.args[0]
        self.assertIn(b'READ ONLY',kwargs['input']);self.assertIn(b"src='binance_klines'",kwargs['input'])
        self.assertNotIn('sudo',argv);self.assertIn('-w',argv)


if __name__=='__main__':unittest.main()
