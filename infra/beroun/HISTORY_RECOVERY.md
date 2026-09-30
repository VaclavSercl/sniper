# Historical recovery and GDS mitigation

The history recovery entrypoint stages public exchange responses into a new
private directory. It performs no database writes or privilege escalation.
Every request has a bounded, closed, half-open UTC interval of at most 35 days.
Binance spot, Bitfinex trade candles and Hyperliquid perpetual candles retain
their distinct source/instrument identities. No cross-venue substitution.

```
python3 -B platform/research/history_recovery.py fetch --venue binance --symbol BTCUSDT --start 2026-08-26T00:00:00Z --end 2026-09-30T00:00:00Z --out /private/new-bundle
python3 -B platform/research/history_recovery.py sql --bundle /private/new-bundle --sha256 REVIEWED_MANIFEST_SHA256 --out /private/new-import.sql
```

`fetch` returns 0 for complete coverage, 2 for incomplete coverage or failure.
A partial run without manifest is unusable. Sparse responses do not shorten
pagination; absence is never forward-filled. Bitfinex does not provide quote
volume/trade count here; these remain NULL. Hyperliquid public candle history
is limited to its most recent 5000 candles, so older history may remain absent.
Source bytes plus exact requests are evidence of retrieval, not a signed
exchange attestation. Do not represent these candles as historical executable
quotes, individual trades, oracle settlement prices, or funding payments.

HTTP 429 returns RATE_LIMITED with numeric Retry-After when supplied, without
automatic retries. Stop the venue batch on this result; multiple jobs and the
existing live collector share an IP quota. Bitfinex historical pages are paced
at seven seconds by default, but this is not a global quota guarantee. Preserve
partial bundles and wait for the venue cooldown before an explicit new attempt.

`sql` replays every archived response and binds the reviewed manifest digest.
It renders SQL only. Execution is separately authorized using the production
schema/role after a read-only inventory. It takes a bounded table lock, inserts
only missing identities and aborts on conflicting existing core OHLCV/time
values. Optional quote-volume/count differences do not cause existing rows to
be overwritten. NULL constraints in an incompatible schema cause rollback.
Run through `psql -X -w -v ON_ERROR_STOP=1 -f ...`, retain protected stdout/stderr,
and require observed COMMIT plus a subsequent read-only inventory. Returned
JSON contains the exact actually inserted rows, not the number fetched.

Capture a consistent pre-import export for the affected source/window and the
database backup identity before production execution. On transport failure the
outcome is UNKNOWN until reconciled. No automatic delete/rollback helper is
provided: intervening ingestion may make deletion unsafe. Replays are
idempotent; recovery/deletion requires reviewed insertion evidence and a fresh
ownership check. Raw data, generated SQL, insert receipts and test fixtures are
runtime artifacts and must remain outside the tracked application tree.

## CPU discovery, 2026-09-30

Beroun i5-6400 is running microcode 0xf0; the boot journal confirms early loading
from 0x74. intel-microcode 3.20260210.1ubuntu2 is already installed and is the
local package candidate. Reinstalling that package is not an evidenced fix.
The kernel still reports `Vulnerable: No microcode`.

The existing `/etc/default/grub.d/99-beroun-gds.cfg.disabled` sets
`gather_data_sampling=force`. The documented fallback disables AVX when the
microcode lacks GDS mitigation. Kernel CONFIG_MITIGATION_GDS being unset controls
the default; the explicit command-line force selector must be checked against
the running kernel version. Upstream Linux v7.0 source was checked and accepts
this explicit selector independently of that default. Ubuntu runtime behavior
still requires post-reboot verification. An enabled config file alone is not mitigation:
verify generated boot config, reboot under an approved maintenance operation,
then check actual kernel command line, GDS sysfs state and application health.

Changing GRUB, invoking update-grub and rebooting each require scoped owner sudo
approval. Preserve config hashes, original file and generated config. If SSH
does not return, recovery needs server-console access to remove the option for
one boot. Programs requiring AVX may be incompatible; no claim of runtime
compatibility can be made from ELF metadata alone. No CPU action is executed by
the recovery tool, and the GDS check must continue to report FAIL until mitigated.

## Primary sources

- https://www.kernel.org/doc/html/next/admin-guide/hw-vuln/gather_data_sampling.html
- https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md
- https://docs.bitfinex.com/reference/rest-public-candles
- https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint

T15 still depends on unavailable tEURUSD and verified historical settlement
oracle prices. Filling candle gaps cannot make that strategy qualified. The
Hydra research queue additionally needs authentic trade ticks, a fixed holdout
and explicit cost assumptions; candles cannot be relabelled as those ticks.
