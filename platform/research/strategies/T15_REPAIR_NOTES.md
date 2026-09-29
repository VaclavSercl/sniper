# T15 v2: accounting repair, not trading qualification

The previous paper epoch is not a performance baseline. Keep its database row
unchanged. The repaired daemon uses separate ID `t15_cross_basis_v2`, schema 2,
explicit init, and compare-and-swap persistence. Construction/status do not write.
Ticks never initialize or migrate state. SQL errors/timeouts are blocking and an
uncertain write must be reconciled by rereading state before another attempt.

## Evidence contract

Four exact closed-candle streams: Bitfinex tBTCEUR, tEURUSD, tUDCUSD and
Hyperliquid BTC-PERP, with source IDs declared in t15_paper_accounting.py.
Maximum age 180 seconds, cross-source skew 60 seconds. No fixed-price fallback.

Funding requires `t15_settled_funding`: venue hyperliquid, symbol BTC, whole-hour
settled_at, interval_ms 3600000, signed decimal rate, positive settlement
oracle_price and SHA-256 reference to archived source evidence. A hash reference
alone does not prove authenticity; the data importer must validate and archive
the exchange response. No importer is supplied because current ingest stores
context polls, not settlement oracle history. Never copy market_funding rows
into this table under an invented settlement timestamp.

Funding is quantity times settlement oracle price times hourly rate; positive
rates credit the hypothetical short, negative rates debit it. This matches the
[exchange specification](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding),
checked 2026-09-23. Replay is idempotent. Missing, conflicting or revised events
block; a five-minute grace permits settlement publication latency.

## Interpretation

Opening allocation is hypothetical, not an exchange fill. Fees, executable
prices, margin/liquidation mechanics, queue position and real execution evidence
are not implemented by this accounting repair. No synthetic arbitrage profit,
rebate or execution count is added. Qualification remains BLOCKED even when
individual accounting/statistical checks pass. Hourly/daily observations count
elapsed buckets, not ticks. Monitoring gaps invalidate statistical qualification.
Legacy t15_mica_cross_basis.py remains historical synthetic research and must
not authorize promotion of this daemon.

## Verification and rollout blockers

22 accounting/entrypoint tests passed on Windows Python 3.12 and Linux 3.14.
Five actual PostgreSQL 18 integration tests passed in a private temporary cluster
without TCP listening, sudo, production access or downloaded dependencies.
Four historical strategy tests passed; those are not a profitability result.
The subsequent isolation repair passed all 143 project/harness tests, with no
skips, and the current complete gate exited 0 in a private Linux fixture.
Evidence: outer repairs/evidence/beroun-gate-hRzbPEMe-*.log. The required T15 SQL
suite now starts its own validated cluster under the full fixture instead of
silently skipping when an older fixture environment is absent.

Software checkpoint/publication is separately owner-authorized, subject to exact
candidate verification and destination approval. Deployment, service restart or
new paper epoch does not follow from passing software tests. The authorized
production read-only audit found no tEURUSD series, sparse tBTCEUR/tUDCUSD candles,
only two BTC-PERP funding snapshots and no settled-event table. Before rollout:
provide validated quotes, settlement evidence feed and least-privilege DB role,
then verify data coverage and execution assumptions. Preserve the old row and
deployment revision for recovery. No real order submission.
