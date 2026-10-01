# Hyperliquid SDK and account evidence

`hyperliquid-sdk-py314-linux.lock` pins all thirty resolved packages and SHA256
wheel hashes for the tested Linux x86-64 / CPython 3.14.4 environment. It is not
a portable wheel lock for other platforms. SDK 0.24.0 was resolved from official
PyPI metadata and installed binary-only in a private Sniper operations venv.
No global runtime or Hermes environment was changed. `pip check` passed.
Official PyPI release metadata listed no known advisories for these exact thirty
versions at inspection time. This limited metadata check is not a complete audit.

Run `verify_hyperliquid_sdk.py` with that isolated interpreter to verify ten
offline signature/recovery and nonce/network/account/action mutation checks.
All fixtures use deterministic synthetic keys and make no network requests.
These results do not verify an order transport or testnet/funded execution.

The existing protected signing material was used locally once to derive the
public agent address and resolve `userRole` to the actual account. It remains in
its original protected administrative location. Account-identity evidence is
private runtime state and is never committed. Do not print or hash signing keys.

`install_hyperliquid_account.py --release <verified-SHA> --identity-source
<reviewed-account-observation.json> --source-sha256 <combined-evidence-digest>`
previews absent unit/configuration targets. With separately authorized `--apply`
it creates only root-owned reviewed units and a 0640 root:beroun public identity
file. Existing safe root-owned configuration directories remain unchanged.
First actual account-role and balance observation must pass before timer enable.
Failed installs preserve their evidence/configuration and stop the new timer;
they require reconciliation rather than repeating an absent-target installation.

`hyperliquid_account.py observe` runs as beroun using public addresses only. It
rechecks the current agent association before reading the actual account's
balances, positions, open orders and fees. It cannot read a signing key, sign,
send orders, approve agents, set leverage or transfer funds. Reporting rejects
observations older than one hour and publishes only a masked account identifier.
The thirty-minute timer is account monitoring, not live trading. Balances never
create a mandate; agent expiry and testnet order execution remain unverified.

## Explicit order boundary

The legacy router's Hyperliquid adapter is L0 diagnostics only. Its asset ID is
unresolved and `wire_ready=false`; L1/L2 reject without network access. There is
no fixed SOL/HYPE mapping, unknown-to-BTC fallback or placeholder signature.
All venues reject unknown capabilities and nonfinite numeric requests.

`platform/gateway/hyperliquid_orders.py` provides a separate testnet-only library.
It resolves exact product metadata, validates Decimal tick/lot rules without
rounding, uses the verified official SDK 0.24.0 signer, and restricts transport to
the fixed testnet host with redirects forbidden. Orders are explicit limits;
spot needs unheld inventory plus a 1% buy fee buffer. Perpetuals may only reduce
an observed position; opening perpetual risk remains blocked. Transfers, agent
approvals, leverage changes, market orders and mainnet dispatch are unavailable.

An explicit testnet session has an order cap, lifetime submitted-notional cap and
expiry at most 24 hours ahead. These are verification limits, not a live mandate.
The client persists a conservative 500-weight rolling one-minute request budget
before each call, including failed reads. It does not account for other clients
sharing an IP. Budget exhaustion stops the operation without implicit retries.
One dedicated signer and private Linux journal are required; another writer or
another journal for that signer is outside this cooperative trust boundary.
The journal binds account, signer and limits, locks its writer, reserves unique
nonces and client IDs, and persists an intent before sending. Every HTTP response
requires independent order-status reconciliation. Unknown transport outcomes,
interrupted sends and failed refreshes block new submissions. Existing IDs never
resubmit. Cancellation requires a recorded, exactly reconciled owned open order
and unchanged metadata; ambiguous cancels cannot retry blindly. The journal is
not an account PnL ledger, and these checks are not paper qualification.

No execution service, signing credential or real testnet order has been installed
by this source change. Offline transport fixtures and actual synthetic SDK
signing remain separate evidence. A genuine testnet execution demonstration
requires an associated funded testnet account and an explicit testnet session.
Mainnet remains blocked by zero qualified candidates, incomplete forward-paper
execution and absent owner capital/loss/drawdown/leverage limits. A 30 real-day
paper interval cannot be replaced by historical replay or passing unit tests.

October 1 verification: 288 Linux application/architecture tests passed, plus
fourteen exact-source synthetic signing checks using the installed SDK. A
read-only testnet role query returned `missing` for the existing account and
agent. No exchange-write verification or account PnL claim follows from these
results. Obtain a genuine testnet association and explicit test session before
the real testnet demonstration. The production account monitor is independent.
