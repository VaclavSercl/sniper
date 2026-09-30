# Backtest integrity repair

## Evidence-driven repair 1 - weighted request expiry and interrupted installation
Baseline ca21c03923d7e06b4ea60e1259f469097ed3bd8d, 266 Linux tests passed,
source published/deployed. First actual research installation failed before
research execution because data capture's reservation raised RuntimeError.
The four new research units are preserved and both research timers are disabled;
do not retry the absent-target installer or adopt any research state blindly.
Actual request log: metadata weights 20+20 and four candles of 104 fill 456 of
500. The next 104 needs 60 weight released; expiring one small oldest request
per retry exhausts three attempts before a sufficient reservation is considered.
Compute the earliest weighted expiry that releases the whole deficit, then
recheck atomically. Preserve the 500/minute ceiling, durable reservations and
bounded attempts; add a realistic mixed-weight regression and explicit failure
category. Pause only the new data timer during this repair, preserving its
enabled state/config and all source/rows/failed-run evidence. Resume it after a
new actual complete capture and replay, not from a test-only claim.
Add a reviewed recovery path for the failed research installation: exact prior
intent/failure, all four unit hashes/ownership, active verified new release,
both timers inactive and research state still absent are mandatory. Never
overwrite the old failure or adopt an interrupted/frozen research epoch. Record
a linked recovery operation, execute/reproduce a real first cycle and compare,
then enable only those two known timers. Failure preserves evidence and pauses
them again. This is the first repair cycle for that failed production gate;
further attempts retain its cumulative three-cycle limit. Full exact Linux gate,
source checkpoint/push/deploy and actual recovery checks remain required.

## Audit step 3 - bounded research cycle, preregistration and causal screens
Baseline b40b157a48ed18c0d6376dd97c71f260e9f9c4e1, clean owned worktree. Actual
read-only account timer runs as beroun, no key copied and no live orders. Data
archive and account observation are installed and verified. Preserve both.
Implement a finite offline grammar of six economic blueprints, separately from
six market variants per blueprint. Register at most two unseen blueprints and
twelve variant test bundles per real UTC day. No paid model/provider invocation,
arbitrary generated-code execution, duplicate quota filling or unlimited-novelty
claim. Once exhausted, expose NEED_NEW_BLUEPRINTS_OR_PROVIDER_BUDGET. A submit/
extension capability needs separate reviewed source, not execution of prompt text.
Preregister the whole grammar, 72 planned market/cost trials, fixed parameters,
120-day data epoch, 60/20/20 chronological partitions, sample/DD/failure rules
and research-only hypothetical capital/costs before testing any candidate.
Use a private append-only registry and frozen hourly input with provenance from
the exact venue archive. Reject gaps, future/unclosed bars, unknown schema,
changed code/policy/data, interrupted attempts and same-path input replacement.
Each final holdout is consumed once, including failed/interrupted evaluations;
never automatically retune or rerun it. Training failures stop before holdout.
All outcomes and missing evidence remain visible, not just profitable winners.
Causal signals read only prior closed bars and fill at the following observed
open under explicit conservative uncalibrated assumptions. Spot inventory cannot
borrow/short; fees/slippage, terminal close, equity and flat days are included.
Compare cash, buy/hold and a simple baseline on identical market/window/cost/
allocation; use sample/DD/stress checks and Bonferroni planned-trial correction.
Perpetual screens remain BLOCKED until the required mark/index/basis and margin/
liquidation model exists; public funding rates alone cannot substitute for them.
Positive exploratory metrics do not grant historical qualification or funded
promotion. Forward shadow diagnostics will have a separate pinned epoch and
actual causal quote/funding/event accounting; the 30 real-day paper rule and fill
calibration/recovery gates remain mandatory. No fabricated accelerated evidence.
Files: research protocol/core, durable cycle registry and tests, lifecycle status
integration/policy wording, guarded absent-unit daily/weekly schedule installer,
source manifest/documentation/PLAN. Each dependency gets a full Linux gate and
actual first execution before its timer is enabled. Preserve older negative T1,
legacy states, WIP, production SQL and all immutable releases. Recovery preserves
interrupted evidence, consumed holdouts and private state; no automatic adoption,
reset, retry of final holdout, cleanup or live activation. Final standalone source
attestations, exact nonforce SHA push and root-owned source deployment follow.

## Audit step 4 prerequisite - isolated SDK and read-only account connection
Baseline eee3299db1de1c43d54003198fed4ba7de104813. Step 2 exact Linux gate
passed 241 tests; immutable source published and deployed. First actual capture
reproduced 58200 rows, 120-day hourly histories have no internal gaps across six
markets, 2880 venue funding events per perpetual; data timer is active. The owner explicitly
requests connecting the existing Hyperliquid account. Existing signing material
must stay on Beroun; do not print, hash, copy into source, or transmit it.
Use only the installed standalone pip 26.2.1 with verified --python capability,
and an isolated no-pip venv under Sniper's private operations directory. Resolve
official hyperliquid-python-sdk==0.24.0 from PyPI with binary wheels only,
record exact resolved wheel hashes, then install the complete hash-locked set.
No global or Hermes environment change, runtime upgrade or executable download.
Keep sanitized installation evidence and actual dependency checks distinct from
a vulnerability assessment; missing assessment cannot mean a clean audit.
Verify official signing primitives with synthetic keys before using existing
material. Derive only its public address locally and query userRole to resolve
the actual master/subaccount identity; then read account balances, positions,
open orders and fee schedule. Empty agent balances are not proof of empty master
balances. No exchange mutations, orders, leverage settings, approvals or transfers.
Persist public account configuration privately outside Git only after validating
the role relationship. Key parsing, malformed roles, unsafe permissions, unknown
ownership, unavailable SDK and transport failures must remain explicit blockers.
Planned source scope: reusable account inspection, SDK requirements with exact
hashes, meaningful offline signature/account-role tests, guarded runtime install
and account-report integration; PLAN, SOURCE_MANIFEST and lifecycle documentation.
Final checks: existing full Linux gate plus isolated SDK import/signature recovery,
pip check, exact account read-only evidence; source and instructions unchanged.
No live mandate inferred from available balance. Funded execution and automatic
promotion still require implemented order guards, qualified historical/paper
evidence, testnet verification and explicit capital/loss/drawdown/leverage limits.
Recovery: preserve failed setup directories and reports; never overwrite/adopt
an unknown runtime or credential. Stop only the affected connection operation.
Observed dependency evidence: binary-only hash-locked installation and pip check
passed on Python 3.14.4; ten offline signature/domain mutation checks passed.
PyPI release advisory metadata was available for all thirty resolved packages,
with zero listed advisories; this is not a complete supply-chain assessment.
Actual userRole resolved the existing agent to a user account; read-only API
observed 6.26 spot USDC, zero perpetual equity, zero positions and open orders.
Public identity remains private on the server; no signing material was copied.
Implementation scope now includes account report service/timer, absent-target
installer, whitelisted public identity and exact companion-evidence pinning,
account/installer regressions and SDK offline verifier + platform-specific lock.
Production observation runs as beroun without a signing dependency or key.
Windows focused tests omit the Linux directory-fsync durability test explicitly;
the required full gate executes it on Beroun, without a platform skip.
Preliminary account gate passed 249 Linux tests. Host inspection found an existing
root:root 0755 /etc/sniper directory. Preserve that shared directory unchanged;
accept safe root-owned traversal permissions while refusing nonroot ownership
and group/world writes, and create only the new 0640 root:beroun identity file.
Add independent permission regression checks and rerun the complete exact gate.

## Audit step 2 — exact-venue research data (implementation scope)
Baseline 80d984d985e3e745dfbb8757f411c98c67a54abc; five application workers
actually consolidated under beroun, private registry bytes unchanged, both-source
report OBSERVED, 231 Linux tests passed, old WIP preserved. Guardian independent.
Public Hyperliquid metadata verified: BTC0 ETH1 SOL5 HYPE159; spot HYPE@107,
UBTC@142 quoted in USDC. Never freeze these numbers as order defaults. Funding
history timestamps contain real millisecond offsets (not exact hour boundaries).
Official candle endpoint offers only latest 5000 bars. Collect distinct 1m and
1h datasets in a new private append-only SQLite store, never mix hourly bars into
production minute tables or silently replace Binance/T1 data. Bootstrap 4900
closed minute bars and 120 days of closed hourly bars; subsequent bounded capture
fills missing available ranges. T1's 365-day minute requirement remains unchanged.
Hourly hypotheses will have their own preregistered history/sample rules.
Dynamic metadata must uniquely resolve exact base/quote/product/token identities.
Store immutable raw response provenance, full event timestamps, numeric validation,
conflict guards and explicit gaps. Public funding rates are venue events, not
account settlement or PnL. Persist local request-weight reservations (500/minute),
bound pages/output/storage, halt on rate-limit/conflict, report partial failures.
No credentials, orders, installs, existing-row rewrites or production schema changes.
Files: PLAN.md; platform/research/hyperliquid_history.py + focused tests; sanitized
observation/report history extension; infra/beroun data service/timer/guarded
installer + regression tests; SOURCE_MANIFEST.json and LIFECYCLE.md. Existing Python
stdlib and systemd only. Full Linux gate, actual first API capture/replay/append-only
repeat, source checkpoint/push/deploy, then install timer after actual observations.
Preserve old releases, source/WIP, all public/private history; no broad cleanup.
SDK prerequisite discovery: server Python3.14.4, venv but no ensurepip on default
PATH. Existing Hermes-managed uv and standalone Python/pip found; not yet used.
Existing Hyperliquid-specific secret stays on server; no valid signing library
available yet, so account identity has not been derived/verified. Necessary SDK
setup will be isolated and pinned under the already approved step 4 scope; no
Hermes/global dependency upgrade and no funded orders before evidence/mandate.


## Owner-requested service-account consolidation â€” design before implementation
Baseline 11bff2768920d94286f17afd8a5fa6dd54cd1ae4; observation bridge actually
installed, both-source report OBSERVED, timer active at 18:40 UTC. Primary app
identity selected: existing beroun (UID/GID1001), with existing nonsuperuser
PostgreSQL peer role and table grants. No account/password creation or renaming.
Consolidate five remaining application workers (perfect-ingest, retention,
strategy-registry, rich-report, disabled paper-T15) and exact owned Hydra state.
Remove unnecessary sudo from ingest/retention DB helpers; errors are sanitized.
The rich report will use the verified read-only registry report without starting
paid agents or sending external messages. Preserve its daily schedule. No
activation of disabled T15. Keep SSH/interactive Hermes administration wwwenda,
PostgreSQL postgres, privileged backup maintenance and independent safety
watchdog/kernel beroun-kernel outside the trading worker identity. A shared
worker UID is not a security boundary; the separate guardian prevents worker
signals/replacement of its socket and avoids weakening established safeguards.

Files: this plan; infra/beroun/install_application_identity.py, fixed reviewed
96-sniper-application-user.conf and rich-report override; platform/scripts/
db_client.py, perfect_market_ingest.py, kline_retention_partition_manager.py;
focused DB/installer tests, SOURCE_MANIFEST.json and LIFECYCLE.md. Installer
previews/pins exact unit/override manifests and private-state hashes/modes,
backs up only managed state/config in protected recovery, stops only affected
timers/writers, chowns individual verified paths (no recursive shell chown),
checks read-only peer-role access and first actual complete report, then restores
previous timer states. Failure preserves all evidence and leaves affected timers
stopped for recovery. Never touches home directories, broad account permissions,
server checkout/WIP, source releases or independent safety service ownership.
Full Linux application/PG gate and systemd verification, then exact checkpoint,
push/deployment and separately verified account migration. Later audit steps
2â€“4 remain pending; real account master identity/risk mandate still unresolved.


Step 1 implementation: public whitelisted observation producer, closed/source-
isolated read-only PostgreSQL audit, fresh-digest reporter integration, guarded
installer with prior configuration hashes and durable failure state. Preliminary
Linux gate: 58 architect + 167 platform tests passed, including actual temporary
PostgreSQL audit and Linux symlink/installation tests; Windows focused checks
passed with four explicitly unavailable symlink checks. Unit verification found
an Environment quoting warning; corrected before final gate. No live orders.
Final exact-candidate verification, checkpoint, publication and actual deployment
follow. Steps 2â€“4 remain separate dependencies, not completion claims.

## Owner-authorized implementation of audit steps 1â€“4 â€” 2026-09-30
Goal: implement the complete proposed lifecycle progressively, verifying each
dependency before proceeding. Owner explicitly said to perform all proposed
implementation/repair steps. Baseline 342b77c95a81a2f07271aa4b1455f8ca8a0c722c,
clean repair/history-recovery worktree. Preserve server checkout/WIP, all
private state, old releases and quarantined invalid paper epochs.
Required inputs still requested: actual Hyperliquid account, capital/loss/DD/
leverage mandate, paid-model budget. Do independent implementation without
those values; no fabricated defaults or funded orders. Use bounded offline
hypothesis generation until an explicit provider budget exists. A 30-day
forward evidence requirement cannot be satisfied by accelerated clock/tests.

Step 1: least-privilege public observation bridge from beroun-owned T1 state,
read-only data-quality audit, integration into existing wwwenda six-hour report.
Do not make private SQLite/input directories public or give reporting root.
New worker runs as existing beroun user, publishes only sanitized derived
observations to its separate owned directory with explicit timestamps/digests;
reports reject stale/malformed/mismatched observations. Existing report reads
its own Hydra registry directly and the derived T1 observation. Add reversible
root-owned unit/drop-in installation with exact prior hashes, intent/result,
first actual observation+report check before enabling its timer.
Step 2: exact spot/perpetual market metadata, source-specific closed candles
and actual historical funding events; append-only capture with conflict guards,
bounded requests and explicit API-history limits. Preserve existing rows.
Step 3: bounded registered new economic hypotheses (distinct from parameter
variants), train/validation/holdout-aware evaluation, fair cohort comparison,
causal forward paper with event-id funding, full equity and pinned epochs.
Historical or forward missing data remains visibly blocked. No arbitrary
generated-code execution, provider sessions or invented fill calibration.
Step 4: verified SDK/runtime capability selection, dynamic asset identity,
precision/nonces/signing/cancel/reconcile adapters; offline then testnet
verification. Actual promotion requires qualified evidence and concrete owner
mandate. Never turn absent integrations into a passing qualification flag.

Step 1 file scope: platform/research/operational_observation.py and tests;
platform/scripts/sync_strategy_registry.py observation input;
infra/beroun/sniper-observation.service/.timer and reporting drop-in;
infra/beroun/install_observation.py and installation regression tests;
platform/SOURCE_MANIFEST.json, LIFECYCLE.md, this plan. Update scope before each
later dependency. Standard library and existing installed PostgreSQL/tools.
Acceptance: no private-state disclosure, no mutation on reads, bounded fresh
observations, meaningful error categories, actual scheduled complete report
under wwwenda, source/recovery hashes, unaffected WIP and trading boundary.
Verification: focused unittest discovery; full Linux tools/verify_platform.py
with private PostgreSQL; shell syntax/systemd-analyze verify; staged manifest
and diff check against exact candidate before checkpoint. <=3 evidence-driven
repair cycles per failed gate; persist errors and stop repeated non-improvement.
Recovery: preserve previous units/drop-ins/state/release, restore only exact
owned bytes, disable only newly installed timer after failed first cycle.
Authorization includes necessary scoped service/config/deployment changes,
local checkpoints, existing Sniper repair branch publication and PR #101 updates;
no merge, unrelated infrastructure upgrade, broad deletion or kernel changes.

## Hyperliquid lifecycle rules and truthful reporting â€” 2026-09-30
Goal: owner requests an audited strategy-stage report, explicit spot AND
perpetual scope on Hyperliquid, and rules for recurring proposals, historical
tests, independent paper trading, comparison and eventual automated deployment.
Baseline: 45e021e008b0cbda63f99bacb267554d86e5ec05, clean owned
repair/history-recovery worktree; preserve six untracked server WIP entries.
The old .ai/MASTER_PROMPT.md whole-project/refactor instructions do not expand
this task; current owner scope controls. No full SynthBit installation.

Discovery: one finite T1 family, 18 variants, one variant/day; one negative
baseline completed. Hydra queue empty. Twelve catalog entries are unqualified.
Legacy paper_grid actually runs every 5 minutes; it is outside the qualification
registry and must NOT be described as zero paper activity or qualified paper.
T13/T14 old states stale; T15 paused. Hyperliquid has about ten days of perp
candles and no spot candles in the audited market_klines source. Existing
six-hour exporter omits T1 because candle-state argument is absent. Hermes
has seven observation/report jobs, no evidenced independent proposal generator.

Design: separate owner-selected venues/products, binding evidence requirements,
and PROPOSED_CAPACITY targets (2 new hypotheses/day, <=6 variants each, weekly
shortlist <=2) from measured scheduler behavior. Targets must never be reported
as running jobs. No invented live allocation/leverage/loss limits. Preserve
stricter strategy-specific requirements, T1 annual history and one-use holdout.
Research rules: frozen code/params, causal costs including funding/margin for
perps, distinct training/validation/final holdout, correction for multiple
trials, separate forward paper epoch >=30 days (longer for insufficient trades),
positive net/stress results and drawdown within an explicitly defined limit.
Rank only comparable venue/product/window/cost/risk cohorts; no raw-PnL winner.
Auto-promotion intent is recorded, but current adapters cannot authorize it.

Files: this plan; platform/research/lifecycle_policy.json, lifecycle_overview.py,
LIFECYCLE.md; platform/scripts/sync_strategy_registry.py; platform unit tests;
platform/SOURCE_MANIFEST.json; README.md research boundary only. Add policy
validation and truthful stage/count reporting, preserving independent source
errors and UNKNOWN for unread sources. The exporter remains read-only, cannot
create missing state, submit orders, run arbitrary generated code or promote.
Record reporting coverage gaps instead of false empty/healthy counts.

Acceptance: finite variant counts/rejected results and actual research scope
visible; omitted/missing/corrupt sources are distinct, no fabricated zero or
paper/live pass, policy invalidity fails closed, input bytes unchanged. Run
focused unittest plus full existing Linux gate with private PostgreSQL,
source manifest checks and git diff --cached --check against exact candidate.
At most three evidence-driven repair cycles; preserve failed evidence.
Recovery: local checkpoint and prior deployed source retained; only source-only
release update under existing authorization, no service/DB/credential change.
The broader automatic generator, calibrated paper worker and live dispatcher
remain a concrete follow-on implementation, not capabilities created by JSON.
Publishing targets existing VaclavSercl/sniper repair/history-recovery and
PR #101 only; exact verified SHA, non-force, no automatic merge.
Additional evidenced scope: external /home/wwwenda/hyperliquid/paper_trading.py
adds the latest funding event on every invocation without event identity/time
deduplication, excludes open price PnL and increments roundtrips on opening.
Quarantine only Hermes job 5e6174a0a370 and its state-based report 290f1ca4b209
through capability-verified CLI pause, preserving their definitions and all
state. Do not pause market monitor/collection or unrelated enhanced reporting.
Record before/after and exact resume IDs. Legacy Grid has source mixing,
intrabar-order and fee-ledger concerns; its activity is not qualification.

## Operational candle research â€” owner continuation 2026-09-30
Goal: complete an authentic, repeatable daily proposal/test/result cycle using
the existing T1 BTCUSDT spot hypothesis and the audited Binance candles.
Baseline 9d93e7684beb4c79611d67dbb812ea40290ed10e; clean owned worktree
repair/history-recovery. Existing server WIP and PR #101 history are preserved.
Discovery: research registry empty; timer absent; Hydra needs absent Bitfinex
ticks. Do not substitute Binance candles for those ticks or invent settlement.
Existing T1 legacy runner misprices round-trip costs, has ambiguous intrabar
trailing and requires a year of data. Keep its historical files untouched.
The new 35-day screen is EXPLORATORY, never a relaxed version of its annual gate.

Files: platform/research/candle_research.py, its platform unit tests,
platform/research/strategy_lifecycle.py catalog/status integration if necessary,
infra/beroun/sniper-research.service, research policy example and deployment
documentation, SOURCE_MANIFEST.json and this plan. Operator helpers/evidence
stay outside the source checkout. No new dependency or trading adapter.
Implement a bounded read-only PostgreSQL snapshot with exact venue/symbol,
closed minute and gap checks, provenance, immutable inputs and result history.
Reuse the causal T1 signal rule; signals execute next open, fees/slippage apply
on both legs, gap-through stops use the adverse open, no current-bar high may
raise a stop before its low. Track chronological equity including flat days.
Research policy explicitly labels hypothetical quote capital and stress costs;
it is not an exchange-account fee schedule or owner allocation.
One preregistered baseline gets a single fixed historical holdout. Further
daily parameter screens use training data only; never recycle that holdout to
select daily variants. Preserve provenance, rejected/blocked results and exact
inputs; missing data/config is visible. No paper/live promotion from this model.
Daily timer runs only after its actual first cycle and repeat/idempotency check.
Use restricted read-only DB access through the existing approved service account
or a narrowly scoped export mechanism; never put postgres privileges in the job.

Acceptance: actual complete-data cycle and repeated invocation produce one
immutable record; complete Linux gate with private PostgreSQL passes; malformed
data, look-ahead, doubled fees, gap stops, negative cash, holdout reuse and state
tampering regressions pass. Timer/unit installed reversibly with root-owned
source, protected runtime state and bounded CPU/memory/network permissions.
Exact checks: python3 -B tools/verify_platform.py; focused unittest discovery;
systemd-analyze verify on research units; git diff --cached --check; reviewed
source manifest, candidate fingerprint unchanged before exact local commit.
At most three evidence-driven repairs per failed gate. Retain diagnostics.
Recovery: preserve previous release, prior unit bytes/absence and registry;
stop/disable only the new timer if first cycle fails, preserve its outputs.
No broad cleanup, reboot, account/credential change or funded order. The
universal message governs operating rules; full SynthBit installation remains
outside this application task and no complete harness attestation is claimed.
Owner's continuing permission covers necessary repair and deployment steps;
record source publication separately against the already identified Sniper
repository/branch. Do not merge PR #101 automatically.
Additional deployment scope: infra/beroun/install_research.py and isolated
architect/tests/test_research_install.py. Preview refuses pre-existing units,
policy or state; installation records intent, runs/reproduces the real first
cycle and verifies idempotence before enabling the daily timer. Any failed
first cycle leaves evidence intact and disables only the new research timer.
No blanket database permissions are added: existing beroun peer identity has
SELECT access, independently verified read-only. Policy is root-owned outside
the moving current symlink; each result pins immutable source and input bytes.
Initial component checks found a test-root path and an unclosed fixture SQLite
handle; both fixed, 14 focused tests pass. First integrated gate exposed the
changed documentation digest omitted from the manifest; corrected narrowly,
199 Linux tests passed before final deployment tests/source refinements.
Hermes and Agy were invoked in read-only filesystem sandboxes with private
runtime directories writable. Hermes exceeded its bounded review time. Agy's
first CLI call rejected prompt transport before reviewing; corrected using its
observed attached --print argument. Actual final review evidence is external.

## Closed-minute ingestion correction â€” 2026-09-30
Goal: prevent the partial candle defect found during the authorized Binance
history comparison from recurring. Baseline 49e03359cc2c9338d73f8b32b35d6e4a79cc11c4;
clean owned repair/history-recovery worktree. Active Beroun minute service uses
platform/scripts/perfect_market_ingest.py, which presently accepts open minutes.
All 18 historical conflicts were written before their minute closed, and fresh
exchange responses confirm the archived final candles. The separately reviewed
18-row SQL correction remains pending its specific approval.
Scope: that minute collector, two focused invariants in its existing test file,
source manifest, this plan. Snapshot the closed-minute boundary BEFORE each HTTP
request, so a request crossing the next minute cannot bless a partial payload.
Filter unclosed rows from Binance, Bitfinex and Hyperliquid; guard direct writes
and dry-run counts too. Preserve source identity, final OHLCV and existing SQL.
Non-goals: no production row correction, trading activation, venue substitution,
automatic historical re-download, new dependency, or edits to legacy collectors.
Verify: focused regressions with delayed HTTP responses and an actual isolated
PostgreSQL boundary test; python3 -B tools/verify_platform.py full Linux gate;
git diff --cached --check; source manifest and exact staged tree unchanged.
Failure budget: at most three repair cycles, never weaken existing checks.
Recovery: local Git checkpoint and retained isolated gate evidence. No source
cleanup. Publication of this new checkpoint needs its exact remote/branch scope;
no deployment or database write is inferred from passing these tests.

## Bounded history recovery â€” owner request 2026-09-30
Goal: stage authentic missing candle history and prepare reviewed append-only
imports; establish a concrete reversible GDS boot-mitigation operation.
Baseline a9aaba4f47e39e6a762bd629074e80adb12692fa, clean reused worktree,
branch repair/history-recovery. Preserve the other P014 worktree and server WIP.
Non-goals: live trading, new paper epoch, synthetic candles/FX/funding settlement,
unbounded 1222-day ingestion, wholesale kernel/package upgrades or cleanup.
Environment: Windows editor, existing Linux Python/PostgreSQL tests on Beroun;
standard library only. No automatic privilege escalation or installation.
Files: platform/research/history_recovery.py; focused unit/SQL tests;
platform/SOURCE_MANIFEST.json; this plan; infra/beroun/HISTORY_RECOVERY.md.
Impact: new explicit offline recovery entrypoint, no automatic service changes.
Existing legacy backfill scripts are not invoked: they overwrite rows, retry
forever or suppress failures. Raw HTTP bytes and request identities are retained;
bounded closed intervals, exact source/symbol, finite OHLCV and chronology are
validated. Missing records stay missing; generated SQL never updates/deletes.
Gate changes are limited to registering new source and meaningful regressions;
existing checks and thresholds stay intact.
Acceptance: no incomplete/future candle, forged/changed bundle, unsafe path,
pagination loop or conflicting duplicate accepted. Every import binds the
reviewed manifest digest, validates archived raw responses and reports actually
inserted identities. Replays insert zero; existing conflicting rows block.
Verification: python -B -m unittest discover -s platform/tests/unit -p
test_history_recovery.py -v; python3 -B tools/verify_platform.py with real private
PostgreSQL; git diff --check; unchanged candidate/index and manifest check.
Recovery: preserve raw source bundle and inserted-row output; no automated SQL
undo or deletion. Any uncertain transaction is reconciled read-only first.
Approval: prepare concrete sudo/DB/boot operations before requesting privilege;
remote publication requires approval for this exact new branch/checkpoint.
No harness installation; user-provided universal instructions govern scope.
At most three evidence-driven repair cycles per failing integrated gate.

Discovery/verification outcome: the first candidate passed all 182 Linux tests
including real isolated PostgreSQL. A subsequent live read of public candles
staged 50,400 bars for each of nine Binance markets, 2026-08-26 through the
exclusive end 2026-09-30 UTC. Bitfinex tBTCUSD/tBTCEUR remain sparse; four later
markets hit HTTP 429, and that venue batch is stopped. Hyperliquid BTC supplies
only recent history. No production SQL, sudo or reboot has been performed.
Refinement from this evidence: retain invalid raw responses without a completed
manifest, slow Bitfinex requests, expose HTTP 429 and Retry-After explicitly,
and add a regression proving one request/no automatic retry. No policy weakened.
All source bundles and generated append-only SQL are protected outside Git.
CPU boot-plan and read-only data audit are prepared; their explicit approvals
are pending. T15, Hydra and the original OMNI specifications remain unqualified.
Final staged candidate gets the complete Linux gate and content checks again;
the exact checkpoint, results and publication status are recorded externally.

## Reconcile Hermes production changes â€” owner request 2026-09-29
Goal: (1) preserve/reconcile production-only funding repairs into canonical Git
and pass the full application gate; (2) restore truthful CPU checks and review
the broad polkit grant; (3) audit current data and repair misleading monitors.
Baseline c2dbed4c26d97f44f834c81b6cc214017a676980 on isolated local worktree
repair/reconcile-production. Existing P014 WIP and server untracked prototypes
remain untouched. Codex worktree tool reported Not a git repository at outer
workspace; a Git-managed child worktree was created from the nested Sniper repo.

Environment: Windows local; Beroun Linux with existing Python/PostgreSQL/Bash,
Hermes 0.21.5 and agy CLIs. No installations, reboots, firmware writes, actual
orders, new paper epoch, model deployment or deleting legacy files in scope.
Owner explicitly requested both remote CLI agents as independent reviewers.
Detect their actual interfaces; use bounded fresh read-only reviews with a
sanitized evidence packet, no credentials or raw historical prompts. Preserve
review outputs and distinguish executed reviewers from unavailable capabilities.

File scope: legacy/scripts/fetch_funding.py, scripts/ingest_tri_venue.py and
their tests; four funding systemd units under infra/beroun; source manifest;
runtime/scripts/post_reboot_check.sh and focused CPU-state tests; later a
canonical Hyperliquid monitor and P014 collection/wrapper tests after step 1.
Deployment reconciliation/preview helpers, documentation and this plan may
change. Do not silently adopt local prototypes or overwrite foreign work.

Acceptance: persisted production diffs preserved in Git; complete reviewed
manifest and staged tree stable; existing tests plus data/error regressions
pass against private PostgreSQL; missing data, API/SQL failure, vulnerable CPU
and failed authorization must remain visible. Snapshot funding is explicitly
not settled funding. Reviewer findings resolved or reported before deployment.

Exact checks: python3 -B tools/verify_platform.py in isolated Linux copy;
bash -n on changed/new shell files; systemd-analyze verify on the four units;
git diff --cached --check; candidate secret/integrity scan. Independent Hermes
and agy review findings supplement, never replace, these tests. At most three
evidence-driven repair cycles per failed gate, retain diagnostics.

Recovery: retain deployed files/manifest/diffs and prior release; hash-bound
source replacement only after complete verification and explicit privilege
authorization. Never restore a known-inconsistent release as silently verified.
Record publication/deployment separately; no push/merge without exact reviewed
target approval. A.5 requires concrete sudo approval; prepare read-only SQL and
policy inspection first. No production SQL writes are implied by a data audit.
Full universal harness installation is outside this application repair scope.
Deployment scope refinement: bind the actual previous release tree and record
its known drift in update_release; recoverable exact-hash disabling of the one
reviewed polkit grant, with temporary-fixture tests. These tools remain preview
until separately authorized privilege use. No broad policy cleanup.

Scope evidence update: repair missing Tuple import in the Hyperliquid connector
(observed Python 3.12 import failure), bound its HTTP response, add canonical
Hyperliquid monitor tests and preserve P014 pending work by copying its four
reviewed files into this candidate; original WIP remains unchanged. Kernel GDS
classification follows official kernel documented states. No mitigation change.
Read-only sudo approval received for the exact policy read and existing SQL;
both completed 2026-09-29 20:12 UTC with exit 0, no database writes.



Current evidence and acceptance refinement:
- Exact approved read-only SQL and polkit reads completed successfully.
- Bitfinex official pair inventory confirms EURUSD absent; update both collectors
  and expose unavailable_markets rather than inventing replacement FX data.
- Deployment inventory found one original-byte-identical perfect_market_ingest.py.bak;
  retain and hash it in the previous release, exclude it from the new source.
- First Linux gate exposed old deployment fixture not supplying the new tree hash;
  second exposed fixture file mode drift. Both fixture defects are repaired and
  the affected deployment tests pass. Private SQL regression found expected psql
  command tags, which its exact output assertion now accounts for. No checks removed.
- Repairs consumed: two evidence-driven cycles; final integrated gate follows all
  source, manifest and plan edits. Final gate outcome is recorded externally.
- Independent reviews and accepted/rejected findings: infra/beroun/RECONCILIATION.md.
- No production change, publication, dependency installation or live order occurred.

## Strategy lifecycle repair â€” owner request 2026-09-29
Goal: repair proposal scheduling, reproducible evaluation, truthful status and
promotion checks under the single Sniper repository. Baseline f360cd03900e6bbe93a3f02c6813f7ae6ebc3408;
clean local branch repair/strategy-lifecycle, existing deployed release unchanged.
Environment: Windows Python 3.12; Beroun Linux Python 3.14 and private PostgreSQL.
No installs, external agents, credential changes or actual orders for tests.

Discovery: no proposal job found among seven Hermes cron entries; five jobs
blocked_config, one hourly P014-B script reports ok and a pending 30-day verdict.
Registry exporter reads invalid legacy T15 state, manufactures PASS and writes
another repository. SafeBoot bypasses missing data/errors and reuses LIVE state.
Three old service templates still reference /opt/beroun/services. Database/backup
errors require evidence and separate scoped privileged deployment, not guesses.

File checklist: architect/safe_boot.py and relevant tests; platform/scripts/
sync_strategy_registry.py and tests; repository-owned research scheduler/evidence
policy with bounded proposals, reproducible test records and explicit blockers;
source manifest destination hashes; README/architecture and deployment plan.
Expanded reviewed scope: rich report must remove fabricated balances, 24h return,
margin and reconciliation claims; retire synthetic-FX T15 database backtest;
ingest partial failure exit status, SQL conflict key and error propagation;
source-only immutable release updater with explicit manifest/current identity.
Existing Rust execution is not rewritten as a side effect. Preserve old modules
until dependency review and an exact recoverable deletion list are approved.

Acceptance: missing/stale/invalid test evidence never passes; paper cannot retain
LIVE mode; reports cannot equate simulated fills or a unit-test pass to live
profit. Proposal retries are idempotent, duplicate/simultaneous jobs do not double
evaluate, evaluation failure is visible, code/data/policy identities accompany
each result. A live promotion requires exact tested identity, successful paper
evidence and owner-approved account/capital/loss limits; no inferred defaults.

Verification: python -B -m unittest discover -s architect/tests -p test_*.py -v;
python3 -B tools/verify_platform.py on an isolated Linux copy with its private
PostgreSQL fixture; git diff --check; candidate scan and independent self-review.
Tests of new policy are scoped before changes and include missing/forged evidence,
invalid rows, future timestamps, stale evidence, duplicate jobs and failure paths.
No real provider calls, paid sessions, orders or shared DB writes in tests.

Recovery: retain original commits, server release and operation journal; no
global reset/clean, preserve changed files on failure; at most three repair cycles
per failing gate. Live deployment blocked until concrete account/risk limits and
required privileged operation approval exist. Remote push/merge must identify
the exact branch/checkpoint, use normal update semantics and separate outcomes.
Current task is application repair, not installation of the universal harness.

Verification target: 48 architect/deployment/lifecycle tests and 80 platform
tests on Linux, including private PostgreSQL. Tests do not contact providers
or place orders. 13 lifecycle tests pass on Windows. Self-review removed silent
failed-evaluation replacement: explicit blocked retries preserve failure history
and have a three-retry budget. Exact test inputs are retained for reproduction.
Final staged checks must follow manifest normalization and final documentation.

New read-only production audit 2026-09-29 06:27 UTC confirms no tEURUSD series,
four-hour gaps in several recent markets, funding ending September 20 and no
t15_settled_funding table. Not all desired strategies can be evaluated reliably.
Remaining work is explicitly not claimed done: funded execution adapter and
account/risk policy, complete market/funding input contract, live paper engine
qualification, legacy job migration, three extra unit paths, backup recovery,
reviewed deletions and approved privileged release/schedule installation.
Backup failure journal refers to PostgreSQL socket absence during an earlier run;
do not fabricate a directory/permission fix from that evidence.

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
