import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'research'))
import hyperliquid_monitor as monitor


def response():
    return ({'universe': [{'name': coin} for coin in monitor.COINS]},
            [{'funding': '0.00006', 'markPx': '100', 'openInterest': '2'} for _ in monitor.COINS])


class MonitorTests(unittest.TestCase):
    def client(self):
        client = Mock(); client.meta_and_asset_contexts.return_value = response()
        return client

    def test_snapshot_not_settlement_and_percent_threshold(self):
        result = monitor.observe(self.client())
        self.assertEqual(result['status'], 'OBSERVED')
        self.assertEqual(len(result['alerts']), 4)
        self.assertIsNone(result['spot_balance'])
        self.assertIsNone(result['funding'][0]['source_time_ms'])
        self.assertEqual(result['funding'][0]['kind'], 'SNAPSHOT_NOT_SETTLEMENT')

    def test_http_422_fails_without_no_alert_success(self):
        client = self.client()
        client.meta_and_asset_contexts.side_effect = HTTPError('fixture', 422, 'bad request', {}, None)
        result = monitor.observe(client)
        self.assertEqual(result['status'], 'FAILED')
        self.assertIsNone(result['funding'])
        with patch.object(monitor, 'HyperliquidReadOnly', return_value=client):
            self.assertEqual(monitor.main([]), 1)

    def test_failed_account_is_unknown_not_zero(self):
        client = self.client(); client._post_info.side_effect = OSError('fixture')
        result = monitor.observe(client, '0x'+'1'*40)
        self.assertEqual(result['status'], 'FAILED')
        self.assertIsNone(result['spot_balance'])
        self.assertEqual(result['alerts'], [])
        client._post_info.side_effect = None; client._post_info.return_value = {'balances': []}
        result = monitor.observe(client, '0x'+'1'*40)
        self.assertEqual(result['spot_balance'], 0)

    def test_missing_nonfinite_duplicate_and_alignment_fail(self):
        for bad in ('nan', 'inf', True, None):
            meta, contexts = response(); contexts[0]['funding'] = bad
            with self.assertRaises((ValueError, TypeError)): monitor.funding_rows(meta, contexts)
        meta, contexts = response()
        with self.assertRaises(ValueError): monitor.funding_rows(meta, contexts[:-1])
        meta['universe'][1]['name'] = 'BTC'
        with self.assertRaises(ValueError): monitor.funding_rows(meta, contexts)

    def test_stale_capture_window_fails(self):
        result = monitor.observe(self.client(), clock=Mock(side_effect=[1000, 1031]))
        self.assertEqual(result['status'], 'FAILED')
        self.assertEqual(result['alerts'], [])

    def test_unique_snapshot_files_no_hourly_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            result = monitor.observe(self.client())
            monitor.save_new(temp, result); monitor.save_new(temp, result)
            files = list(Path(temp).glob('*.json'))
            self.assertEqual(len(files), 2)
            self.assertEqual(json.loads(files[0].read_text())['status'], 'OBSERVED')


if __name__ == '__main__': unittest.main()
