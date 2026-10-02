from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'research'))
import research_cycle as c
import research_protocol as p
import hyperliquid_history as h
from test_hyperliquid_history import metadata,bar
from test_research_protocol import bars


def epoch(now=1800000000000//p.HOUR*p.HOUR):
    meta,spot=metadata();markets=h.markets(meta,spot);start=now-120*p.DAY
    rows=bars()
    for row in rows:row['time']+=start;row['close_time']+=start
    return {'schema':1,'start_ms':start,'end_ms':now,'markets':markets,
        'series':{m['id']:rows for m in markets},'spot_fee':'0.0021','preregistration':c.preregistration()}


class CycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.now=1800000000000//p.HOUR*p.HOUR+60000
        self.epoch=epoch(self.now//p.HOUR*p.HOUR)

    def database(self):
        con=c.connect(self.root/'cycle');self.addCleanup(con.close);c.save_epoch(con,self.epoch)
        return con

    def test_real_day_quota_deduplicates_economic_blueprints_and_market_variants(self):
        con=self.database()
        self.assertEqual(c.register(con,self.epoch,self.now)['new_blueprints'],2)
        self.assertEqual(c.register(con,self.epoch,self.now)['new_blueprints'],0)
        self.assertEqual(con.execute('SELECT count(*) FROM variants').fetchone()[0],12)
        for offset in (1,2):self.assertEqual(c.register(con,self.epoch,self.now+offset*p.DAY)['new_blueprints'],2)
        self.assertEqual(c.register(con,self.epoch,self.now+3*p.DAY)['status'],'NEED_NEW_BLUEPRINTS_OR_PROVIDER_BUDGET')
        self.assertEqual(c.report(con)['blueprints_registered'],6)
        with self.assertRaises(ValueError):c.save_epoch(con,self.epoch)

    def test_evaluation_cap_rejection_product_block_and_exact_reproduction(self):
        con=self.database();c.register(con,self.epoch,self.now);_,sha=c.load_epoch(con)
        count=0
        while c.evaluate_next(con,self.epoch,sha,self.now) is not None:count+=1
        self.assertEqual(count,12);self.assertEqual(c.report(con)['completed_screens'],4)
        self.assertEqual(c.report(con)['blocked_product_models'],8)
        self.assertEqual(con.execute('SELECT sum(holdout_consumed) FROM attempts').fetchone()[0],0)
        identity=con.execute("SELECT variant FROM attempts WHERE status='SCREEN_REJECTED_TRAINING' LIMIT 1").fetchone()[0]
        before=con.total_changes
        self.assertEqual(c.reproduce(con,identity)['status'],'REPRODUCED');self.assertEqual(con.total_changes,before)
        self.assertIsNone(c.evaluate_next(con,self.epoch,sha,self.now))

    def test_crash_after_consumption_never_replays_holdout(self):
        con=self.database();c.register(con,self.epoch,self.now);_,sha=c.load_epoch(con)
        # Reach a spot variant, retaining all blocked product attempts.
        for _ in range(4):c.evaluate_next(con,self.epoch,sha,self.now)
        calls=[]
        def fake(*args,**kwargs):
            calls.append(kwargs)
            if kwargs.get('holdout'):raise KeyboardInterrupt('synthetic interruption')
            return {'normal':{'net':'1'},'failures':[],'qualified':False}
        with patch.object(p,'segment',side_effect=fake),self.assertRaises(KeyboardInterrupt):
            c.evaluate_next(con,self.epoch,sha,self.now)
        row=con.execute("SELECT holdout_consumed FROM attempts WHERE status='STARTED'").fetchone()
        self.assertEqual(row,(1,));self.assertEqual(len(calls),3)
        with self.assertRaises(ValueError):c.evaluate_next(con,self.epoch,sha,self.now+p.DAY)
        with self.assertRaises(ValueError):c.register(con,self.epoch,self.now+p.DAY)
        self.assertEqual(c.report(con)['interrupted'],1)

    def test_changed_epoch_code_gap_or_digest_does_not_become_a_pass(self):
        con=self.database();item=con.execute('SELECT raw FROM epoch').fetchone()[0]
        bad=copy.deepcopy(self.epoch);bad['series'][bad['markets'][0]['id']][100]['time']+=p.HOUR
        raw=c.encode(bad)
        con.execute('UPDATE epoch SET sha256=?,raw=?',(c.digest(raw),c.zlib.compress(raw)));con.commit()
        with self.assertRaises(ValueError):c.load_epoch(con)
        con.execute("UPDATE epoch SET sha256='invalid',raw=?",(item,));con.commit()
        with self.assertRaises(ValueError):c.load_epoch(con)
        con.execute("UPDATE epoch SET raw=x'00'");con.commit()
        with self.assertRaises(ValueError):c.load_epoch(con)
        with patch.object(c,'MODEL_BYTES',(b'wrong',)*4),self.assertRaises(ValueError):c.model_hash()

    def test_weekly_comparison_is_cohort_specific_idempotent_and_never_promotes(self):
        con=self.database();c.register(con,self.epoch,self.now)
        result=c.compare(con,self.now)
        self.assertFalse(result['live_eligible']);self.assertEqual(result['promotion'],'KEEP_CASH_NO_QUALIFIED_CANDIDATE')
        self.assertEqual(c.compare(con,self.now)['status'],'COMPARISON_ALREADY_RECORDED')
        before=con.total_changes;c.report(con);self.assertEqual(before,con.total_changes)

    def test_freeze_reconstructs_public_response_and_refuses_corrupt_row(self):
        archive=self.root/'archive';meta,spot=metadata();markets=h.markets(meta,spot)
        end=self.now//p.HOUR*p.HOUR;start=end-120*p.DAY
        with closing(h.connect(archive)) as con:
            a=h.retain(con,h.encode(meta));b=h.retain(con,h.encode(spot))
            mv={'markets':markets,'perp_response':a,'spot_response':b};version=h.digest(h.encode(mv))
            con.execute('INSERT INTO market_versions VALUES(?,?)',(version,h.encode(mv).decode()))
            capture={'run_id':'fixture','started_ms':self.now,'status':'COLLECTED','metadata_sha256':version}
            con.execute('INSERT INTO runs VALUES(?,?,?,?)',('fixture',self.now,'COLLECTED',h.encode(capture).decode()));con.commit()
            for market in markets:
                raw=h.encode([bar(start+i*p.HOUR,market['coin'],'1h') for i in range(2880)])
                artifact=h.retain(con,raw);rows=h.candles(json.loads(raw),market,'1h',start,end)
                h.append(con,'candles',market['id'],rows,artifact,'1h')
                con.execute('INSERT INTO captures VALUES(?,?,?,?,?,?,?)',('fixture',market['id'],'1h',start,end,artifact,len(rows)));con.commit()
        fee={'status':'ACCOUNT_OBSERVED_READ_ONLY','association_revalidated':True,'observed_ms':self.now,
            'fees':{'userSpotCrossRate':'0.0007'}}
        with patch.object(c.account,'summary',return_value=fee):
            frozen=c.freeze_inputs(archive,self.root/'account',self.now)
            self.assertEqual(len(frozen['series']),6);self.assertEqual(frozen['spot_fee'],'0.0021')
            with closing(h.connect(archive)) as con:
                con.execute("UPDATE candles SET body=replace(body,'\"close\":\"11\"','\"close\":\"12\"') WHERE market=?",(markets[0]['id'],));con.commit()
            with self.assertRaises(ValueError):c.freeze_inputs(archive,self.root/'account',self.now)
        with patch.object(c.account,'summary',return_value={'status':'STALE'}),self.assertRaises(ValueError):
            c.freeze_inputs(archive,self.root/'account',self.now)

    def test_status_missing_and_unknown_registry_do_not_initialize_or_adopt(self):
        absent=self.root/'absent';self.assertEqual(c.status(absent)['status'],'NOT_STARTED');self.assertFalse(absent.exists())
        unknown=self.root/'unknown';unknown.mkdir();sqlite3.connect(unknown/'cycle.sqlite3').close()
        with self.assertRaises(sqlite3.Error):c.connect(unknown)


if __name__=='__main__':unittest.main()
