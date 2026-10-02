# Evidence-driven research Ă˘â‚¬â€ť implementation boundary

## Current owner scope and lifecycle policy (2026-09-30)

The owner selected **Hyperliquid, spot and perpetuals**, and wants automatic
selection after historical AND independent forward paper evidence. Canonical
structured rules are in `lifecycle_policy.json`; `lifecycle_overview.py` validates
them and the read-only registry exporter reports coverage and stage counts.
This includes bounded offline screens and reporting, **not a promotion executor**. Existing
live refusal stays in force until the required execution and risk integrations
exist. A JSON field, timer success or model/provider answer cannot enable live.

T1 retains one predetermined parameter variant/day, at most 18 in one family,
on Binance research data. Hydra has a separate finite 12-variant queue with
different data prerequisites. These variants are not new economic families.
The new bounded offline implementation registers up to two unseen economic
blueprints/day, six market variants each and twelve primary test bundles/day.
It has a finite library of six blueprints and records all blocked/rejected
results. Installed schedules and actual counts require runtime observations;
the policy status alone cannot prove them. Weekly cohort comparison is implemented.
At most two qualified paper admissions/week remains a target; the qualification/
admission executor is not supplied by exploratory screens. Paid generation needs
an explicit provider budget. Exhaustion must be reported without duplicate filling.

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
   concrete mandate and executor exist. The owner mandate is now configured;
   the funded executor and qualification/admission integrations remain unavailable.
   Runtime breaches pause new risk and trigger reconciliation; risk-reducing exit
   handling must be independently implemented/tested, not assumed from this rule.

If no candidate qualifies, keep cash and keep testing; never lower a gate to fill
a deployment slot. On October 1 the owner set a 90% main-account capital ceiling
and delegated other risk choices to AI. The protected runtime mandate supplies
the conservative initial canary limits; public JSON fields stay unset because
they are not the private account authority. Research assumptions do not supply
or override these values. The report distinguishes configured policy from
unimplemented funded loss/exposure enforcement.

`/etc/sniper/hyperliquid-mandate.json` is root:beroun 0640 and bound to the current
protected public account identity. Initial AI limits are one strategy, <=20% of
the deployable budget per strategy, <=25% of that allocation per order, daily
loss <=1% of budget, drawdown <=3% and perpetual leverage <=1x. Fresh USDC spot
and perpetual equity determine the 90% ceiling without double counting; held or
nonwithdrawable funds reduce deployable capital. Positive other assets require
verified valuation. This calculator is read-only policy evidence and cannot
activate trading, relax guardian mode or replace 30 real-day paper qualification.

Reporting: count catalog entries, families, variants, completed/rejected/blocked
screens and qualified stages separately. Query both state roots; omitted,
missing, inaccessible or corrupt data gives UNKNOWN/PARTIAL and nonzero CLI
status, not zero activity or a healthy complete report. Independent readable
sources remain visible when another fails. The exporter does not query legacy
paper workers or systemd health. It reads a fresh minimal actual-account
observation rather than using an agent wallet's balance or signing credentials.
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
The old six-hour registry service originally omitted `--candle-state-dir` and
ran as wwwenda, which could not read beroun-owned candle state. A verified minimal
observation bridge now supplies that input; the application service and private
Hydra state have also been consolidated under beroun. Administrative, database
and independent guardian roles remain separate. Private state is not public.

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

## Actual Hyperliquid account observation
The existing signing material remains in its protected administrative location.
The official SDK 0.24.0 is hash-locked in a separate operations venv; its offline
signature recovery and nonce/network/account/action mutation checks are separate
from real exchange execution. This is not a validated funded order adapter.
Read-only service `sniper-hyperliquid-account` runs under `beroun`, using only
root-owned public identity configuration. On every observation it revalidates
the agent-to-account relationship and queries the actual account's spot/perp
state, open orders and fees. It cannot sign orders, change leverage or transfer
funds. Available balances do not create a capital/risk mandate. Agent expiry,
testnet order execution and live execution remain unverified/disabled.
The installer pins the private account-identity evidence, copies only public
addresses/role/provenance and enables its thirty-minute timer after a real
account observation. Unknown prior configuration is never overwritten. Stale,
revoked/reassigned agents, malformed numbers and network errors remain visible.

## Bounded economic research cycle
`research_cycle.py` registers at most two unseen economic blueprints per real UTC
day and six market variants for each. The current finite offline grammar has six
blueprints; it does not invent an unlimited stream or call paid models. An
exhausted library reports `NEED_NEW_BLUEPRINTS_OR_PROVIDER_BUDGET`. Market and
parameter variants are counted separately from economic hypotheses. The daily
03:15 UTC timer evaluates at most twelve primary variant bundles; Sunday 03:45
UTC records a comparison within identical market/product/window/cost/risk cohorts.
Timer installation and observed throughput are reported separately.

The entire finite grammar and 72 planned market/cost trials are preregistered,
along with fixed parameters, 120-day closed hourly input, chronological 60/20/20
partitions, sample thresholds and failure rules. A private immutable compressed
epoch retains exact metadata, normalized inputs, original response references,
actual fee observation and source fingerprints. Original response reconstruction
must match every input row before freezing. Unknown or modified state is refused.
Training failure stops before validation/final holdout. Holdout consumption is
durable before evaluation; crashes cannot provide another look at unseen data.
Completed metrics can be reproduced read-only with the original source revision.

Spot screens assume long-only next-open fills, 25% of a hypothetical 1000-unit
research balance, conservative fees/slippage and doubled-cost stress. Signals
use prior closed bars. Full hourly closing equity and flat days, terminal costs,
same-cohort cash/buy-hold/simple baselines, minimum trades and planned-trial
Bonferroni sign-test checks are retained. This sign test assumes independent
daily signs; serial dependence and execution calibration remain unresolved.
Hourly closes are not tick-level or intrahour drawdown evidence. The fee model
and market multiplier are explicit research assumptions, not account allocations.
Perpetual variants remain blocked for absent mark/index/basis, margin/liquidation
and leverage evidence even though public historical funding rates are available.

An exploratory positive result remains unqualified. Historical qualification,
forward paper, calibrated fills/recovery and funded promotion are separate gates;
the thirty actual calendar-day requirement cannot be supplied by these tests.
Both scheduler services use the common beroun account and make no network or
exchange writes. The installer starts a real cycle, reproduces every completed
spot screen and records a comparison before enabling its two new timers. Failed
installation preserves all state and consumed holdouts for explicit recovery.
Recovery can only reuse exact known unit files from a failed install while the
research state is still absent and both timers are inactive. It pins the active
new release, rejects overrides/ownership/hash changes, records a linked repair
intent and carries a cumulative three-repair budget. Existing research epochs
are never adopted by this installation recovery path. The request limiter waits
until the full weighted deficit expires, preserving its 500/minute ceiling.
# October 1 execution dependency update

Testnet spot admission now requires a durable portfolio-risk reservation and
fresh actual-account/fill reconciliation. Mainnet transport, qualified paper
admission and automatic funded promotion remain unavailable. See
[RISK_EXECUTION.md](../../infra/beroun/RISK_EXECUTION.md). Preserve the frozen
research epoch and four pinned modules; source tests do not promote any of the
current rejected/blocked candidates. Thirty real paper days begin only after
historical qualification and candidate freeze, not after source deployment.

## Independent accounting audit while testnet is unavailable

The owner permits research to continue without a successful testnet faucet.
Actual signed testnet execution remains a prerequisite for funded activation.
The canonical read-only `sync_strategy_registry.py` report now includes
`research_accounting`: a separate cash/inventory reference ledger checks every
stored completed spot phase's normal/stressed results and cash, buy-hold and
simple-MA benchmark accounting. It does not call the simulator or signal model.
It checks pinned next-open price assumptions, entry budget, complete inventory
exits, fees, chronological hourly equity including flat days, terminal costs,
closing drawdown, daily returns and the exact sign-test tail. Cash cannot trade;
buy-hold must use its pinned entry/terminal interval.

The auditor uses a bounded read-only SQLite transaction and validates original
epoch/model identity, attempt/phase identity and durable holdout consumption.
A digest of the complete public stage projection binds the audit to the report;
concurrent changes cannot silently attest another reported snapshot. Missing,
corrupt, interrupted or inconsistent evidence makes integration health PARTIAL
and the CLI nonzero. Product-blocked perpetuals are explicitly NOT_APPLICABLE.
No completed accounting evidence remains BLOCKED, rather than an empty PASS.

Accounting PASS is limited to stored assumed-fill arithmetic. It does not verify
the signals independently, calibrate realistic fills, prove a return advantage,
resolve serial dependence or qualify historical/paper/live trading. No new trial
or unused holdout evaluation occurs; frozen research bytes and state remain
unchanged. A qualifying forward-paper epoch still needs historical admission
and at least thirty real calendar days. Runtime/accounting audit evidence must
be recorded separately from source tests and service health.
# Measured-book continuation (October 1, 2026)

The `spot-book-forward-v1` campaign is separate from the frozen six-blueprint
campaign. `forward_worker.py` records actual Hyperliquid MAINNET public L2 books
for the metadata-resolved HYPE/USDC and UBTC/USDC pairs every ten seconds. It
never signs or sends an exchange order and cannot read the account/key paths
hidden by its service. State is private to `beroun` at
`/var/lib/sniper/forward-research-v1`; the existing risk guardian stays separate.

`execution_evidence.py` models spot IOC orders with exact venue precision,
minimum notional, protected limits, one-second delay, five-percent displayed-depth
participation, cancellation of unfilled size and base/quote fee accounting.
Stress doubles latency/fees and halves available depth. Missing future books,
stale/gapped books or insufficient balance never produce hypothetical fills.
REST receipt timing is an observation proxy, **not actual execution calibration**.
Only linked, reconciled owned real MAINNET order/fill evidence can calibrate this
model; testnet and unsupported fee assets cannot substitute for mainnet evidence.
The optional import is a root-owned, non-world-readable journal at
`/var/lib/sniper/execution-calibration/mainnet.json`, not a PASS certificate.
Absent evidence blocks qualification while public collection continues.

The generator enumerates 336 declared economic interactions of four causal price
triggers with zero, one, two or three non-contradictory volume/trend/volatility/session
filters. At most two new mechanisms and up to twelve market/parameter variants are
registered per UTC day. There are 1,848 planned primary trials, at most twelve/day.
Lookback windows (1/6/24 hours, or 6/24 for pullbacks), holding parameters, wording and market variants do not create new economic identities.
This is a deterministic generator, **not an LLM integration or unlimited novelty**.
Exhaustion is explicit and requires a separately reviewed new grammar/campaign;
it does not recycle a failed holdout. Weekly comparisons preserve equal-market,
equal-frozen-input cohorts and remain diagnostic until qualification.

Continuous capture/paper runs in `sniper-forward-research.service`; generation
and potentially long historical tests run separately in `sniper-forward-screen.service`
every fifteen minutes through its timer. Daily quotas are enforced independently
of timer frequency. SQLite WAL and separate process locks allow continued collection
during tests; this is tested local process coordination, not cross-host locking.

Immediate preliminary screens use only the old frozen training/validation data
(first eighty percent) with clearly labelled OHLC cost assumptions. No old final
holdout is evaluated by this continuation. New exact book-based historical windows
start at the next UTC midnight **after each candidate is preregistered** and need
120 days of complete prospective coverage. Their train/validation/final split is
50/10/40 percent; final holdouts are consumed once and durably before evaluation.
Normal and stressed results must pass costs, same-risk cash/buy-hold/MA benchmarks,
sample and risk limits. No new entry is allowed in the terminal holding window;
residual terminal inventory is written off rather than assumed liquidatable for
free. Observed intra-hour price extrema are assessed after the decision, so
future adverse prices cannot suppress an earlier signal. Non-overlapping three-day sign blocks and a correction for
all 1,848 planned trials are conservative selection checks, not proof of independent
returns or future profit. A second Decimal reference ledger checks execution,
accounting, chronological equity and metrics without calling the production fill
or accounting functions. Shared DSL source is pinned; independent strategy
authorship/model review is not claimed.

Only historical candidates passing all gates enter forward paper, at most two
admissions per ISO week, ranked by stressed final-holdout result. Paper uses the
same frozen signal, execution and risk fractions with **hypothetical 1,000 USDC**
per experiment. This never allocates the actual account balance. Marks describe
closed hourly periods and retain actual observation timestamps and source-book
digests; partial admission days are not full qualification days. Thirty real days,
complete equity including flat periods, normal/stress performance and fresh
calibration are mandatory. Durable intents deduplicate across restart; feed gaps
invalidate an epoch and cancel unresolved paper orders. Fault tests on isolated
clones test stop/cancel, disconnect invalidation and reopening without rewinding
the production paper journal. These tests are not exchange execution evidence.

Use `python3 -B /opt/sniper/current/platform/research/forward_worker.py status`
as the application user, or the existing canonical registry report, to inspect
actual counts and blockers. `once` performs one real public capture/research cycle;
`run` is the continuous capture/paper worker; `research-once` is the separate
bounded generator/test pass. Missing/stale state makes canonical
integration health PARTIAL. Running collection, positive diagnostics or elapsed
wall time alone never grants funded promotion. Perpetual qualification remains
blocked by missing margin/mark/funding mechanics; funded transport, actual testnet
verification, guardian/risk/capacity gates are separate prerequisites.

## Operation and proposal additions (October 1)

`forward_operations.py` inspects actual archive/feed/storage state, projects the
storage requirement to the last registered window, and keeps a deduplicated
local alert outbox. Local recording is not external notification delivery.
Each research pass performs an online daily SQLite backup and an actual restore
into an absent private probe directory. Committed WAL contents, integrity,
campaign/source identities and table counts are checked. Production restore is
never automatic; existing targets and changed/corrupt backups are rejected.
Backups share an explicit 24 GiB total budget, including temporary restore space.
Keep two verified databases. Remove only older owned databases after verifying
their hashes, retaining immutable intent/result and pruning evidence. Unknown,
modified, incomplete or interrupted attempts block new backups and are preserved
for explicit operator reconciliation; they are never retried automatically.
The restore probe is deleted only after its actual restored hash/state matches;
its proof remains in the backup result. Exhaustion is visible. This backup covers the forward
SQLite epoch, not PostgreSQL, the separate candle archive or the whole server.
Only newly created backup/restore copies are finalized as standalone DELETE
journals with integrity/settings/count comparisons. The live source remains WAL;
no unknown WAL/SHM files are manually removed to obtain successful verification.

`strategy_proposals.py` balances economic trigger families within the existing
336-mechanism/1,848-variant grammar and the SAME database-wide daily quota.
Existing candidates, their model/source fingerprints and consumed holdouts are
unchanged. Future candidates record proposal version/source/rationale provenance.
External AI proposals can be validated as bounded data-only DSL; a paid or
scheduled LLM generator is not configured by this change. No output executes as
Python or changes risk thresholds, holding protocol, data windows or trial budget.

The guarded mainnet library is distinct from the public worker. Preparing an
order commits the nonce/intent/risk reserve without signing or sending it.
An exact short-lived root-owned operation permit is required for dispatch. The
permit issuer recomputes historical/paper/calibration evidence and requires
independent fresh guardian and actual testnet evidence. These prerequisites
remain unavailable in the current runtime. No funded daemon, credential reader
or calibration pilot is installed; offline adapter tests are not actual fills.
Independent continuous guardian implementation and real transport/recovery
evidence remain blockers before any automated funded activation.

Research health is RUNNING until maintenance finishes; external status never
accepts this interim state. A combined once failure marks the actual failed stage.
The current root mandate is reloaded before signing and immediately before send;
UNAVAILABLE forbids entries. Venue expiry cannot exceed the five-second permit.
Owned exits require complete reconciled fills and independent reduction permits,
including under a latched halt, and do not consume new-entry lifetime caps.
Expired PREPARED operations with no dispatch event may be explicitly reconciled
as unsent after stopped-writer and complete account proof. Unknown sends remain
blocked; no operation is replayed. These are offline-tested capabilities, with
no funded daemon, production permits, actual orders or profit qualification.
