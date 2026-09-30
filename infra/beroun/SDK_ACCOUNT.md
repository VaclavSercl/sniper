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
