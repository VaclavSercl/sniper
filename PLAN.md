# Backtest integrity repair

## Canonical repository consolidation (owner instruction 2026-09-29)
Sniper is the only trading source repository; Beroun is its deployment host.
Integrate reviewed application sources from the corrected Beroun checkpoint plus
deployed sources not represented there, without importing another repository's
agent instructions, generic harness, secrets, runtime state or Git directory.
Preserve Sniper's existing Rust and Python architecture and all user changes.

Files: new platform/ application tree (gateway, ingestion, risk, research,
scripts, DB definitions and application tests), source provenance manifest;
infra/beroun deployment mapping and reversible migration tooling; narrow README,
ARCHITECTURE and INSTALL additions. Exact paths follow discovery manifest before
copy. Existing test fixtures are ported explicitly; generic harness is not installed.

Acceptance: one GitHub source authority VaclavSercl/sniper, preserved source
provenance, no omitted deployed trading entrypoint, existing backtest regressions
and relocated application tests pass in a private DB fixture; syntax/diff/secret
checks; service mapping verifies every source path. Runtime state/DB names remain
compatible. No funded execution, automatic LIVE promotion or new T15 epoch.

Publication: owner's request includes fixing GitHub in Sniper; use the existing
repair/backtest-integrity branch and a reviewable PR against main. No force push,
repository deletion, history rewrite or merge of unverified changes. The prior
two-repository push plan is superseded. Preserve old Beroun repository history.

Deployment: prepare exact reversible source-path changes only after tests. Any
required sudo invocation must have concrete scoped approval under A.5; old
read-only audit consent cannot authorize service mutation. Preserve old files,
active modes and DB identities. Missing privileges block only cutover. Do not
claim deployed until service execution paths and health are verified.

Validation: relocated source tree contains 119 reviewed source/test files.
Linux validation passed 34 backtest/cutover regressions and 76 relocated
application tests, with actual private PostgreSQL integration and no skips.
Root Rust binaries are unchanged; no Rust build or live exchange execution.
Cutover apply/rollback were exercised only in temporary directories with mocked
systemd. Windows lacks unprivileged symlinks, so the two filesystem cutover tests
require Linux; no Windows deployment compatibility is claimed.
Actual read-only host preview resolved 18 unit path overrides. Owner selected
option 1, explicitly authorizing scoped sudo cutover, preserving originals and
pausing the old T15 timer. No approval to activate a funded bot is inferred.
Before checkpoint/publication, rerun exact candidate checks after documentation
and manifest finalization. Record actual server result externally after deploy.

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
