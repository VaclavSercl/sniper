# Evidence-driven research Ă˘â‚¬â€ť implementation boundary

## Current owner scope and lifecycle policy (2026-09-30)

The owner selected **Hyperliquid, spot and perpetuals**, and wants automatic
selection after historical AND independent forward paper evidence. Canonical
structured rules are in `lifecycle_policy.json`; `lifecycle_overview.py` validates
them and the read-only registry exporter reports coverage and stage counts.
This is policy/reporting implementation, **not a promotion executor**. Existing
live refusal stays in force until the required execution and risk integrations
exist. A JSON field, timer success or model/provider answer cannot enable live.

Current capacity: one predetermined T1 parameter variant/day, at most 18 in one
family, on Binance research data. No independent new-strategy generator is
implemented. Hydra has a separate finite 12-variant queue; its execution and
data prerequisites are different. Neither variant count means new families.
The **proposed** next capacity is 2 distinct economic hypotheses/day, at most
6 variants each and 12 training tests/day; compare weekly and admit at most
2 qualified candidates/week to paper. These are capped planning targets, not
installed schedules or promises to generate duplicates just to meet a quota.
Model and compute budgets must be explicit before enabling paid generation.

Stages and required evidence:

1. `IDEA`: unique economic rationale, venue/product/instrument and failure rule.
   Hash the design; distinguish a new family from a parameter variation. Reject
   duplicates and keep failed ideas, not only winners. Generated code receives
   isolated tests/review, no credentials or permission to run arbitrary commands.
2. `DATA_READY`: exact Hyperliquid instruments and closed, complete, timestamped
   history. Spot and perpetual IDs/costs differ. Other-venue screening is useful
   research but cannot qualify Hyperliquid execution. Record dataset provenance.
3. `HISTORICAL_TEST`: freeze candidate/code/costs before testing. Chronological
   train/validation splits and walk-forward checks precede a single untouched
   final holdout. Log all trials, account for selection bias/multiple testing,
   reproduce from immutable inputs and retain stricter family-specific gates
   (T1 requires annual history). A missing history or sample stays BLOCKED.
   Include adverse spread/slippage, latency, partial/rejected fills and both-leg
   fees; perps additionally need funding events, margin and liquidation. Require
   positive net and stressed results, predeclared loss/drawdown/sample thresholds
   and comparisons to cash, buy-and-hold and a simple baseline at equal risk.
4. `FORWARD_PAPER`: separate fresh epoch after freeze, same code and parameters,
   at least 30 calendar days AND the preregistered independent sample requirement.
   Thirty days alone never suffice. Changing strategy parameters restarts the
   epoch. Record all equity, including flat days, simulated fills and settlement;
   test stale data, restart, disconnection, reconciliation and stop/cancel behavior.
   Legacy paper balances or funding snapshots cannot be relabeled as qualification.
5. `ELIGIBLE_FOR_CANARY`: all prior gates plus reviewed signing/execution,
   exact account, real fees, approved capital/loss/drawdown/leverage mandate,
   portfolio exposure/correlation and tested reconciliation. Compare candidates
   only within comparable venue/product/window/cost/risk cohorts; raw net profit
   or annualized Sharpe from a short sample is not a winner criterion.
6. `CAPPED_LIVE` then `LIVE`: intended automatic admission inside the configured
   owner mandate, starting with a small capped allocation and scaling only after
   fresh evidence. No repeated owner approval per candidate is required once that
   concrete mandate and executor exist. Today they do not exist in this lifecycle.
   Runtime breaches pause new risk and trigger reconciliation; risk-reducing exit
   handling must be independently implemented/tested, not assumed from this rule.

If no candidate qualifies, keep cash and keep testing; never lower a gate to fill
a deployment slot. Account/capital/day-loss/drawdown/perp-leverage values remain
unset. Research assumptions in an old backtest do not supply those values.

Reporting: count catalog entries, families, variants, completed/rejected/blocked
screens and qualified stages separately. Query both state roots; omitted,
missing, inaccessible or corrupt data gives UNKNOWN/PARTIAL and nonzero CLI
status, not zero activity or a healthy complete report. Independent readable
sources remain visible when another fails. The exporter does not query legacy
paper workers, systemd health or exchange positions: audit those separately.
Its zero qualified counts describe missing qualification adapters, not a proof
that no old paper process or direct trading path exists on the host.

Do not use legacy T_HL funding paper as qualification: inspection on 2026-09-30
found no funding event deduplication/time eligibility, missing open price PnL,
and an opening counted as a roundtrip. Quarantine its update job 5e6174a0a370
and state-based report 290f1ca4b209, preserving definitions and historical data.
Their new state must be verified separately; this document is not evidence of
an executed pause. Repair inside Sniper with a fresh epoch before resuming.

Observed on Beroun 2026-09-30: a legacy Grid paper tick runs every five minutes
outside this qualification pipeline. T13/T14 legacy state is stale, T15 paused.
The old six-hour registry service omits `--candle-state-dir` and runs as wwwenda,
which cannot read the beroun-owned candle state. Updating source makes that
coverage gap explicit; a separate least-privilege reporting bridge is still
required for a complete unattended cross-owner report. Do not broaden private
state permissions or run an unrestricted root reporting service to hide the gap.

Hyperliquid API references (checked 2026-09-30):
- [Instrument identity and account queries](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint)
- [Fees](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees)
- [Funding](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding)

## T1 candle research continuation (2026-09-30)

`candle_research.py` adds an executable route for the existing T1 BTCUSDT spot
hypothesis on complete `binance_klines` data. It does not substitute candles for
Hydra ticks, or implement the supplied discretionary OMNI strategy descriptions.
The original Hydra queue below remains available but unqualified.

The daily `sniper-research` unit now targets this candle runner. This source
document describes the candidate configuration; dated deployment evidence must
confirm whether the unit was installed. It runs as the existing `beroun` user,
reads public market data in a PostgreSQL READ ONLY transaction over a Unix
socket, and has no internet socket capability or order API. State belongs in
`/var/lib/sniper/candle-research`; the root-owned policy belongs in
`/etc/sniper/candle-research-policy.json`. Existing Hydra state is preserved.

The policy uses explicit hypothetical 1000 USDT capital, 10 bp fee and 8 bp
adverse slippage on EACH side, maximum 20% spot allocation and 1% modeled risk.
These are stress assumptions, not verified account costs, executable liquidity
or approved real capital. No leverage, rebates or invented funding is applied.
The causal T1 signal is retained from the legacy implementation. Entry is next
open, trailing levels are known before the next bar, stops account for gaps,
and final inventory is liquidated with costs. Candle ordering and slippage are
unverified, so results never qualify paper or live operation.

One fixed baseline (240-minute breakout, median range filter, 2x trailing range)
is registered BEFORE fetching/evaluating its data. Training is Aug 26Ă˘â‚¬â€śSep 23
(first two days are warmup); its single historical holdout is Sep 23Ă˘â‚¬â€śSep 30 UTC.
The other 17 predetermined variants receive only the frozen training window,
one per UTC day. This is a finite exploratory family, not 18 independent
strategies or an endless AI search. Exhaustion is explicit. Further families,
future holdouts or policy migration require a new recorded research design;
the historical holdout is never recycled for daily selection. The legacy
annual-data requirement is not relaxed by a 35-day screen.

Immutable data, policy and model bytes accompany each result. A STARTED intent
survives interruption; it blocks automatic continuation until reconciliation.
Failed baseline attempts stay visible; a subsequent day cannot bypass them.
Same-day repetition returns the prior result without another proposal or test.
State is private cooperative operational evidence, not a tamper-proof security
boundary against the service account or root. Do not publish the runtime state.

```sh
python3 -B platform/research/candle_research.py status --state-dir /var/lib/sniper/candle-research
python3 -B platform/research/candle_research.py reproduce --state-dir /var/lib/sniper/candle-research --day 2026-09-30
python3 -B platform/scripts/sync_strategy_registry.py --state-dir /var/lib/sniper/research --candle-state-dir /var/lib/sniper/candle-research
```

Use the service identity or an authorized operator for private state reads.
Reproduction requires the recorded model revision; changed inputs are refused.
It makes no exchange/DB call. A completed screen is a successful software run,
including when its net result is negative. `BASELINE_SCREEN_COMPLETE` and
`TRAINING_SCREEN_COMPLETE` are not qualification passes.

Recover by stopping only `sniper-research.timer`, preserving the state and
inspecting the recorded intent, result and input hashes. Do not erase failed
runs or change the pinned policy to make them pass. Keep existing releases and
unit/config backups. No universal ai-run/undo harness is installed by this work.

`strategy_lifecycle.py` implements an offline, daily, bounded parameter proposal
queue and invokes the existing Hydra walk-forward simulator. Twelve distinct
parameter hypotheses are available; one is proposed per UTC day. These are
parameter variants, not twelve independently invented strategies. Exhaustion is
reported explicitly. No paid AI session, shell command from a proposal, or remote
Git publication occurs. Arbitrary AI-generated executable strategies are not
automatically trusted or run.

Historical Hermes inventory on 2026-09-29 had five blocked_config jobs, P014-B
hourly ok and a pending verdict. Rechecked 2026-09-30: six report last_status=ok
and one verdict is pending. These job statuses do not prove paper profitability
or generation. No regular new-hypothesis job was identified. The research timer
was installed for T1 on 2026-09-30; it does not schedule the Hydra queue below.

## Inputs and execution

Provision a protected `/var/lib/sniper/research` and supply three reviewed inputs:

- `market.sqlite3`: candles_1m and ticks with instrument identity, unique trade
  IDs and chronological data, matching architect/counterfactual_backtest.py.
- `split.json`: is_start_ms, is_end_ms, oos_start_ms, oos_end_ms (non-overlapping,
  past intervals). Do not reuse OOS data for iterative parameter selection.
- `cost-policy.json`: exactly maker_fee (nonnegative fractional cost),
  initial_capital (positive hypothetical capital), and cost_basis (provenance).
  No account fee, rebate or live capital is silently assumed.

```sh
python3 -B platform/research/strategy_lifecycle.py propose --state-dir /var/lib/sniper/research
python3 -B platform/research/strategy_lifecycle.py evaluate --state-dir /var/lib/sniper/research --data /var/lib/sniper/research/market.sqlite3 --split /var/lib/sniper/research/split.json --policy /var/lib/sniper/research/cost-policy.json
python3 -B platform/research/strategy_lifecycle.py status --state-dir /var/lib/sniper/research
python3 -B platform/scripts/sync_strategy_registry.py --state-dir /var/lib/sniper/research
```

SQLite transactions serialize workers. Each evaluation uses a private consistent
SQLite backup and pins split, policy and simulator fingerprints. Parquet is
explicitly excluded so it cannot silently add changing data to that snapshot.
Records remain local and contain model results, not secrets or prompts.
Exact SQLite snapshot, split, policy and simulator inputs are retained under
evidence-inputs for reproduction, with content-addressed names. Do not publish
this runtime directory. Failed configuration attempts can be explicitly
requeued with `retry-blocked --proposal-id ID --state-dir PATH`; the prior
failure is archived transactionally, with a maximum of three retries. Rejected
research tests cannot be silently retried against the same holdout for tuning.
Infrastructure/configuration failure returns BLOCKED/exit 2; a completed rejected
research result remains visible and is not turned into a successful strategy.

## What this does not certify

The current Hydra model is uncalibrated and always carries an execution-model
qualification blocker. T12Ă˘â‚¬â€śT16, P019 and other Rust bots have no verified common
promotion adapter. `RESEARCH_PASS` is never permission to go live. There is no
paper qualification or production-order adapter in this research queue; the
zero qualified counts are deliberate capability limits, not measured test passes.

The previous exporter that manufactured T15 PASS and pushed another repository
is retired. Its compatibility entrypoint now reads only this registry. Previously
published documents are historical and need a separately authorized correction;
running this entrypoint does not modify them.

Legacy SafeBoot now runs the real existing backtest and fails closed; missing
parameters, data, unsupported simulators and errors cannot pass. Paper startup
requires a known existing risk IPC layout, sets pause first and never reuses LIVE
or spawns a delayed live transition. The generic mode setter refuses LIVE until
a reviewed execution adapter and explicit owner account/risk policy exist.
Other direct Rust/CLI control paths have not been globally qualified or removed;
the unchanged read-only production gateway remains the current order boundary.

Required before funded operation: actual venue/account, capital allocation,
daily loss and drawdown limits; independently reproducible strategy-specific
backtest, realistic fees/slippage, fresh complete data, separate paper epoch,
exchange reconciliation and tested stop/cancel behavior. These are unresolved,
not implemented by relabeling research metrics.
# Operational reporting bridge (2026-09-30)

The dedicated `beroun` producer reads private T1 SQLite state without changing
it, performs a repeatable-read/read-only market audit and publishes only bounded
public observations in `/var/lib/sniper/observations/candle.json`. The private
research/input directory keeps mode 0700. The `wwwenda` reporter reads its own
Hydra database and rejects observations older than ten minutes, future timestamps,
invalid schemas and mismatched digests. Atomic replacement prevents partial JSON.
The digest is an integrity check; root-owned code and producer permissions remain
the trust boundary. The public projection is never qualification authority.

The new observation timer refreshes every five minutes. The existing six-hour
report gets only the narrowly owned `95-sniper-observation.conf` override.
Installation previews pin both prior reporting file hashes, require a verified
active release, preserve prior files/state and retain operation intent/outcome.
The first real observation and report must succeed before enabling the timer.
Failed installation preserves evidence and disables its new timer for explicit
reconciliation. Source deployment and unit installation are distinct operations.

## One application worker identity
The worker identity is the existing nonsuperuser Linux/PostgreSQL `beroun` role.
The reviewed migration consolidates ingest, retention, registry, daily reporting
and the disabled T15 definition. Exact private Hydra bytes are backed up and
transferred with mode 0700/0600; only pinned files are changed. Home directories,
server WIP and root-owned prior unit files remain intact. Shared worker identity
is a convenience boundary, not protection between untrusted trading strategies.
The independent guardian/watchdog keeps `beroun-kernel`; administrative SSH and
Hermes keep `wwwenda`, PostgreSQL keeps `postgres`, privileged backup maintenance
keeps its required system identity. These services cannot submit trading orders.
DB helpers use the caller's peer role without sudo, prompt or silent failure.
Daily reporting uses the actual read-only registry/audit and starts no paid model
or external messaging. Timer states are preserved; disabled T15 is not started.
A failed migration keeps exact private/configuration backups and pauses only its
scoped timers for explicit recovery rather than adopting or deleting unknown work.

## Public Hyperliquid research archive
`hyperliquid_history.py` stores separate 1m/1h series for dynamically resolved
BTC/ETH/SOL/HYPE perps and UBTC/HYPE spot against USDC. The private archive is
owned by the common beroun worker account. Raw responses, metadata versions,
request bounds, full funding event IDs and closed numeric rows are retained.
Rows are append-only; conflicts fail without rewriting history. Instrument/token
identity changes require a distinct data epoch. Read-only reproduction checks
retained response hashes and every normalized row against the archive.
The first capture requests 4900 minute bars and 120 days of hourly bars. Missing
rows remain explicit; the 5000-bar API bound cannot qualify annual-minute T1.
Funding history is a public rate series, not account settlement or realized PnL.
Request-weight reservations survive restarts (500/minute for this client); this
is not an IP-wide guarantee for unrelated clients. HTTP failures/rate limits
remain failures. Response/pages/storage are bounded; retention needs review.
The absent-unit installer preserves failed capture evidence and enables the
five-minute timer only after actual six-market/two-interval capture and replay.
Reporting publishes only coverage/counts, keeping raw archive inputs private.
None of these data checks constitutes historical/paper/live qualification.
