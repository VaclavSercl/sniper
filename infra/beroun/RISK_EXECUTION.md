# Hyperliquid risk enforcement and staged execution

## Current boundary

The implemented Hyperliquid exchange path is TESTNET ONLY. Its spot buy admission
now requires an exact durable portfolio reservation before signing. Mainnet
orders, automatic paper admission and funded promotion remain unavailable. A
source deployment does not enable a trading service or qualify a strategy.

The main-account owner mandate remains `/etc/sniper/hyperliquid-mandate.json`:
at most 90% of measured account equity; one canary strategy; allocation at most
20% of that budget; order at most 25% of allocation; daily loss at most 1% of
budget; drawdown at most 3%; perpetual leverage at most 1x. The testnet guard
uses the same conservative fractions on actual testnet USDC spot equity, excluding
perpetual cash from available capital. It does not read the mainnet private key,
move money, expand the root mandate or claim funded guardian enforcement.

## Components and transaction ownership

`platform/gateway/order_validation.py` contains bounded exact Decimal and public
identity validation. Existing order-module imports remain compatible.

`spot_risk_observation.py` reads the actual testnet account and proves inventory,
cash, open-order ownership and complete observed fills against the order journal.
One exact USDC spot instrument is supported per risk epoch. Other positive
inventory or any perpetual position blocks new spot risk. Positive perpetual
cash is conservatively excluded. Negative/unvalued equity is rejected.

`portfolio_risk.py` owns the numerical limits, chronological equity/high-water
mark, overnight loss anchor, breach latch and reservations. It uses the SAME
SQLite connection, transactions and single-writer lock as `hyperliquid_orders.py`.
An order's intent/nonce and its risk reservation commit together before dispatch.
No risk reservation is released just because a request timed out or a process
restarted. Actual terminal reconciliation determines whether an order remains
pending; an ambiguous state blocks new submissions.

The journal is local cooperative state. A process with the same Unix identity
can edit it; this is not a hostile-process security boundary or the independent
mainnet guardian. Funded deployment must place mandatory admission and continuous
loss/exposure supervision behind the separate guardian before it can be enabled.

## Admission and fail-closed cases

The initial risk epoch requires flat reconciled spot USDC and no existing order
history. An old journal is not silently adopted after the software upgrade.

Each new buy requires fresh bounded account reads, exact dynamic token identities,
the actual fee schedule, owned open orders, non-funding account ledger updates and
nonaggregated user fills. Only empty non-trading cashflow histories are accepted;
deposits, withdrawals, transfers or unknown updates require reviewed reconciliation.
Full-bound/truncated response sets are rejected instead of interpreted as complete.

Fill identifiers are unique; account, order ID, instrument, side, price, size and
fee token must match. Measured cash and inventory must exactly reconcile. Positive
inventory is valued against fresh observed bid depth, never an optimistic last
trade or an unmeasured mid. Insufficient depth or stale books block admission.
Observed held USDC must match owned pending buy exposure. Fees are included in
cash reconciliation and a conservative 1% reservation buffer.

Risk reads must finish within five seconds. The risk clock cannot go backward;
an observation gap over 60 seconds blocks automatic continuation. There is no
claim of reconstructing missing intragap drawdowns. A failed new observation
invalidates the previous fresh status before any network read.

The original budget never increases automatically after profits. Daily losses
and drawdowns use chronological equity; the first observation after midnight
preserves overnight losses. Breaches latch across restarts and recovery; no
automatic reset or removal of the journal restores admission. Cancellation of
owned orders and inventory-proven reducing exits remain available under the
separate existing testnet order/session caps. New perpetual risk stays blocked;
only venue reduce-only orders proven not to flip an existing position are allowed.

Prepared legacy buys without a durable risk reservation cannot be signed after
the upgrade. The dispatch boundary derives this requirement from the actual
product/side, not an optional caller flag. Mainnet transport is still absent.

## Real testnet activation

The actual main account and its existing agent returned `missing` on testnet on
October 1. A browser email login can create DIFFERENT mainnet/testnet addresses;
the public main account alone is not a login credential. Verify the exact account
shown in the browser before claiming test USDC or approving an agent.

Use a dedicated TESTNET agent. Generate and protect its private key on Beroun,
outside Git and browser transcripts, then authorize only its PUBLIC address in
the testnet UI. Actual association, mock balances, the real owned order lifecycle,
fill/account reconciliation and restart/disconnect behavior must all be verified.
The present source contains no automatic key bootstrap, authorization or claim
that a real testnet order has been executed. Existing mainnet signing material
must not be copied into a new testnet worker as a convenience.

The verification client has an explicit expiry of at most 24 hours and a lifetime
submitted-notional cap. It is not a persistent funded daemon. Reusing its signer
through another journal/process is unsupported. Keep unresolved journals for
reconciliation; never recreate them to reset nonce, loss or submission limits.

## Remaining lifecycle dependencies

1. Complete actual testnet verification and independent funded guardian integration.
2. Calibrate execution and independently qualify a frozen historical candidate.
3. Admit that candidate into a complete forward-paper engine for at least 30 REAL
   calendar days, with unchanged code/parameters/risk and actual causal evidence.
4. Extend the finite hypothesis generator through reviewed preregistration and a
   bounded provider/compute policy. Current six-blueprint exhaustion remains
   explicit; market/parameter variants are not invented economic hypotheses.
5. Promote only an exact qualified candidate to one capped canary, with adequate
   minimum order value, observed capacity/costs, reconciliation and enforced risk.

There is currently no historically/paper qualified candidate. Keep cash and
preserve the four pinned research modules, epoch and consumed holdouts. Neither
this document nor passing tests approves a new research epoch or bypasses gates.

## Verification and opposition

Pure Decimal/transaction tests cover fee allowance, pending reservations, original
budget, equity chronology, midnight losses, drawdown, stale/future/gapped evidence,
atomic rollback, unknown cashflows and restart-latched breaches. Linux order tests
add mandatory dispatch admission, old-intent refusal, account/fill ownership and
failed-observation invalidation to existing interruption/nonce/cancel regressions.

The complete existing gate uses architecture tests and a PRIVATE PostgreSQL
fixture, shell syntax and systemd verification. Real SDK signature recovery uses
synthetic keys without network. These are separate from actual testnet evidence.
Linux locking/fsync is tested on Beroun; Windows explicitly skips Linux-only order
tests. No macOS/WSL support, Rust validation or paid-provider review is claimed.

Adversarial self-review found and fixed optional-flag admission bypass, lost
overnight loss anchors, stale risk reuse, missing actual-fill/cashflow proof and
optimistic inventory valuation. Two preliminary gate failures are preserved:
one fixture exhausted the unchanged request budget, and one manifest used CRLF
working bytes instead of normalized Git index bytes. Neither weakened a check.
