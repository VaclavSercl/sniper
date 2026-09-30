import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import contextlib
import io
import urllib.error

MODULE=Path(__file__).resolve().parents[2]/'research/history_recovery.py'
spec=importlib.util.spec_from_file_location('history_recovery',MODULE)
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
T=1_700_000_040_000  # minute aligned, historical


def bar(t=T):
    return [t,'10','12','9','11','3',t+59999,'32',4,'1','10','0']


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'bundle'

    def stage(self,raw=None,end=T+60000):
        return h.stage('binance','BTCUSDT',T,end,self.root,
                       fetch=lambda s:json.dumps(raw if raw is not None else [bar()]).encode(),
                       now=end+60000,delay=0)

    def test_complete_replay_and_sql_is_append_only(self):
        m=self.stage();saved,rows=h.verify_bundle(self.root,m['manifest_sha256'])
        self.assertEqual(saved['status'],'COMPLETE');self.assertEqual(len(rows),1)
        sql=h.import_sql(self.root,m['manifest_sha256'])
        self.assertIn('DO NOTHING RETURNING',sql)
        self.assertNotIn('DO UPDATE',sql);self.assertNotIn('DELETE',sql)
        self.assertIn('LOCK TABLE market_klines',sql)

    def test_empty_response_is_incomplete_not_fabricated(self):
        m=self.stage([]);self.assertEqual(m['status'],'INCOMPLETE')
        self.assertEqual(m['missing_minutes'],1)
        with self.assertRaises(ValueError):h.import_sql(self.root,m['manifest_sha256'])

    def test_sparse_response_preserves_gaps(self):
        m=self.stage([bar(T+60000)],T+180000)
        self.assertEqual(m['missing_intervals'],[[T,T+60000],[T+120000,T+180000]])

    def test_short_page_does_not_skip_next_page(self):
        calls=[]
        def fetch(s):
            calls.append(s)
            return json.dumps([bar(T if len(calls)==1 else T+1000*60000)]).encode()
        m=h.stage('binance','BTCUSDT',T,T+1001*60000,self.root,fetch,now=T+1002*60000,delay=0)
        self.assertEqual(len(calls),2);self.assertEqual(m['rows'],2)
        self.assertEqual(m['missing_minutes'],999)

    def test_invalid_values_and_chronology(self):
        mutations=[(1,'NaN'),(2,'Infinity'),(5,-1),(1,True),(8,True),
                   (2,'8'),(3,'12'),(0,T+1),(6,T+60000),(1,'1e999')]
        for index,value in mutations:
            with self.subTest(index=index,value=value):
                r=bar();r[index]=value
                with self.assertRaises(ValueError):h.normalize(json.dumps([r]),'binance','BTCUSDT',T,T+60000)
        for data in ([bar(),bar()], [bar(T+60000)], {'error':'API error'}):
            with self.assertRaises(ValueError):h.normalize(json.dumps(data),'binance','BTCUSDT',T,T+60000)

    def test_identity_and_window(self):
        for venue,sym,start,end,now in [('binance',"BTCUSDT';--",T,T+60000,T+120000),
            ('bitfinex','tEURUSD',T,T+60000,T+120000),
            ('binance','BTCUSDT',T,T+60000,T+59999),
            ('binance','BTCUSDT',T+1,T+60000,T+120000),
            ('binance','BTCUSDT',T,T+h.MAX_SPAN+60000,T+h.MAX_SPAN+120000)]:
            with self.assertRaises(ValueError):h.identity(venue,sym,start,end,now)

    def test_corrupted_archive_blocks_sql(self):
        m=self.stage();(self.root/'raw-000.json').write_bytes(b'[]')
        with self.assertRaises(ValueError):h.import_sql(self.root,m['manifest_sha256'])

    def test_manifest_digest_required(self):
        self.stage()
        with self.assertRaises(ValueError):h.verify_bundle(self.root,'0'*64)

    def test_self_consistent_forged_request_and_coverage_rejected(self):
        m=self.stage();raw=json.loads((self.root/'manifest.json').read_bytes())
        for field,value in [('rows',2),('missing_minutes',5),('status','FAKE')]:
            changed=dict(raw);changed[field]=value;b=h.encode(changed)
            (self.root/'manifest.json').write_bytes(b)
            with self.assertRaises(ValueError):h.verify_bundle(self.root,h.sha(b))
        raw['pages'][0]['request']['url']='https://example.com/data'
        b=h.encode(raw);(self.root/'manifest.json').write_bytes(b)
        with self.assertRaises(ValueError):h.verify_bundle(self.root,h.sha(b))

    def test_no_overwrite_and_no_partial_success(self):
        self.stage()
        with self.assertRaises(FileExistsError):self.stage()
        second=Path(self.temp.name)/'failed'
        with self.assertRaises(ValueError):
            h.stage('binance','BTCUSDT',T,T+60000,second,fetch=lambda s:b'{}',now=T+120000)
        self.assertTrue((second/'STARTED.json').exists())
        self.assertTrue((second/'raw-000.json').exists())
        self.assertFalse((second/'manifest.json').exists())

    def test_rate_limit_stops_without_retry_or_success_manifest(self):
        error=urllib.error.HTTPError('https://api-pub.bitfinex.com',429,'Rate limit',{'Retry-After':'60'},None)
        output=io.StringIO()
        with mock.patch.object(h.urllib.request,'urlopen',side_effect=error) as get,contextlib.redirect_stdout(output):
            rc=h.main(['fetch','--venue','bitfinex','--symbol','tBTCUSD',
                       '--start','2023-11-15T00:00:00Z','--end','2023-11-15T00:01:00Z','--out',str(self.root)])
        self.assertEqual(rc,2);self.assertEqual(get.call_count,1)
        self.assertEqual(json.loads(output.getvalue()),{'status':'RATE_LIMITED','http_status':429,'retry_after_seconds':60})
        self.assertFalse((self.root/'manifest.json').exists())

    def test_bitfinex_unprovided_fields_are_null(self):
        rows=h.normalize(json.dumps([[T,10,11,12,9,3]]),'bitfinex','tBTCEUR',T,T+60000)
        self.assertIsNone(rows[0]['quote_volume']);self.assertIsNone(rows[0]['trades_count'])

    def test_hyperliquid_identity(self):
        r={'t':T,'T':T+59999,'s':'BTC','i':'1m','o':'10','h':'12','l':'9','c':'11','v':'3','n':4}
        self.assertEqual(h.normalize(json.dumps([r]),'hyperliquid','BTC',T,T+60000)[0]['symbol'],'BTC-PERP')
        r['s']='ETH'
        with self.assertRaises(ValueError):h.normalize(json.dumps([r]),'hyperliquid','BTC',T,T+60000)

    def test_symlink_refused(self):
        if not hasattr(__import__('os'),'geteuid'): self.skipTest('Requires Linux symlink permissions; required Linux gate exercises it')
        target=Path(self.temp.name)/'outside';target.mkdir()
        self.root.symlink_to(target,target_is_directory=True)
        with self.assertRaises(ValueError):h.safe(self.root/'x')


if __name__=='__main__':unittest.main()
