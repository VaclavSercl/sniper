# Backtest integrity repair

## Goal and boundary
Repair counterfactual validation on baseline c7c9870df8e0bab1d77747b5b30b64075fa690cb, isolated branch repair/backtest-integrity. Owner approved sequential Beroun/T15 and Sniper repairs. No real orders, deployment, dependency installation or remote publication. No harness installation.

## Environment and policy
Windows Python 3.12, standard-library tests. Existing pre-change workflow and CHECKLIST reviewed. No configured Python CI gate found. Rust engine is outside this change. Existing queue model is uncalibrated and cannot qualify live trading; historical statistics must disclose that limitation.

## Impact / file checklist
architect/counterfactual_backtest.py: signed fees, chronological drawdown, causal quotes, bounded fills, explicit evidence limitations, read-only data loading with instrument identity, unique trade IDs and half-open windows.
architect/tests/test_backtest_integrity.py: isolated SQLite and deterministic simulator regressions; no network or production database.
PLAN.md: evidence and remaining blockers.

## Acceptance criteria
No false PASS from sorted profit values, assumed rebates, unvalidated execution or insufficient daily return evidence. No silent malformed-source fallback. Preserve simultaneous distinct trades. Reject conflicting duplicates and overlapping walk-forward partitions. Import has no filesystem side effects. Legacy call signature retained.

## Verification
python -B -m unittest discover -s architect/tests -p test_backtest_integrity.py -v
git diff --check
Static Python compilation without imports. Review changes and test boundary conditions. No claim of historical profitability from synthetic fixtures.

## Failure and recovery
Fail closed for ambiguous/missing identity, missing parquet reader when parquet files exist, malformed data, unsupported bot/partition or unavailable fee/capital evidence. At most three evidence-driven repair cycles; preserve diagnostic outputs. Revert only owned local edits if needed. Deployment blocked until actual data, fee schedule, execution model and complete project validation are available.

## Status
25 regression tests PASS on Windows Python 3.12.10 and Linux Python 3.14.4.
Source compiled without imports; git diff --check PASS. Initial regression run
had 8 failures and 7 errors (unconditional missing pandas); the first repair
passed all initial 15 tests. Ten additional data/simulation tests also pass.
Evidence and tested source hashes: ../evidence/sniper-validation.json.

Self-review: fees now require an explicit assumption; full parameters are retained.
Quotes use previous observations, event-level equity retains intraminute losses,
fills respect aggressor side, observed volume and position capacity. Daily Sharpe
uses positive explicit initial capital, consecutive observed calendar days and
excludes the last partial day; unavailable estimates carry a failure reason.
Queue/latency/slippage and candle ordering remain unvalidated; all generated
results retain UNVERIFIED_EXECUTION_MODEL. Parquet filtering uses fixture records,
not a live parquet dataset. Real historical profitability is not measured.

Owner subsequently authorized conditional software checkpoint/publication.
Beroun's isolation repair now passes its existing gate; actual strategy data and
execution evidence remain insufficient. Prepare a software-only checkpoint,
with explicit publication destination, without enabling funded trading.
No claim of Rust engine, macOS/WSL or agent adapter testing.
