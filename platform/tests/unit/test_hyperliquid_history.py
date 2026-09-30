from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'platform/research'))
import hyperliquid_history as h


def metadata():
    meta={'universe':[{'name':name,'szDecimals':3,'maxLeverage':10} for name in ['SOL','UNKNOWN','HYPE','BTC','ETH']]}
    spot={'tokens':[{'name':'USDC','index':0,'tokenId':'quote','szDecimals':8},
        {'name':'UBTC','index':197,'tokenId':'btc-token','szDecimals':5},
        {'name':'HYPE','index':150,'tokenId':'hype-token','szDecimals':2}],
        'universe':[{'tokens':[197,0],'index':142,'name':'@142'}, {'tokens':[150,0],'index':107,'name':'@107'}]}
    return meta,spot


def bar(t,coin,interval):
    return {'t':t,'T':t+h.INTERVALS[interval]-1,'s':coin,'i':interval,
        'o':'10','h':'12','l':'9','c':'11','v':'3','n':4}


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'archive';self.now=1800000000000//h.HOUR*h.HOUR+60000
        self.meta,self.spot=metadata();self.selected=h.markets(self.meta,self.spot)

    def fetch(self,payload):
        typ=payload['type']
        if typ=='meta':return h.encode(self.meta)
        if typ=='spotMeta':return h.encode(self.spot)
        if typ=='candleSnapshot':
            p=payload['req'];step=h.INTERVALS[p['interval']];end=p['endTime']+1
            return h.encode([bar(end-2*step,p['coin'],p['interval']),bar(end-step,p['coin'],p['interval'])])
        end=payload['endTime']+1;t=end//h.HOUR*h.HOUR-h.HOUR+53
        if t<payload['startTime']:return b'[]'
        return h.encode([{'coin':payload['coin'],'time':t,'fundingRate':'0.0001','premium':'-0.0002'}])

    def capture(self):return h.capture(self.root,self.now,self.fetch,lambda con,weight:None)

    def test_dynamic_product_identity_never_uses_legacy_asset_defaults(self):
        ids={m['id']:m['asset_id'] for m in self.selected}
        self.assertEqual(ids['perpetual:BTC'],3);self.assertEqual(ids['perpetual:HYPE'],2)
        self.assertEqual(ids['spot:UBTC/USDC'],10142)
        meta=copy.deepcopy(self.meta);meta['universe'].append(meta['universe'][0])
        with self.assertRaises(ValueError):h.markets(meta,self.spot)
        spot=copy.deepcopy(self.spot);spot['universe'].append(spot['universe'][0])
        with self.assertRaises(ValueError):h.markets(self.meta,spot)

    def test_closed_bounds_identity_ohlc_and_semantic_numeric_canonicalization(self):
        market=self.selected[0];end=self.now//h.HOUR*h.HOUR;t=end-h.HOUR;good=bar(t,market['coin'],'1h')
        self.assertEqual(h.number('10.000'),'10');self.assertEqual(h.number('-0.000'),'0')
        for key,value in [('s','WRONG'),('T',end),('i','1m'),('t',True),('h','8'),('v','NaN'),('c','Infinity')]:
            changed=dict(good);changed[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):h.candles([changed],market,'1h',t,end)
        with self.assertRaises(ValueError):h.candles([good,good],market,'1h',t,end)

    def test_full_event_id_offsets_duplicates_and_future_rate_rejected(self):
        m=self.selected[0];event={'coin':m['coin'],'time':self.now-1000+53,'fundingRate':'0.0001','premium':'0'}
        result=h.funding([event],m,self.now-h.HOUR,self.now)
        self.assertEqual(result[0]['time'],event['time']);self.assertEqual(result[0]['kind'],'VENUE_RATE_NOT_ACCOUNT_SETTLEMENT')
        with self.assertRaises(ValueError):h.funding([event,event],m,self.now-h.HOUR,self.now)
        event['time']=self.now
        with self.assertRaises(ValueError):h.funding([event],m,self.now-h.HOUR,self.now)

    def test_actual_archive_reproduction_gaps_and_append_only_repeat(self):
        first=self.capture();self.assertEqual(first['status'],'COLLECTED');self.assertFalse(first['qualified'])
        self.assertGreater(first['captures'][0]['missing_requested'],0)
        self.assertGreater(h.reproduce(self.root,first['run_id'])['verified_rows'],0)
        before=h.status(self.root);again=self.capture();after=h.status(self.root)
        self.assertEqual(again['status'],'COLLECTED');self.assertEqual(before['series'],after['series'])
        self.assertEqual(before['funding'],after['funding'])
        self.assertEqual(len(after['series']),12);self.assertEqual(len(after['funding']),4)

    def test_conflict_rolls_back_new_rows_without_overwriting_earlier_source(self):
        with closing(h.connect(self.root)) as con:
            artifact=h.retain(con,b'fixture');m=self.selected[0]['id']
            original={'time':1000,'rate':'0.001'};h.append(con,'funding',m,[original],artifact)
            with self.assertRaises(ValueError):h.append(con,'funding',m,[{'time':2000,'rate':'0.003'},{'time':1000,'rate':'0.002'}],artifact)
            self.assertEqual(con.execute('SELECT body FROM funding').fetchall(),[(h.encode(original).decode(),)])

    def test_raw_tampering_and_unknown_database_refused(self):
        record=self.capture()
        with closing(h.connect(self.root)) as con:
            con.execute("UPDATE artifacts SET raw=x'00'");con.commit()
        with self.assertRaises((ValueError,h.zlib.error)):h.reproduce(self.root,record['run_id'])
        unknown=Path(self.tmp.name)/'unknown';unknown.mkdir();sqlite3.connect(unknown/'history.sqlite3').close()
        with self.assertRaises((ValueError,sqlite3.Error)):h.connect(unknown)

    def test_failed_network_preserved_without_success_or_qualification(self):
        def failed(payload):raise OSError('fixture private error')
        record=h.capture(self.root,self.now,failed,lambda con,weight:None)
        self.assertEqual(record['status'],'BLOCKED');self.assertNotIn('private',h.encode(record).decode())
        with self.assertRaises(ValueError):h.reproduce(self.root,record['run_id'])
        missing=Path(self.tmp.name)/'not-created';self.assertEqual(h.status(missing)['status'],'NOT_STARTED')
        self.assertFalse(missing.exists())

    def test_restart_safe_request_reservations_limit_weight_and_clock_reversal(self):
        with closing(h.connect(self.root)) as con:
            now=[1000000];waits=[]
            def sleep(seconds):waits.append(seconds);now[0]+=int(seconds*1000)+1
            h.reserve(con,500,clock=lambda:now[0],sleep=sleep)
            h.reserve(con,20,clock=lambda:now[0],sleep=sleep)
            self.assertEqual(len(waits),1)
            with self.assertRaises(ValueError):h.reserve(con,20,clock=lambda:now[0]-5000,sleep=sleep)
            self.assertEqual(con.execute('SELECT count(*) FROM requests').fetchone()[0],2)


if __name__=='__main__':unittest.main()
