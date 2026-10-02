#!/usr/bin/env python3
"""Exact order preparation and at-most-once TESTNET operations.

No mainnet transport, CLI credential reader or automatic promotion. This is a
library for explicit testnet verification, not a running trading strategy. Its
private journal is the single nonce authority for one dedicated testnet signer;
sharing the signer with another writer or deleting its journal is unsupported.
"""
from dataclasses import dataclass
from decimal import Decimal
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import urllib.request
from portfolio_risk import RiskBook, MAX_AGE_MS
from order_validation import integer, address, number, wire
from spot_risk_observation import observe as observe_spot_risk

TESTNET = 'https://api.hyperliquid-testnet.xyz'
LIMIT = 1024 * 1024


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


@dataclass(frozen=True)
class Instrument:
    product: str
    symbol: str
    coin: str
    asset: int
    size_decimals: int
    base_index: int | None = None
    quote_index: int | None = None
    base_token: str | None = None
    quote_token: str | None = None


def resolve(product, symbol, meta, spot):
    """Main DEX perpetuals or exact USDC spot pair; no alias/default asset."""
    if product not in ('spot', 'perpetual') or not isinstance(symbol, str):
        raise ValueError('Explicit product/instrument required')
    if product == 'perpetual':
        universe = meta['universe']
        if not isinstance(universe, list) or len(universe) > 10000:
            raise ValueError('Invalid metadata')
        found = [(i, r) for i, r in enumerate(universe) if r['name'] == symbol]
        if len(found) != 1 or found[0][1].get('isDelisted', False):
            raise ValueError('Unknown, ambiguous or delisted perpetual')
        i, row = found[0]
        return Instrument(product, symbol, symbol, i, integer(row['szDecimals'], 0, 6))
    parts = symbol.split('/')
    if len(parts) != 2 or parts[1] != 'USDC' or not parts[0]:
        raise ValueError('Exact USDC spot pair required')
    tokens = {}
    if len(spot['tokens']) > 10000 or len(spot['universe']) > 10000:
        raise ValueError('Unbounded spot metadata')
    for row in spot['tokens']:
        i = integer(row['index'], 0, 100000)
        if i in tokens:
            raise ValueError('Duplicate token index')
        tokens[i] = row
    found = []
    for row in spot['universe']:
        if len(row['tokens']) != 2:
            raise ValueError('Invalid spot pair')
        base, quote = [tokens[integer(i, 0, 100000)] for i in row['tokens']]
        if [base['name'], quote['name']] == parts:
            found.append((row, base, quote))
    if len(found) != 1:
        raise ValueError('Unknown or ambiguous spot pair')
    pair, base, quote = found[0]
    index = integer(pair['index'], 0, 100000)
    for token in (base, quote):
        if not re.fullmatch(r'0x[0-9a-fA-F]{32}', token['tokenId']):
            raise ValueError('Missing immutable token identity')
    return Instrument(product, symbol, 'PURR/USDC' if parts[0] == 'PURR' else '@'+str(index),
                      10000+index, integer(base['szDecimals'], 0, 8),
                      base['index'], quote['index'], base['tokenId'], quote['tokenId'])


def prepare(instrument, side, price, quantity, post_only, reduce_only, cloid):
    if side not in ('buy', 'sell') or type(post_only) is not bool or type(reduce_only) is not bool:
        raise ValueError('Invalid side/flags')
    if not re.fullmatch(r'0x[0-9a-f]{32}', cloid):
        raise ValueError('Invalid client order ID')
    if instrument.product == 'spot' and reduce_only:
        raise ValueError('Spot has no perpetual reduce-only semantics')
    p, q = number(price), number(quantity)
    pt, qt = wire(p), wire(q)
    decimals = len(pt.split('.')[1]) if '.' in pt else 0
    size_decimals = len(qt.split('.')[1]) if '.' in qt else 0
    significant = len(pt.replace('.', '').lstrip('0'))
    if decimals > (6 if instrument.product == 'perpetual' else 8) - instrument.size_decimals:
        raise ValueError('Price violates tick precision')
    if decimals and significant > 5 or size_decimals > instrument.size_decimals:
        raise ValueError('Price/quantity precision would require rounding')
    return {'type': 'order', 'orders': [{'a': instrument.asset, 'b': side == 'buy',
            'p': pt, 's': qt, 'r': reduce_only, 't': {'limit': {'tif': 'Alo' if post_only else 'Gtc'}},
            'c': cloid}], 'grouping': 'na'}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Redirect forbidden at exchange boundary')


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON field')
            result[key] = value
        return result
    def invalid(_):
        raise ValueError('Nonfinite JSON')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def transport(kind, payload):
    if kind not in ('info', 'exchange'):
        raise ValueError('Unsupported endpoint')
    if kind == 'info' and payload.get('type') not in (
            'meta', 'spotMeta', 'userRole', 'spotClearinghouseState', 'clearinghouseState', 'orderStatus',
            'openOrders', 'userFees', 'l2Book', 'userNonFundingLedgerUpdates', 'userFillsByTime'):
        raise ValueError('Unsupported read-only request')
    if kind == 'exchange' and payload.get('action', {}).get('type') not in ('order', 'cancelByCloid'):
        raise ValueError('Unsupported signed action')
    # Keep action field insertion order used by SDK msgpack hashing. Sorted
    # journal JSON is an integrity representation, not a wire serializer.
    raw = request_bytes(payload)
    if len(raw) > 8192:
        raise ValueError('Oversized request')
    req = urllib.request.Request(TESTNET+'/'+kind, data=raw,
            headers={'Content-Type': 'application/json', 'User-Agent': 'Sniper-testnet-verification/1'}, method='POST')
    with urllib.request.build_opener(NoRedirect()).open(req, timeout=10) as response:
        raw = response.read(LIMIT+1)
    if len(raw) > LIMIT:
        raise ValueError('Oversized exchange response')
    return decode(raw)


def request_bytes(payload):
    return json.dumps(payload, separators=(',', ':'), allow_nan=False).encode()


def sdk_sign(wallet, action, nonce, expires):
    if version('hyperliquid-python-sdk') != '0.24.0':
        raise ValueError('Unverified SDK version')
    from hyperliquid.utils.signing import sign_l1_action
    return sign_l1_action(wallet, action, None, nonce, expires, False)


SCHEMA = '''CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE operations(id TEXT PRIMARY KEY,kind TEXT NOT NULL,client TEXT NOT NULL,
 nonce INTEGER UNIQUE NOT NULL,intent TEXT NOT NULL,status TEXT NOT NULL,result TEXT NOT NULL);
CREATE TABLE events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,operation TEXT NOT NULL,
 time_ms INTEGER NOT NULL,status TEXT NOT NULL,evidence TEXT NOT NULL);
CREATE TABLE requests(time_ms INTEGER NOT NULL,weight INTEGER NOT NULL);'''


class TestnetClient:
    """Single dedicated signer + private durable journal, without retry-on-write.

    Limits are explicit testnet verification limits, never a live mandate.
    Total submitted order notional is capped for this journal's whole lifetime;
    reconciliation does not replenish it. Perpetuals may only reduce a currently
    observed position. No subaccounts/vaults, market orders or transfers.
    """
    journal_name = 'testnet.sqlite3'
    prepare_action = staticmethod(prepare)

    def __init__(self, root, wallet, account, order_cap, session_cap,
                 expires_ms, *, send=transport, signer=sdk_sign, clock=None):
        if os.name != 'posix':
            raise ValueError('Durable testnet execution supports verified Linux only')
        self.clock = clock or (lambda: int(time.time()*1000))
        self.account, self.agent = address(account), address(wallet.address)
        self.order_cap, self.session_cap = number(order_cap), number(session_cap)
        if self.order_cap > self.session_cap:
            raise ValueError('Order cap exceeds session cap')
        self.expires_ms = integer(expires_ms)
        self.wallet, self._transport, self.signer = wallet, send, signer
        root = Path(root).absolute()
        if any(p.is_symlink() for p in (root, *root.parents)):
            raise ValueError('Unsafe execution state path')
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if root.stat().st_mode & 0o077:
            raise ValueError('Execution state must be private')
        path = root/self.journal_name
        if path.is_symlink() or path.exists() and (path.stat().st_nlink != 1 or path.stat().st_mode & 0o077):
            raise ValueError('Unsafe journal')
        if path.exists() and path.stat().st_size > 32*1024*1024:
            raise ValueError('Execution journal requires reviewed retention')
        import fcntl
        lock_path = root/'writer.lock'
        if lock_path.is_symlink() or lock_path.exists() and lock_path.stat().st_nlink != 1:
            raise ValueError('Unsafe writer lock')
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        self.lock = os.fdopen(fd, 'a+b')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.lock.close()
            raise
        exists = path.exists()
        self.con = None
        try:
            self.con = sqlite3.connect(path, timeout=2, isolation_level=None)
            self.con.execute('PRAGMA synchronous=FULL')
            if not exists:
                path.chmod(0o600)
                self.con.executescript(SCHEMA)
                with self.transaction():
                    self.con.execute('INSERT INTO settings VALUES(?,?)', ('schema', '1'))
                    self.con.execute('INSERT INTO settings VALUES(?,?)', ('nonce', '0'))
                    self.con.execute('INSERT INTO settings VALUES(?,?)', ('binding', self.binding()))
            if self.con.execute("SELECT value FROM settings WHERE key='schema'").fetchone() != ('1',) or \
                    self.con.execute("SELECT value FROM settings WHERE key='binding'").fetchone() != (self.binding(),):
                raise ValueError('Journal identity/limit mismatch')
            self.risk = RiskBook(self.con)
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except BaseException:
            if self.con is not None:
                self.con.close()
            self.lock.close()
            raise

    def binding(self):
        return encode({'network': 'testnet', 'agent': self.agent, 'account': self.account,
                       'order_cap': wire(self.order_cap), 'session_cap': wire(self.session_cap),
                       'expires_ms': self.expires_ms})

    def close(self):
        self.con.close()
        self.lock.close()

    def send(self, kind, payload):
        # Conservative rolling window budget for this client only. Reserve in
        # the same transaction as its durable intent or read-only refresh. No
        # sleep, implicit retry or claim about other processes sharing an IP.
        now = integer(self.clock())
        if self.con.in_transaction:
            raise ValueError('Network cannot run inside a rollbackable state transaction')
        with self.transaction():
            latest = self.con.execute('SELECT max(time_ms) FROM requests').fetchone()[0]
            if latest is not None and now < latest:
                raise ValueError('Request clock moved backward')
            used = self.con.execute('SELECT coalesce(sum(weight),0) FROM requests WHERE time_ms>?', (now-60000,)).fetchone()[0]
            weight = 60 if kind == 'info' and payload['type'] == 'userRole' else 20
            if used+weight > 500:
                raise ValueError('Testnet client request budget unavailable')
            self.con.execute('INSERT INTO requests VALUES(?,?)', (now, weight))
        return self._transport(kind, payload)

    def transaction(self):
        client = self
        class Transaction:
            def __enter__(self):
                client.con.execute('BEGIN IMMEDIATE')
            def __exit__(self, kind, exc, tb):
                client.con.execute('ROLLBACK' if kind else 'COMMIT')
        return Transaction()

    def check_role(self):
        now = integer(self.clock())
        if not now < self.expires_ms <= now+24*3600000:
            raise ValueError('Expired or unbounded testnet session')
        role = self.send('info', {'type': 'userRole', 'user': self.agent})
        if self.agent == self.account:
            valid = role == {'role': 'user'}
        else:
            valid = role.get('role') == 'agent' and address(role['data']['user']) == self.account
        if not valid:
            raise ValueError('Testnet signer/account association unavailable')
        account_role = self.send('info', {'type': 'userRole', 'user': self.account})
        if account_role != {'role': 'user'}:
            raise ValueError('Only explicit master testnet accounts supported')

    def instrument(self, product, symbol):
        return resolve(product, symbol, self.send('info', {'type': 'meta'}),
                       self.send('info', {'type': 'spotMeta'}))

    def inventory(self, instrument, side, price, quantity, reduce_only):
        if instrument.product == 'spot':
            data = self.send('info', {'type': 'spotClearinghouseState', 'user': self.account})
            index = instrument.quote_index if side == 'buy' else instrument.base_index
            rows = [r for r in data['balances'] if r['token'] == index]
            if len(rows) != 1:
                raise ValueError('Missing/ambiguous testnet inventory')
            total, hold = number(rows[0]['total'], False), number(rows[0]['hold'], False)
            required = number(price)*number(quantity)*Decimal('1.01') if side == 'buy' else number(quantity)
            if hold > total or total-hold < required:
                raise ValueError('Insufficient unheld testnet inventory including fee buffer')
        else:
            if not reduce_only:
                raise ValueError('New perpetual risk requires unimplemented margin/leverage qualification')
            data = self.send('info', {'type': 'clearinghouseState', 'user': self.account})
            rows = [r['position'] for r in data['assetPositions'] if r['position']['coin'] == instrument.coin]
            if len(rows) != 1:
                raise ValueError('No unique reducible position')
            text = rows[0]['szi']
            position = number(text[1:] if isinstance(text, str) and text.startswith('-') else text)
            short = isinstance(text, str) and text.startswith('-')
            if (side == 'buy') != short or number(quantity) > position:
                raise ValueError('Order could increase/flip perpetual position')

    def record(self, operation, status, evidence):
        # Evidence never includes signature/key or raw exception text.
        raw = encode(evidence)
        self.con.execute('UPDATE operations SET status=?,result=? WHERE id=?', (status, raw, operation))
        self.con.execute('INSERT INTO events(operation,time_ms,status,evidence) VALUES(?,?,?,?)',
                         (operation, integer(self.clock()), status, raw))

    def reserve(self, operation, kind, client, intent):
        now = integer(self.clock())
        last = integer(int(self.con.execute("SELECT value FROM settings WHERE key='nonce'").fetchone()[0]))
        nonce = max(now, last+1)
        if nonce > now+1000:
            raise ValueError('Journal clock/nonce drift')
        self.con.execute('UPDATE settings SET value=? WHERE key=?', (str(nonce), 'nonce'))
        self.con.execute('INSERT INTO operations VALUES(?,?,?,?,?,?,?)',
                         (operation, kind, client, nonce, encode(intent), 'PREPARED', '{}'))
        self.record(operation, 'PREPARED', {'nonce': nonce, 'expires_ms': min(now+10000, self.expires_ms)})
        return nonce, min(now+10000, self.expires_ms)

    def dispatch(self, operation, action, nonce, expires):
        with self.transaction():
            row = self.con.execute('SELECT nonce,intent,status,result FROM operations WHERE id=?', (operation,)).fetchone()
            if row is None or row[0] != nonce or row[2] != 'PREPARED' or \
                    encode(json.loads(row[1])['action']) != encode(action) or json.loads(row[3])['expires_ms'] != expires:
                raise ValueError('Dispatch requires the exact unused durable intent')
            intent = json.loads(row[1])
            needs_risk = (action['type'] == 'order' and intent['instrument']['product'] == 'spot'
                          and action['orders'][0]['b'])
            if needs_risk:
                state = self.risk.state()
                reservation = self.con.execute('SELECT notional FROM risk_reservations WHERE operation=?',
                                               (operation,)).fetchone()
                if (intent.get('risk_required') is not True or not reservation or
                        reservation[0] != intent['notional'] or not state or
                        not state['fresh'] or state['halted'] or
                        not 0 <= integer(self.clock())-state['time_ms'] <= MAX_AGE_MS):
                    raise ValueError('Exact fresh durable risk reservation required before signing')
            if (action['type'] == 'order' and intent['instrument']['product'] == 'perpetual'
                    and action['orders'][0]['r'] is not True):
                raise ValueError('Prepared perpetual intent cannot introduce new risk')
        try:
            if integer(self.clock()) >= expires:
                raise ValueError('Intent expired before signing')
            self.check_dispatch_authority(operation, action, nonce, expires)
            signature = self.signer(self.wallet, action, nonce, expires)
            if set(signature) != {'r', 's', 'v'} or signature['v'] not in (27, 28) or any(
                    not isinstance(signature[k], str) or not re.fullmatch(r'0x[0-9a-fA-F]{1,64}', signature[k]) or
                    int(signature[k], 16) == 0 for k in ('r', 's')):
                raise ValueError('Invalid signature')
            self.check_dispatch_authority(operation, action, nonce, expires)
            with self.transaction():
                self.record(operation, 'DISPATCH_STARTED', {'nonce': nonce, 'expires_ms': expires})
            if integer(self.clock()) >= expires:
                raise ValueError('Intent expired before transport')
            self.check_dispatch_authority(operation, action, nonce, expires)
            response = self.send('exchange', {'action': action, 'nonce': nonce,
                'signature': signature, 'vaultAddress': None, 'expiresAfter': expires})
            # An HTTP response is not fill/account reconciliation, even when ok.
            state = 'RESPONSE_RECEIVED_REQUIRES_RECONCILIATION'
            evidence = {'response_sha256': hashlib.sha256(encode(response).encode()).hexdigest()}
        except Exception as exc:
            state, evidence = 'UNKNOWN_REQUIRES_RECONCILIATION', {'error_type': type(exc).__name__}
        with self.transaction():
            self.record(operation, state, evidence)
        return {'operation': operation, 'status': state, 'live_eligible': False}

    def check_dispatch_authority(self, operation, action, nonce, expires):
        if json.loads(self.binding())['network'] != 'testnet':
            raise ValueError('Non-testnet clients require separate dispatch authority')

    def check_submission_limits(self, product, side, notional):
        if notional > self.order_cap:
            raise ValueError('Explicit testnet order limit')
        spent = sum((number(json.loads(r[0])['notional']) for r in self.con.execute(
                     "SELECT intent FROM operations WHERE kind='order'")), Decimal(0))
        if spent+notional > self.session_cap:
            raise ValueError('Explicit testnet lifetime submission limit')

    def observe_submission(self, instrument, side):
        if instrument.product == 'spot' and side == 'buy':
            self.observe_risk(instrument)

    def submit(self, client_id, product, symbol, side, price, quantity, *, post_only=True, reduce_only=False):
        if not isinstance(client_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', client_id):
            raise ValueError('Invalid owned client ID')
        operation = 'order:'+client_id
        notional = number(price)*number(quantity)
        cloid = '0x'+hashlib.sha256((self.binding()+'\0'+client_id).encode()).hexdigest()[:32]
        with self.transaction():
            if self.con.execute('SELECT 1 FROM operations WHERE id=?', (operation,)).fetchone():
                raise ValueError('Existing intent; reconcile without resubmitting')
            if self.con.execute("SELECT 1 FROM operations WHERE status IN ('PREPARED','DISPATCH_STARTED',"
                    "'RECONCILIATION_STARTED','UNKNOWN_REQUIRES_RECONCILIATION',"
                    "'RESPONSE_RECEIVED_REQUIRES_RECONCILIATION')").fetchone():
                raise ValueError('Unreconciled operation blocks new dispatch')
            self.check_submission_limits(product,side,notional)
        self.check_role()
        instrument = self.instrument(product, symbol)
        action = self.prepare_action(instrument, side, price, quantity, post_only, reduce_only, cloid)
        self.inventory(instrument, side, price, quantity, reduce_only)
        # The implemented entry path cannot bypass portfolio admission. Genuine
        # inventory-reducing exits and owned cancellation remain possible even
        # after a risk halt; the preceding inventory check proves no short/flip.
        risk_required = instrument.product == 'spot' and side == 'buy'
        self.observe_submission(instrument,side)
        with self.transaction():
            intent = {'instrument': instrument.__dict__, 'action': action, 'notional': wire(notional),
                      'risk_required': risk_required}
            if risk_required:
                intent['risk'] = self.risk.reserve(operation, wire(notional), integer(self.clock()))
            nonce, expires = self.reserve(operation, 'order', client_id, intent)
        return self.dispatch(operation, action, nonce, expires)

    def observe_risk(self, instrument):
        return observe_spot_risk(self, instrument)

    def reconcile(self, client_id):
        with self.transaction():
            row = self.con.execute('SELECT intent FROM operations WHERE id=?', ('order:'+client_id,)).fetchone()
            if row is None:
                raise ValueError('Unknown order ownership')
            intent = json.loads(row[0]); order = intent['action']['orders'][0]
            self.record('order:'+client_id, 'RECONCILIATION_STARTED', {})
        # Invalidate the previous observation durably before network/proof. A
        # failed or interrupted refresh cannot preserve a prior apparent pass.
        self.check_role()
        data = self.send('info', {'type': 'orderStatus', 'user': self.account, 'oid': order['c']})
        with self.transaction():
            if data.get('status') != 'order':
                self.record('order:'+client_id, 'UNKNOWN_REQUIRES_RECONCILIATION', {})
                return {'status': 'UNKNOWN_REQUIRES_RECONCILIATION', 'live_eligible': False}
            current, status = data['order']['order'], data['order']['status']
            if current['cloid'] != order['c'] or current['coin'] != intent['instrument']['coin'] or \
                    current['side'] != ('B' if order['b'] else 'A') or \
                    number(current['limitPx']) != number(order['p']) or number(current['origSz']) != number(order['s']) or \
                    type(current['reduceOnly']) is not bool or current['reduceOnly'] != order['r']:
                raise ValueError('Venue order identity differs from owned intent')
            remaining = number(current['sz'], False)
            if remaining > number(order['s']):
                raise ValueError('Invalid remaining size')
            integer(current['oid'], 1)
            if status not in ('open', 'filled', 'canceled', 'rejected', 'marginCanceled', 'reduceOnlyCanceled',
                    'selfTradeCanceled', 'delistedCanceled', 'scheduledCancel', 'liquidatedCanceled',
                    'badAloPxRejected', 'minTradeNtlRejected', 'insufficientSpotBalanceRejected'):
                raise ValueError('Unverified venue order status')
            if status == 'filled' and remaining != 0:
                raise ValueError('Inconsistent filled status')
            evidence = {'venue_status': status, 'oid': current['oid'], 'remaining': wire(remaining),
                        'observed_ms': integer(self.clock())}
            self.record('order:'+client_id, 'RECONCILED_'+status.upper(), evidence)
            # Read-only proof of current order state reconciles a pending cancel;
            # an open order after cancel dispatch remains ambiguous, never retry.
            if status != 'open' and self.con.execute('SELECT 1 FROM operations WHERE id=?', ('cancel:'+client_id,)).fetchone():
                self.record('cancel:'+client_id, 'RECONCILED_TERMINAL', evidence)
            return {'status': 'RECONCILED_'+status.upper(), **evidence, 'live_eligible': False}

    def cancel(self, client_id):
        observed = self.reconcile(client_id)
        if observed['status'] != 'RECONCILED_OPEN':
            raise ValueError('Cancel requires an owned reconciled open order')
        operation = 'cancel:'+client_id
        with self.transaction():
            if self.con.execute('SELECT 1 FROM operations WHERE id=?', (operation,)).fetchone():
                raise ValueError('Existing cancel intent; no blind retry')
            intent = json.loads(self.con.execute('SELECT intent FROM operations WHERE id=?', ('order:'+client_id,)).fetchone()[0])
        self.check_role()
        expected = Instrument(**intent['instrument'])
        if self.instrument(expected.product, expected.symbol) != expected:
            raise ValueError('Asset identity changed since order submission')
        with self.transaction():
            action = {'type': 'cancelByCloid', 'cancels': [{'asset': expected.asset,
                       'cloid': intent['action']['orders'][0]['c']}]}
            nonce, expires = self.reserve(operation, 'cancel', client_id, {'action': action})
        return self.dispatch(operation, action, nonce, expires)
