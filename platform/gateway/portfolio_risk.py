"""Decimal portfolio limits in the order journal's existing SQLite transaction.

The caller owns the single-writer lock and authenticates fresh complete venue
observations. This is a cooperative testnet guard, not an independent mainnet
security boundary. Non-trading cashflows, unknown ownership and observation gaps
require reconciliation; they never reset the high-water mark or loss allowance.
"""
from decimal import Decimal, InvalidOperation
import json

DAY_MS = 86400000
MAX_AGE_MS = 5000
MAX_GAP_MS = 60000
PROFILE = {
    'capital': '0.9', 'strategy': '0.2', 'order': '0.25',
    'daily_loss': '0.01', 'drawdown': '0.03', 'fee_buffer': '0.01',
}


def amount(value):
    if type(value) is not str or len(value) > 64:
        raise ValueError('Exact risk decimal string required')
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ValueError('Invalid risk number') from None
    if (not result.is_finite() or result < 0 or result > Decimal('1e15') or
            not -30 <= result.as_tuple().exponent <= 15):
        raise ValueError('Unbounded risk number')
    return result


def stamp(value):
    if type(value) is not int or not 0 < value < 2**63:
        raise ValueError('Invalid risk timestamp')
    return value


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


class RiskBook:
    """No separate connection/commit: reservations share order-intent atomicity.

    Equity is conservatively valued from actual unborrowed USDC spot inventory.
    Perpetual cash is excluded from deployable capital; perpetual positions block
    spot admission. The original 90% ceiling is never increased automatically.
    One strategy owns this journal. Even reconciled open buy orders consume its
    allocation. Unknown/partial outcomes continue consuming their full reserve.
    """
    def __init__(self, connection):
        self.con = connection
        connection.execute('CREATE TABLE IF NOT EXISTS risk_state '
                           '(id INTEGER PRIMARY KEY CHECK(id=1),record TEXT NOT NULL)')
        connection.execute('CREATE TABLE IF NOT EXISTS risk_observations '
                           '(sequence INTEGER PRIMARY KEY,time_ms INTEGER NOT NULL,record TEXT NOT NULL)')
        connection.execute('CREATE TABLE IF NOT EXISTS risk_reservations '
                           '(operation TEXT PRIMARY KEY,notional TEXT NOT NULL,cost TEXT NOT NULL)')

    def state(self):
        row = self.con.execute('SELECT record FROM risk_state WHERE id=1').fetchone()
        return json.loads(row[0]) if row else None

    def invalidate(self):
        state = self.state()
        if state:
            state['fresh'] = False
            self._save(state)

    def _save(self, state):
        self.con.execute('INSERT INTO risk_state VALUES(1,?) ON CONFLICT(id) '
                         'DO UPDATE SET record=excluded.record', (encode(state),))

    def observe(self, snapshot, now):
        if not self.con.in_transaction:
            raise ValueError('Risk observation requires the owned transaction')
        required = {'time_ms', 'equity', 'available', 'inventory', 'pending_buys',
                    'complete', 'cashflow_free', 'ownership_verified'}
        if set(snapshot) != required or any(snapshot[k] is not True for k in
                ('complete', 'cashflow_free', 'ownership_verified')):
            raise ValueError('Complete reconciled cashflow-free risk observation required')
        now, observed = stamp(now), stamp(snapshot['time_ms'])
        if not 0 <= now-observed <= MAX_AGE_MS:
            raise ValueError('Stale/future risk observation')
        equity, available, inventory, pending = [amount(snapshot[k]) for k in
                                                ('equity', 'available', 'inventory', 'pending_buys')]
        if equity <= 0 or available > equity or inventory > equity:
            raise ValueError('Inconsistent portfolio valuation')
        previous = self.state()
        if previous and (observed < previous['time_ms'] or
                         observed-previous['time_ms'] > MAX_GAP_MS):
            raise ValueError('Risk chronology gap requires evidence-based recovery')
        day = observed//DAY_MS
        if previous:
            budget = min(amount(previous['budget']), equity*Decimal(PROFILE['capital']))
            peak = max(amount(previous['peak']), equity)
            # A first observation after midnight must include the overnight loss;
            # resetting the anchor to current equity would erase that loss.
            anchor = (amount(previous['day_anchor']) if previous['day'] == day else
                      max(amount(previous['equity']), equity))
            halted = previous['halted']
        else:
            if inventory or pending:
                raise ValueError('Initial risk epoch requires a flat reconciled account')
            budget, peak, anchor, halted = equity*Decimal(PROFILE['capital']), equity, equity, False
        daily_loss, drawdown = max(Decimal(0), anchor-equity), peak-equity
        reasons = []
        if daily_loss >= budget*Decimal(PROFILE['daily_loss']):
            reasons.append('DAILY_LOSS_LIMIT')
        if drawdown >= budget*Decimal(PROFILE['drawdown']):
            reasons.append('DRAWDOWN_LIMIT')
        if inventory+pending > budget*Decimal(PROFILE['strategy']):
            reasons.append('STRATEGY_EXPOSURE_LIMIT')
        if halted and not reasons:
            reasons.append('PRIOR_BREACH_REQUIRES_REVIEW')
        state = {'schema': 1, 'profile': PROFILE, 'time_ms': observed, 'day': day,
                 'start_ms': previous['start_ms'] if previous else observed,
                 'initial_equity': previous['initial_equity'] if previous else str(equity),
                 'equity': str(equity), 'available': str(available), 'inventory': str(inventory),
                 'pending_buys': str(pending), 'budget': str(budget), 'peak': str(peak),
                 'day_anchor': str(anchor), 'daily_loss': str(daily_loss), 'drawdown': str(drawdown),
                 'halted': halted or bool(reasons), 'reasons': reasons, 'fresh': True}
        self._save(state)
        self.con.execute('INSERT INTO risk_observations(time_ms,record) VALUES(?,?)',
                         (observed, encode(state)))
        return state

    def reserve(self, operation, notional, now):
        if not self.con.in_transaction:
            raise ValueError('Risk reservation must be atomic with the order intent')
        state, now, notional = self.state(), stamp(now), amount(notional)
        if (not state or not state['fresh'] or state['halted'] or
                not 0 <= now-state['time_ms'] <= MAX_AGE_MS):
            raise ValueError('Risk admission blocked or stale')
        budget = amount(state['budget'])
        allocation = budget*Decimal(PROFILE['strategy'])
        if not Decimal('10') <= notional <= allocation*Decimal(PROFILE['order']):
            raise ValueError('Venue minimum or portfolio order cap')
        cost = notional*(1+Decimal(PROFILE['fee_buffer']))
        # New reservations after the observed account cut consume budget even if
        # a caller has not yet sent/reconciled them. Existing live-order reserves
        # are already counted by the caller's complete pending-buys observation.
        reserved = sum((amount(row[0]) for row in self.con.execute(
            'SELECT r.cost FROM risk_reservations r JOIN operations o ON o.id=r.operation '
            "WHERE o.status IN ('PREPARED','DISPATCH_STARTED','RECONCILIATION_STARTED',"
            "'UNKNOWN_REQUIRES_RECONCILIATION','RESPONSE_RECEIVED_REQUIRES_RECONCILIATION')")), Decimal(0))
        exposure = amount(state['inventory'])+amount(state['pending_buys'])+reserved+cost
        if (exposure > allocation or exposure > budget or
                cost+reserved > amount(state['available'])):
            raise ValueError('Portfolio inventory/pending/available allocation cap')
        if amount(state['daily_loss'])+cost-notional >= budget*Decimal(PROFILE['daily_loss']):
            raise ValueError('Fee buffer would exhaust remaining daily loss budget')
        self.con.execute('INSERT INTO risk_reservations VALUES(?,?,?)',
                         (operation, str(notional), str(cost)))
        return {'status': 'RISK_RESERVED', 'observed_ms': state['time_ms'],
                'notional': str(notional), 'reserved_cost': str(cost),
                'profile': 'owner-90pct-ai-canary-v1-testnet-only'}
