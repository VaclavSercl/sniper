# Production reconciliation, 2026-09-29

Owner scope: repair the reviewed Hermes changes, truthful health checks and data
collection/monitoring. This change does not activate trading or qualify a strategy.
Baseline repository commit: c2dbed4c26d97f44f834c81b6cc214017a676980.
Previous release: f9720df2235fde192393715dd02dc553fe5357d7, with three known edits.

## Repair and validation

Funding writers use the real composite key (symbol, src, funding_time), reject
invalid identities/nonfinite values, and surface database/API failures without
automatic sudo. A private PostgreSQL regression checks two instruments at the
same timestamp and replay deduplication. Runtime collectors do not create schema.

The funding service captures BTC/ETH/SOL with a two-day overlap each hour at
minute 05 UTC. It is incremental collection, not a repair of historical gaps.
The official Bitfinex pair inventory observed 2026-09-29 contains the configured
pairs except EURUSD. Both collectors explicitly report tEURUSD as unavailable;
no synthetic substitute is introduced. T15 input requirements remain unchanged.

The CPU checker accepts only documented known mitigated states, returns 1 for
vulnerable and 2 for unknown/unreadable. A live unprivileged read returned
Vulnerable: No microcode. No microcode installation or reboot is performed.

The Hyperliquid monitor uses metaAndAssetCtxs, validates required instruments,
fixes the 0.005 percent hourly threshold (fraction 0.00005), keeps failed account
reads unknown, and returns failure on HTTP/data errors. Data receipt time is
not claimed as an exchange timestamp. Funding snapshots never become settlement
or realized profit. No account is silently selected. CLI accepts --account and
--output-dir; the legacy entrypoint must be migrated with its existing explicit
configuration before scheduled operation can use this monitor.

P014 collection preserves old JSONL, rejects invalid/stale/crossed observations,
uses an exclusive cooperative lock and bounded worker, and stops at the existing
2026-10-06 19:00 UTC deadline. Timeout may leave a lock or an uncertain last write;
inspect before recovery, never clear unknown locks automatically. Wrapper migration
requires exact old-wrapper and active-release manifest hashes and retains a backup.

## Data audit limitations

The approved SQL ran as a read-only repeatable-read transaction on 2026-09-29
20:12 UTC. Recent Binance series have one approximately four-hour internal gap;
the separate public BTCUSDT history has a 7-day 2-hour gap. Four Hyperliquid candle
series have no internal minute gaps in their roughly 9.6-day coverage, which is
not 35 days of data. Thin Bitfinex markets have many absent bars; these require
venue-specific analysis rather than automatic forward filling. No invalid close
was found by this query. Other OHLC/volume properties were not tested by that SQL.
The t15_settled_funding table is absent. These inputs cannot qualify live trading.
LightGBM and old paper results remain experimental/unqualified.

## Independent review and adjudication

Both Hermes and Agy were invoked on Beroun using their installed CLI interfaces,
with the host source filesystem read-only. Their own runtime directories remained
writable for normal provider operation; this was not credential isolation.
Hermes completed the design review; a larger source review hit its time budget.
Agy completed a text-only source review after correcting CLI prompt transport.
These are static opinions, not test attestations.

Agy's alleged missing updater parent directories/default file mode are disproved
by apply_cutover.atomic and the real isolated deployment tests. PostgreSQL 18.6
and postgresql@18-main are present and active. Candle gaps cannot be attributed
to the funding timer. The calendar scheduling improvement is accepted. Suggestions
to silently ignore log errors, accept zero mark prices, or relabel snapshots are
not adopted. Complete gate results and review transcripts are retained in the
operator's private reconciliation evidence, outside Git.

## Exact checks and deployment boundary

Run on Linux in an isolated copy with the already installed dependencies:

    python3 -B tools/verify_platform.py
    bash -n platform/runtime/scripts/post_reboot_check.sh
    bash -n infra/beroun/p014_capture.sh
    systemd-analyze verify infra/beroun/beroun-funding.service infra/beroun/beroun-funding.timer infra/beroun/beroun-tri-venue-ingest.service infra/beroun/beroun-tri-venue-ingest.timer
    git diff --cached --check

The source updater now also requires --expected-current-tree-sha256, obtained
from inspect_release on the actual previous directory. Preserve its three edits
and byte-identical perfect_market_ingest.py.bak. The backup is never copied into
the new release; any other unexpected file blocks deployment. A fallback to this
former altered release is explicitly labelled ROLLED_BACK_TO_KNOWN_DRIFT.

The polkit tool defaults to preview. Its separately authorized apply operation
renames only 51-sudo-manage-units.rules to an exact-hash .disabled recovery file.
It does not add a narrower grant or claim other system policies were reviewed.
Restore only after comparing recovery bytes and confirming the target is absent.

Publication, the privileged source switch, four unit replacements/reload, polkit
revocation and compatibility-wrapper migration require their concrete reviewed
operation approval. The completed read-only sudo approval does not cover them.
Keep the T15 timer disabled; do not activate research/trading without qualified
data, account/risk policy and the required separate authorization.
