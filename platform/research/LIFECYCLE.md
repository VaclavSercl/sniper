# Evidence-driven research — implementation boundary

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
is registered BEFORE fetching/evaluating its data. Training is Aug 26–Sep 23
(first two days are warmup); its single historical holdout is Sep 23–Sep 30 UTC.
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

The seven current Hermes schedules were inventoried on 2026-09-29: five report
blocked_config, P014-B hourly reports ok, and one verdict is pending. None is a
regular proposal job. The supplied systemd timer is a reviewed deployment
candidate, not evidence that it has been installed. Preserve existing research
state until the P014-B and Hyperliquid dependencies have been reconciled.

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
qualification blocker. T12–T16, P019 and other Rust bots have no verified common
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
