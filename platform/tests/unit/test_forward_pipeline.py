import copy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'research'))
import execution_evidence as ex
import forward_pipeline as pipe
import forward_reference as ref
import forward_worker as worker
import strategy_recipes as recipes
from test_execution_evidence import MARKET
from test_strategy_recipes import bars


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.con=pipe.connect(self.root);self.addCleanup(self.con.close)
        self.markets=[MARKET,{**MARKET,'id':'spot:OTHER/USDC','coin':'@124','symbol':'OTHER/USDC','asset_id':10124}]
        pipe.generate(self.con,self.markets,0)
        self.identity=self.con.execute('SELECT id FROM candidates WHERE market=? ORDER BY id',(MARKET['id'],)).fetchone()[0]
        self.value=pipe.candidate(self.con,self.identity)

    def add_book(self,t,bid='99.9',ask='100.1',depth='100'):
        raw={'coin':'@123','time':t,'levels':[[{'px':bid,'sz':depth,'n':1}],[{'px':ask,'sz':depth,'n':1}]]}
        return pipe.retain_book(self.con,raw,MARKET,t,t+10)

    def admitted_fixture(self,now):
        # Synthetic PAPER state only; no real-world historical pass is asserted.
        state={'candidate':self.identity,'cash':'1000','inventory':'0','entered_ms':None,
               'stress_cash':'1000','stress_inventory':'0','stress_entered_ms':None,
               'peak':'1000','day':now//pipe.DAY,'day_equity':'1000','filled_orders':0,
               'stress_filled_orders':0,'source_sha256':pipe.source_hash(),
               'risk_paused':False,'recovery_verified':False,'last_decision_ms':None}
        with self.con: self.con.execute('INSERT INTO paper VALUES(?,?,?,?,?,?)',(self.identity,'fixture',now,'RUNNING',now,json.dumps(state)))

    def test_daily_quota_semantic_deduplication_and_restart(self):
        self.assertEqual(self.con.execute('SELECT count(*) FROM families').fetchone()[0],2)
        self.assertEqual(pipe.generate(self.con,self.markets,1000)['new_economic_mechanisms'],0)
        self.assertEqual(pipe.generate(self.con,self.markets,pipe.DAY)['new_economic_mechanisms'],2)
        self.assertEqual(self.con.execute('SELECT count(*) FROM candidates').fetchone()[0],24)
        with closing(pipe.connect(self.root)) as reopened: self.assertEqual(pipe.generate(reopened,self.markets,pipe.DAY)['status'],'DAILY_QUOTA')

    def test_changed_frozen_source_or_candidate_blocks(self):
        with patch('forward_pipeline.source_hash',return_value='changed'):
            with self.assertRaises(ValueError): pipe.validate_store(self.con)
        value=copy.deepcopy(self.value);value['recipe']['holding_hours']=12
        with self.con:self.con.execute('UPDATE candidates SET body=? WHERE id=?',(json.dumps(value),self.identity))
        with self.assertRaises(ValueError): pipe.candidate(self.con,self.identity)

    def test_raw_book_hash_and_future_selection(self):
        self.add_book(100000);self.add_book(101100)
        self.assertEqual(pipe.future_book(self.con,'spot:TEST/USDC',100000)['sent_ms'],101100)
        with self.assertRaises(LookupError): pipe.future_book(self.con,'spot:TEST/USDC',200000)
        with self.con:self.con.execute("UPDATE books SET sha='changed' WHERE sent=101100")
        with self.assertRaises(ValueError): pipe.future_book(self.con,'spot:TEST/USDC',100000)

    def test_archive_gap_fails_without_consuming_holdout(self):
        self.add_book(100000);self.add_book(150000)
        with self.assertRaises(ValueError): pipe.book_archive(self.con,'spot:TEST/USDC',100000,150011)
        result=pipe.historical(self.con,self.identity,self.root,0)
        self.assertEqual(result['status'],'WAITING_PROSPECTIVE_HISTORY')
        result=pipe.historical(self.con,self.identity,self.root,self.value['historical_end_ms'])
        self.assertEqual(result['status'],'BLOCKED_CALIBRATION')
        self.assertEqual(self.con.execute('SELECT count(*) FROM trials').fetchone()[0],0)

    def test_holdout_is_consumed_before_failed_execution_and_never_retried(self):
        start=self.value['historical_start_ms'];end=self.value['historical_end_ms'];boundary=start+(1440+288)*pipe.HOUR
        data=[{'time':start+i*pipe.HOUR,'open':'100','high':'101','low':'99','close':'100','volume':'1'} for i in range(2880)]
        def replay(_con,_value,_bars,left,right,*args,**kwargs):
            if left>=boundary:raise ValueError('Injected final-holdout failure')
            return {'net_return':'.01' if kwargs.get('benchmark') else '.1','risk_breached':False,'filled_orders':12,'max_drawdown':'.01'}
        with patch('forward_pipeline.calibration_snapshot',return_value={'status':'PASS'}),patch('forward_pipeline.book_archive',return_value={'sha256':'fixture','minimum_bids':{}}),patch('forward_pipeline.closed_bars',return_value=data),patch('forward_pipeline.replay',side_effect=replay):
            with self.assertRaises(ValueError):pipe.historical(self.con,self.identity,self.root,end)
            self.assertEqual(self.con.execute('SELECT status,holdout_used FROM trials').fetchone(),('STARTED',1))
            self.assertEqual(pipe.historical(self.con,self.identity,self.root,end)['status'],'ALREADY_ATTEMPTED')

    def test_paper_admission_refuses_booleans_and_positive_ohlc(self):
        with self.assertRaises(ValueError): pipe.admit(self.con,self.identity,100000)
        with self.con:self.con.execute('INSERT INTO trials VALUES(?,?,?,?,?)',(self.identity,'fixture','SCREEN_PASS',0,json.dumps({'passed':True})))
        with self.assertRaises(ValueError): pipe.admit(self.con,self.identity,100000)

    def test_bounded_admission_requires_exact_reference_and_fresh_calibration(self):
        # Synthetic historical attestation fixture tests the admission gate;
        # it is not real mainnet history/calibration or an end-to-end PASS.
        good={'net_return':'.1','risk_breached':False,'filled_orders':20,'max_drawdown':'.01',
              'complete_days':48,'positive_block_sign_pvalue':.000001}
        benchmark={**good,'net_return':'.01'}
        phases={p:{'normal':good.copy(),'stress':good.copy(),
                   'benchmarks':{name:benchmark.copy() for name in ('cash','buy_hold','ma')}} for p in ('train','validation','holdout')}
        legs=[r[k] for r in phases.values() for k in ('normal','stress')]+[leg for r in phases.values() for leg in r['benchmarks'].values()]
        record={'candidate':self.identity,'status':'HISTORICAL_QUALIFIED','source_sha256':pipe.source_hash(),
                'reproduced':True,'calibration':{'status':'PASS','model_sha256':ex.sha(ex.MODEL)},'results':phases,
                'independent_reference':[{'status':'PASS','result_sha256':ex.sha(leg)} for leg in legs]}
        now=self.value['historical_end_ms']+pipe.HOUR
        bad=copy.deepcopy(record);bad['independent_reference'][0]['result_sha256']='changed'
        with self.con:self.con.execute('INSERT INTO trials VALUES(?,?,?,?,?)',(self.identity,'fixture','HISTORICAL_QUALIFIED',1,json.dumps(bad)))
        with patch('forward_pipeline.calibration_snapshot',return_value={'status':'PASS'}):
            with self.assertRaises(ValueError):pipe.admit(self.con,self.identity,now)
        with self.con:self.con.execute('UPDATE trials SET body=?',(json.dumps(record),))
        with self.assertRaises(ValueError):pipe.admit(self.con,self.identity,now)
        with patch('forward_pipeline.calibration_snapshot',return_value={'status':'PASS'}):
            state=pipe.admit(self.con,self.identity,now)
        self.assertEqual(state['cash'],'1000');self.assertFalse(state['recovery_verified'])
        self.assertEqual(self.con.execute('SELECT status FROM paper').fetchone()[0],'RUNNING')
        self.assertFalse(pipe.paper_result(self.con,self.identity,now)['qualified'])

    def test_wal_reader_does_not_block_public_capture_writer(self):
        with closing(pipe.connect(self.root)) as reader:
            reader.execute('BEGIN');self.assertEqual(reader.execute('SELECT count(*) FROM books').fetchone()[0],0)
            self.add_book(100000)
            self.assertEqual(reader.execute('SELECT count(*) FROM books').fetchone()[0],0)
            reader.rollback();self.assertEqual(reader.execute('SELECT count(*) FROM books').fetchone()[0],1)

    def test_forward_intent_is_durable_future_fill_and_deduplicated(self):
        now=48*pipe.HOUR; self.add_book(now);self.admitted_fixture(now)
        data=bars(now);data[-1].update(close='103',high='104')
        # Select a momentum recipe for this mechanical execution test only.
        with patch('forward_pipeline.candidate',return_value={**self.value,'recipe':recipes.proposed(0)}):
            pipe.paper_tick(self.con,self.identity,data,now+10)
            self.assertEqual(self.con.execute('SELECT count(*) FROM orders WHERE result IS NULL').fetchone()[0],2)
            self.add_book(now+3000)
            pipe.paper_tick(self.con,self.identity,data,now+3010)
            pipe.paper_tick(self.con,self.identity,data,now+3010)
        self.assertEqual(self.con.execute('SELECT count(*) FROM orders').fetchone()[0],2)
        self.assertEqual(self.con.execute('SELECT count(*) FROM orders WHERE result IS NULL').fetchone()[0],0)
        state=json.loads(self.con.execute('SELECT body FROM paper').fetchone()[0]);self.assertGreater(ex.D(state['inventory']),0)
        with patch('execution_evidence.ioc',side_effect=AssertionError('Independent paper reference must not use production fill')):
            self.assertEqual(ref.paper_ledger(self.con,self.identity,'normal',self.value,state)['status'],'PASS')
        state['cash']='999'
        with self.assertRaises(ValueError):ref.paper_ledger(self.con,self.identity,'normal',self.value,state)

    def test_disconnect_invalidates_without_backfill_or_unresolved_orders(self):
        now=48*pipe.HOUR;self.add_book(now);self.admitted_fixture(now)
        order={'decision_ms':now,'side':'B','quantity':'1','limit':'101'}
        with self.con:self.con.execute('INSERT INTO orders VALUES(?,?,?,?,?,NULL)',(self.identity,'normal',now,'B',json.dumps(order)))
        result=pipe.paper_tick(self.con,self.identity,bars(now),now+31000)
        self.assertEqual(result['status'],'INVALIDATED_FEED_GAP')
        self.assertEqual(self.con.execute('SELECT count(*) FROM marks').fetchone()[0],0)
        self.assertEqual(self.con.execute('SELECT count(*) FROM orders WHERE result IS NULL').fetchone()[0],0)
        self.assertFalse(pipe.paper_result(self.con,self.identity,now+31*pipe.DAY)['qualified'])

    def test_recovery_fault_probe_preserves_production_and_is_reopenable(self):
        now=48*pipe.HOUR;self.add_book(now);self.admitted_fixture(now)
        before=self.con.execute('SELECT count(*) FROM orders').fetchone()[0]
        proof=pipe.recovery_probe(self.con,self.identity,bars(now),now+10)
        self.assertEqual([p['status'] for p in proof['proofs']],['PASS','PASS'])
        self.assertEqual(self.con.execute('SELECT count(*) FROM orders').fetchone()[0],before)
        state=json.loads(self.con.execute('SELECT body FROM paper').fetchone()[0]);self.assertTrue(state['recovery_verified'])
        self.assertIn('THIRTY_REAL_DAYS_NOT_ELAPSED',pipe.paper_result(self.con,self.identity,now+10)['reasons'])

    def test_reference_checks_cash_curve_without_simulator(self):
        data=bars(); start=47*pipe.HOUR; end=48*pipe.HOUR
        self.add_book(start-100);self.add_book(start+1100);self.add_book(start+2100);self.add_book(end-100)
        value={**self.value,'recipe':recipes.proposed(0)}
        result=pipe.replay(self.con,value,data,start,end,benchmark='cash')
        with patch('execution_evidence.ioc',side_effect=AssertionError('Reference must not call production fill')):
            self.assertEqual(ref.verify(self.con,value,data,start,end,result,benchmark='cash')['status'],'PASS')
        result['equity'][0][1]='999'
        with self.assertRaises(ValueError): ref.verify(self.con,value,data,start,end,result,benchmark='cash')

    def test_adverse_future_does_not_suppress_causal_entry_and_terminal_has_no_free_inventory(self):
        start=48*pipe.HOUR;end=55*pipe.HOUR
        data=[{'time':i*pipe.HOUR,'open':'101','high':'104','low':'99','close':'101','volume':'10'} for i in range(55)]
        data[47]['close']='103'
        times={t+delta for t in range(start,end,pipe.HOUR) for delta in (-100,1100,2100)}|{end-100}
        for t in sorted(times):self.add_book(t)
        value={**self.value,'recipe':recipes.proposed(0)}
        result=pipe.replay(self.con,value,data,start,end,minima={start:ex.D('70')},maxima={start:ex.D('120')})
        self.assertEqual(result['journal'][0]['order']['side'],'B')
        self.assertTrue(result['risk_breached'])
        with patch('execution_evidence.ioc',side_effect=AssertionError('Reference must not call production')):
            self.assertEqual(ref.verify(self.con,value,data,start,end,result,minima={start:ex.D('70')},maxima={start:ex.D('120')})['status'],'PASS')

    def test_report_missing_stale_health_and_exact_counts(self):
        self.assertEqual(pipe.report(self.root/'absent',1000)['status'],'MISSING')
        self.assertEqual(pipe.report(self.root,1000)['status'],'STALE_OR_FAILED')
        with self.con:self.con.execute('INSERT INTO health VALUES(1,?,?,?)',(1000,'PASS','{}'))
        self.assertEqual(pipe.report(self.root,2000)['status'],'STALE_OR_FAILED')
        with self.con:self.con.execute('INSERT INTO health VALUES(2,?,?,?)',(1000,'PASS','{}'))
        result=pipe.report(self.root,2000)
        self.assertEqual(result['status'],'OBSERVED')
        self.assertEqual(result['counts']['families'],2);self.assertEqual(result['counts']['candidates'],12)
        self.assertEqual(result['mechanisms_remaining'],334);self.assertEqual(result['paper_qualified'],0)
        self.assertEqual(result['calibration']['status'],'BLOCKED')

    def test_weekly_comparison_not_a_promotion(self):
        pipe.compare(self.con,0);pipe.compare(self.con,1)
        record=json.loads(self.con.execute('SELECT body FROM comparisons').fetchone()[0])
        self.assertEqual(record['scope'],'KNOWN_DATA_DIAGNOSTIC_ONLY_NO_ADMISSION')
        self.assertEqual(self.con.execute('SELECT count(*) FROM comparisons').fetchone()[0],1)


if __name__=='__main__': unittest.main()
