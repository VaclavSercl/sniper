"""Offline boundaries and Linux durable intent/recovery; no exchange writes."""
import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'gateway'))
import hyperliquid_orders as hl
import execution_router as legacy

AGENT = '0x'+'1'*40
ACCOUNT = '0x'+'2'*40
NOW = 1790800000000
META = {'universe': [{'name': 'BTC', 'szDecimals': 5}, {'name': 'ETH', 'szDecimals': 4},
                     {'name': 'OTHER', 'szDecimals': 0}, {'name': 'SOL', 'szDecimals': 2}]}
SPOT = {'tokens': [{'name': 'USDC', 'index': 0, 'szDecimals': 8, 'tokenId': '0x'+'a'*32},
                   {'name': 'HYPE', 'index': 6, 'szDecimals': 2, 'tokenId': '0x'+'b'*32}],
        'universe': [{'index': 19, 'name': '@19', 'tokens': [6, 0]}]}


class FakeVenue:
    def __init__(self):
        self.requests = []; self.fail = None; self.status = 'unknownOid'; self.order = None
        self.meta = copy.deepcopy(META); self.spot = copy.deepcopy(SPOT)
        self.role = {'role': 'agent', 'data': {'user': ACCOUNT}}
        self.balances = [{'token': 0, 'total': '10000', 'hold': '0'},
                         {'token': 6, 'total': '100', 'hold': '0'}]
        self.positions = [{'position': {'coin': 'SOL', 'szi': '2'}}]

    def __call__(self, kind, payload):
        self.requests.append((kind, copy.deepcopy(payload)))
        if kind == 'exchange':
            if self.fail: raise self.fail
            return {'status': 'ok', 'response': {'type': 'order', 'data': {'statuses': [{'resting': {'oid': 123}}]}}}
        name = payload['type']
        if name == 'userRole': return self.role if payload['user'] == AGENT else {'role': 'user'}
        if name == 'meta': return self.meta
        if name == 'spotMeta': return self.spot
        if name == 'spotClearinghouseState': return {'balances': self.balances}
        if name == 'clearinghouseState': return {'assetPositions': self.positions}
        if name == 'orderStatus':
            return {'status': 'unknownOid'} if self.status == 'unknownOid' else {
                'status': 'order', 'order': {'status': self.status, 'order': self.order}}
        raise AssertionError(name)

    def acknowledge(self, client, status='open'):
        intent = json.loads(client.con.execute("SELECT intent FROM operations WHERE kind='order'").fetchone()[0])
        o = intent['action']['orders'][0]
        self.status = status
        self.order = {'coin': intent['instrument']['coin'], 'cloid': o['c'], 'side': 'B' if o['b'] else 'A',
                      'limitPx': o['p'], 'origSz': o['s'], 'sz': o['s'] if status == 'open' else '0',
                      'oid': 123, 'reduceOnly': o['r']}


class OrderPreparation(unittest.TestCase):
    def test_dynamic_product_ids_and_no_aliases(self):
        self.assertEqual(hl.resolve('perpetual', 'SOL', META, SPOT).asset, 3)
        self.assertEqual(hl.resolve('spot', 'HYPE/USDC', META, SPOT).asset, 10019)
        for product, symbol in [('perpetual', 'BTCUSDT'), ('spot', 'BTC/USDC'), ('unknown', 'BTC')]:
            with self.subTest(product=product, symbol=symbol), self.assertRaises(ValueError):
                hl.resolve(product, symbol, META, SPOT)

    def test_ambiguous_delisted_and_token_identity(self):
        meta = copy.deepcopy(META); meta['universe'].append(meta['universe'][0])
        with self.assertRaises(ValueError): hl.resolve('perpetual', 'BTC', meta, SPOT)
        meta = copy.deepcopy(META); meta['universe'][0]['isDelisted'] = True
        with self.assertRaises(ValueError): hl.resolve('perpetual', 'BTC', meta, SPOT)
        spot = copy.deepcopy(SPOT); spot['universe'].append(spot['universe'][0])
        with self.assertRaises(ValueError): hl.resolve('spot', 'HYPE/USDC', META, spot)
        spot = copy.deepcopy(SPOT); spot['tokens'][1]['tokenId'] = 'name-only'
        with self.assertRaises(ValueError): hl.resolve('spot', 'HYPE/USDC', META, spot)

    def test_exact_price_size_rules_and_integer_exception(self):
        inst = hl.resolve('perpetual', 'SOL', META, SPOT)
        def prepare(p, q='1.01'):
            return hl.prepare(inst, 'buy', p, q, True, False, '0x'+'1'*32)
        self.assertEqual(prepare('123456')['orders'][0]['p'], '123456')
        self.assertEqual(prepare('12.3400')['orders'][0]['p'], '12.34')
        for p, q in [('12345.6', '1'), ('0.00001', '1'), ('12.34', '1.001'), (12.34, '1'), ('NaN', '1'), ('1', True)]:
            with self.subTest(p=p, q=q), self.assertRaises(ValueError): prepare(p, q)
        spot = replace(hl.resolve('spot', 'HYPE/USDC', META, SPOT), size_decimals=1)
        self.assertEqual(hl.prepare(spot, 'buy', '0.0001234', '1', True, False, '0x'+'1'*32)['orders'][0]['p'], '0.0001234')
        with self.assertRaises(ValueError): hl.prepare(spot, 'buy', '1', '1', True, True, '0x'+'1'*32)

    def test_legacy_modes_never_dispatch_hyperliquid(self):
        with patch('urllib.request.urlopen', side_effect=AssertionError('Network forbidden')):
            for level in ('L1', 'L2', 'LIVE', '', None):
                with self.subTest(level=level), self.assertRaises(ValueError):
                    legacy.HyperliquidExecutionAdapter(AGENT).execute(
                        legacy.OrderRequest('hyperliquid', 'SOL', 'buy', 'limit', 100., 1., capability_level=level))
            result = legacy.UnifiedExecutionRouter().route_order(legacy.OrderRequest('hyperliquid', 'UNKNOWN', 'buy', 'limit', 1., 1.))
            self.assertFalse(result['wire_ready']); self.assertIsNone(result['asset_id'])
            for n in (float('nan'), float('inf'), True):
                with self.subTest(n=n), self.assertRaises(ValueError):
                    legacy.OrderRequest('binance', 'BTC', 'buy', 'limit', n, 1).validate()
            with self.assertRaises(ValueError): legacy.UnifiedExecutionRouter('TYPO')

    def test_transport_only_fixed_testnet_and_bounded_json(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"status":"ok"}'
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.return_value = response
            hl.transport('info', {'type': 'meta'})
            req = opener.return_value.open.call_args.args[0]
            self.assertEqual(req.full_url, 'https://api.hyperliquid-testnet.xyz/info')
            response.__enter__.return_value.read.return_value = b'x'*(hl.LIMIT+1)
            with self.assertRaises(ValueError): hl.transport('info', {'type': 'meta'})
        for raw in ('{"x":1,"x":2}', '{"x":NaN}'):
            with self.assertRaises(ValueError): hl.decode(raw)
        with self.assertRaises(ValueError): hl.NoRedirect().redirect_request(None)
        with self.assertRaises(ValueError): hl.transport('exchange', {'action': {'type': 'withdraw'}})


@unittest.skipUnless(os.name == 'posix', 'Linux durable journal/lock integration')
class DurableExecution(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)/'state'
        self.venue = FakeVenue(); self.clients = []
        self.client = self.make()

    def tearDown(self):
        for client in reversed(self.clients):
            client.close()
        self.temp.cleanup()

    def make(self, **kwargs):
        values = dict(root=self.root, wallet=SimpleNamespace(address=AGENT), account=ACCOUNT,
                      order_cap='100', session_cap='200', expires_ms=NOW+3600000,
                      send=self.venue, clock=lambda: NOW,
                      signer=lambda *args: {'r': '0x1', 's': '0x2', 'v': 27})
        values.update(kwargs); client = hl.TestnetClient(**values); self.clients.append(client)
        return client

    def submit(self, client_id='first', **kwargs):
        values = dict(client_id=client_id, product='spot', symbol='HYPE/USDC', side='buy', price='20', quantity='1')
        values.update(kwargs); return self.client.submit(**values)

    def writes(self):
        return [r for r in self.venue.requests if r[0] == 'exchange']

    def test_intent_precedes_send_and_response_is_not_reconciliation(self):
        old = self.venue
        def send(kind, payload):
            if kind == 'exchange':
                self.assertEqual(self.client.con.execute('SELECT status FROM operations').fetchone()[0], 'DISPATCH_STARTED')
            return old(kind, payload)
        self.client._transport = send
        self.assertEqual(self.submit()['status'], 'RESPONSE_RECEIVED_REQUIRES_RECONCILIATION')
        action = self.writes()[0][1]['action']['orders'][0]
        self.assertEqual(action['a'], 10019); self.assertEqual(action['p'], '20')
        with self.assertRaises(ValueError): self.submit('second')
        with self.assertRaises(ValueError): self.submit()
        self.assertEqual(len(self.writes()), 1)
        self.assertNotIn('signature', '\n'.join(r[0] for r in self.client.con.execute('SELECT intent FROM operations')))
        intent = json.loads(self.client.con.execute('SELECT intent FROM operations').fetchone()[0])
        with self.assertRaises(ValueError):
            self.client.dispatch('order:first', intent['action'], NOW, NOW+10000)
        with self.assertRaises(ValueError):
            self.client.dispatch('unknown-operation', intent['action'], NOW+1, NOW+10000)
        self.assertEqual(len(self.writes()), 1)

    def test_timeout_unknown_and_restart_never_resends(self):
        self.venue.fail = TimeoutError('must not persist this message')
        self.assertEqual(self.submit()['status'], 'UNKNOWN_REQUIRES_RECONCILIATION')
        self.assertEqual(self.client.reconcile('first')['status'], 'UNKNOWN_REQUIRES_RECONCILIATION')
        self.client.close(); self.clients.remove(self.client); self.client = self.make()
        with self.assertRaises(ValueError): self.submit()
        with self.assertRaises(ValueError): self.submit('new-id')
        self.assertEqual(len(self.writes()), 1)
        self.assertNotIn('must not', str(self.client.con.execute('SELECT evidence FROM events').fetchall()))

    def test_interruption_is_durable_and_not_adopted(self):
        self.venue.fail = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt): self.submit()
        self.assertEqual(self.client.con.execute('SELECT status FROM operations').fetchone()[0], 'DISPATCH_STARTED')
        with self.assertRaises(ValueError): self.submit('another')

    def test_role_and_inventory_fail_before_intent(self):
        self.venue.role['data']['user'] = '0x'+'3'*40
        with self.assertRaises(ValueError): self.submit()
        self.venue.role['data']['user'] = ACCOUNT
        self.venue.balances[0]['hold'] = '10000'
        with self.assertRaises(ValueError): self.submit()
        self.assertEqual(self.client.con.execute('SELECT count(*) FROM operations').fetchone()[0], 0)
        self.assertFalse(self.writes())

    def test_reconciliation_requires_exact_ownership(self):
        self.submit(); self.venue.acknowledge(self.client)
        self.venue.order['coin'] = '@20'
        with self.assertRaises(ValueError): self.client.reconcile('first')
        with self.assertRaises(ValueError): self.submit('new-after-invalid-proof')
        self.client.clock = lambda: NOW+60000
        self.venue.acknowledge(self.client)
        self.assertEqual(self.client.reconcile('first')['status'], 'RECONCILED_OPEN')
        with self.assertRaises(ValueError): self.client.cancel('foreign')
        self.assertEqual(len(self.writes()), 1)

    def test_failed_refresh_cannot_reuse_previous_reconciliation(self):
        self.submit(); self.venue.acknowledge(self.client); self.client.reconcile('first')
        self.venue.status = 'unknownOid'
        self.assertEqual(self.client.reconcile('first')['status'], 'UNKNOWN_REQUIRES_RECONCILIATION')
        with self.assertRaises(ValueError): self.submit('another')

    def test_owned_cancel_nonce_and_terminal_reconciliation(self):
        self.submit(); self.venue.acknowledge(self.client)
        self.client.clock = lambda: NOW+60000
        result = self.client.cancel('first')
        self.assertEqual(result['status'], 'RESPONSE_RECEIVED_REQUIRES_RECONCILIATION')
        actions = self.writes()
        self.assertGreater(actions[1][1]['nonce'], actions[0][1]['nonce'])
        self.assertEqual(actions[1][1]['action']['type'], 'cancelByCloid')
        with self.assertRaises(ValueError): self.client.cancel('first')
        self.client.clock = lambda: NOW+120000
        self.venue.status = 'canceled'; self.venue.order['sz'] = '0'
        self.client.reconcile('first')
        self.assertEqual(self.client.con.execute("SELECT status FROM operations WHERE kind='cancel'").fetchone()[0], 'RECONCILED_TERMINAL')

    def test_changed_metadata_blocks_cancel(self):
        self.submit(); self.venue.acknowledge(self.client)
        self.client.clock = lambda: NOW+60000
        self.venue.spot['universe'][0]['index'] = 20
        with self.assertRaises(ValueError): self.client.cancel('first')
        self.assertEqual(len(self.writes()), 1)

    def test_limits_are_lifetime_and_binding_immutable(self):
        with self.assertRaises(ValueError): self.submit(price='101')
        self.client.clock = lambda: NOW+60000
        self.submit(quantity='5'); self.venue.acknowledge(self.client, 'filled'); self.client.reconcile('first')
        self.client.clock = lambda: NOW+120000
        self.submit('second', quantity='5')
        self.venue.status = 'filled'
        intent = json.loads(self.client.con.execute("SELECT intent FROM operations WHERE client='second'").fetchone()[0])
        self.venue.order['cloid'] = intent['action']['orders'][0]['c']
        self.client.reconcile('second')
        with self.assertRaises(ValueError): self.submit('third')
        self.client.close(); self.clients.remove(self.client)
        with self.assertRaises(ValueError): self.make(session_cap='201')

    def test_lock_contention_and_expiry(self):
        with self.assertRaises(BlockingIOError): self.make()
        self.client.clock = lambda: NOW+3600001
        with self.assertRaises(ValueError): self.submit()
        self.assertFalse(self.writes())

    def test_request_budget_is_bounded_and_preserved_on_restart(self):
        for _ in range(8): self.client.send('info', {'type': 'userRole', 'user': AGENT})
        with self.assertRaises(ValueError): self.client.send('info', {'type': 'userRole', 'user': AGENT})
        self.client.close(); self.clients.remove(self.client); self.client = self.make()
        with self.assertRaises(ValueError): self.client.send('info', {'type': 'userRole', 'user': AGENT})
        self.client.clock = lambda: NOW+60000
        self.client.send('info', {'type': 'userRole', 'user': AGENT})

    def test_failed_info_reservation_survives_exception(self):
        def fail(kind, payload): raise TimeoutError('do not persist')
        self.client._transport = fail
        with self.assertRaises(TimeoutError): self.client.check_role()
        self.assertEqual(self.client.con.execute('SELECT sum(weight) FROM requests').fetchone()[0], 60)

    def test_perpetual_only_genuinely_reducing_position(self):
        for side, quantity, reduce in [('sell', '1', False), ('buy', '1', True), ('sell', '3', True)]:
            self.client.clock = lambda n=len(self.venue.requests): NOW+n*60000
            with self.subTest(side=side, quantity=quantity, reduce=reduce), self.assertRaises(ValueError):
                self.submit(product='perpetual', symbol='SOL', side=side, quantity=quantity, reduce_only=reduce)
        self.client.clock = lambda: NOW+1800000
        self.submit(product='perpetual', symbol='SOL', side='sell', quantity='1', reduce_only=True)
        self.assertTrue(self.writes()[0][1]['action']['orders'][0]['r'])


if __name__ == '__main__': unittest.main()
