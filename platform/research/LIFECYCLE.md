# Evidence-driven research — implementation boundary

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
