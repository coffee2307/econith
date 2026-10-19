# ECONITH institutional Phase 1 — frozen exit-gate matrix

**Date:** 2026-10-18. **Scope:** original Phase 1 from the owner's
Institutional-Grade roadmap, not a reduced or retrospectively
redefined "offline research only" phase. PR #28 remains DRAFT.
**Exit status: OPEN.** No default runtime cutover, capital, live orders
or production deployment is authorized by this document.

## Why Phase 1 has taken multiple days

The project was implementing multiple proof-of-concept transport,
receipt and research layers, while the original broad "institutional
market data" exit condition also required continuous reliable
ingestion, multiple asset/data types, default runtime integration and
long-run operational verification. Unit-test counts grew but the last
four readiness gates did not move. A gap-free market history or a
durable order book cannot be proven by green CI alone. Consequently
old percentage claims were engineering estimates, not certification.

**Freeze the checklist below.** Future tasks should close one of these
gates or be deferred explicitly. Do not invent additional automatic
requirements in this phase without a written owner decision. Any
change to the denominator needs an approved new version of this
document, not an altered percentage.

## Weighted engineering completeness (not an exit certificate)

| Gate | Required output | Weight | Evidence credit | State |
| --- | --- | ---: | ---: | --- |
| P1-01 | Original source capture, indexed immutable bytes, REST+diff Spot synchronized snapshot, as-of closed replay | 20 | 20 | PASS within BTCUSDT opt-in scope |
| P1-02 | Source availability, lineage, authenticated independently owned source/receiver ACK | 15 | 15 | PASS in bounded research trials |
| P1-03 | Raw+derived publisher fences, primary/audit/monitor separately durable read-only projections | 15 | 15 | PASS in opt-in SIMULATION graph |
| P1-04 | Atomic source intent, original-as-of eligible event rescan, outbox ACKs, crash recovery and process fencing | 15 | 14 | PARTIAL: external host crash acceptance outstanding |
| P1-05 | Sustained continuous protected ingress, reconnect/catch-up, bounded backlog, long-duration host soak | 15 | 12 | PARTIAL: reused pinned WSS across independently sealed forward segments, failed-link reconnect and durable gaps; real 24h soak/completeness unproven |
| P1-06 | Reviewed default-app source cutover, no legacy data bypass and fully isolated execution | 10 | 4 | PARTIAL: CI AST gate catches verified/legacy engine crossover; default still legacy, migration unapproved |
| P1-07 | Original dataset coverage beyond BTCUSDT and real operator-certified 24–72h continuity/quality evidence | 10 | 0 | BLOCKED: no current-HEAD external soak or full dataset matrix |
| **TOTAL** | | **100** | **80** | **Phase 1 remains OPEN** |

**80% is a conservative, explicitly subjective weighted engineering
estimate.** The *certified institutional Phase 1 exit score is not this percentage*:
all mandatory gate rows must be PASS before 100% can be stated. Neither
a source label nor simulated forward data authorizes orders. Existing
real public Binance operator observations belong to earlier commits
and are not silently transferred as certification for this HEAD.

## Corrections completed during the present iteration

At reviewed code commit `05c4dc87a29ae32c1d87091c4bd7203dec38520d`:
- Raw source and forward capture-intent inventory now COMMIT
  **atomically in the same original SQLite transaction**.
- Every persisted raw record must match the durable source-intent
  metadata (ID, session, kind, local receipt and SHA-256).
  A failed intent INSERT aborts the original COMMIT.
- The forward producer and manual staged recovery claim one
  exclusive local process ownership lock. Active/missing/orphan
  ownership cannot silently become a second writer.
- Real OS subprocess hard-exit and source/ACK fault windows already
  have durable idempotency checks from earlier Phase 1 revisions.
- CI `#39099273369`: **699 passed, 1 skipped**, zero failed;
  container images and container smoke succeeded on the same commit.

**Correct interpretation:** the capture inventory tells recovery that
a raw row was committed, but cannot itself attest that an as-of,
depth-synchronized, *eligible* event had reached STAGE. Staged-only
replay must still mark source completeness and session certification
false. An additional eligibility rescan is necessary before the
forward crash-recovery gate is fully closed.

## What a real 100% requires

P1-04 — Produce a deterministic original-byte eligibility rescan that
covers un-STAGED raw rows after a hard exit, recomputes source as-of book
state without future leakage, and reconciles exact derived receipts
under a retained producer lease. Add crash-before-stage and
crash-between-ACK tests, including failure-injection/restart on actual
host. Zero unclassified committed original rows in the approved
finite source scope; otherwise mark QUARANTINED.

P1-05 — Introduce a genuinely continuous read-only source service,
bounded in-memory queues and durable delivery checkpoints, with
reconnect invalidating prior depth and explicit unobserved outage
windows. Prove no stale book reaches research consumers, measure
throughput/backlog/memory/disk/tail-latency and time-as-of invariants.
Only a *new* snapshot allows resumed depth-derived publication.

P1-06 — Review and explicitly migrate `main.py` default source
selection only **after** isolating every order/CCXT route, credential
and legacy source publisher, removing raw/derived alternate admission
bypasses. CI must test invalid configs, forced fallback, restart,
stale data and real-vs-simulation mode. An opt-in historical ASGI
branch is not a default-market-data migration.

P1-07 — Collect versioned real public Binance capture proof with
clock/source/session provenance, long-run soak (operator-controlled
24–72h target) and independent disk/process restart validation,
plus the original multi-symbol/OHLCV, listing/delisting and optional
funding/OI, macro announcement-vintage acceptance where applicable.
If outside target Spot products, explicitly document N/A with
owner approval rather than invent observations.

### Freeze and decision authority

- This matrix supersedes **informal** percent estimates, not the
  original Phase 1 requirements.
- Read-only research scope may independently be marked complete after
  its own tests, but cannot be renamed "Phase 1 100%" without the owner
  formally moving the remaining requirements to another phase.
- PR #28 stays Draft/unmerged until evidence and review; no real
  exchange trading or deployment is permitted.

## Evidence update — optional forward preview and raw original eligibility rescan

The most recent iteration adds a deterministic, source-availability-ordered
rescan of **previously committed original Spot frames** into missing
forward outbox envelopes. It starts with an unsynchronized empty book,
replays the original snapshot/diff bridge, refuses stale/gapped/ambiguous
source data, checks every old immutable envelope before staging new ones
and replays through the separately authenticated receiver and read-only
derived observers. It does not assert missing external trade history or a
successful crashed capture session. A completely new receiver-owned DB
is created only for an entirely previously un-STAGED source with no
preexisting receiver effects; it cannot silently replace an existing DB.

`main.py` also has a new **explicitly configured**
`verified_forward_preview` mode. Only a bounded public-data-only
SIMULATION forward source and the guarded status endpoint are started.
No engine, exchange order bridge, legacy streamer, world service or
mutating HTTP handler is admitted in this branch. The `legacy` **default
has not changed**, and this new path is NOT continuous production ingest.

Bounded research tests on commit
`4bd586fba83a2351d43f29d79d18abdb5332d97e`:
[CI #39100349644](https://github.com/coffee2307/econith/actions/runs/39100349644)
**707 passed, 1 skipped, 0 failed**; corresponding images and container
smoke succeeded. The documentation-only follow-up commit requires
its own exact-HEAD CI confirmation.

Updated engineering evidence credit: **P1-04 14/15**, **P1-06 3/10**
and **total 70/100**. No previously open institutional exit gate was
renamed 100%. Real continuous service, long-duration hardware soak,
default cutover and full dataset coverage remain the dominant
unaccepted obligations. See
`PHASE1_FORWARD_RESCAN_ASGI_PREVIEW_20261018.md` for reproducible
offline-test scope and operator instructions.

## Forward outage/endurance supervision — incremental bounded evidence

`infrastructure/spot_forward_supervisor.py` and manually opted-in
`ci/phase1_forward_endurance_trial.py` now supervise a bounded set of
**individually sealed forward source sessions** with real SQLite
`STARTED/PASSED/FAILED` attempt state, auditable outage opening and
restoration, a new REST book/snapshot after failure, exact receiver-to-source
HMAC receipts and research observer ACK checks. The status captures per-run
queue high-watermark and disk bytes. An orphan owner lock and interrupted
attempt `STARTED` state require human review, never invented automatic
source continuity.

This is NOT a genuine uninterrupted continuous feed, a persistent receiving
service or a real-host endurance acceptance. Even repeated clean trials have
unobserved market intervals. The evidence credit on P1-05 rises from
3 to **5 out of 15**, giving **72/100 provisional engineering points**.
The original institutional Phase 1 exit decision is still **OPEN**;
no new denominator or 100%-completion claim is introduced. See
`PHASE1_FORWARD_OUTAGE_ENDURANCE_TRIAL_20261018.md` for
operator instructions and explicit risks.

## Bounded persistent authenticated receiver — incremental evidence

`infrastructure/market_persistent_hmac_receiver.py` and the private
`ci/phase1_persistent_receiver_worker.py` replace one-shot
**per-event subprocess startup** with ONE HMAC-authenticated independent
local SQLite receiver process per *finite opt-in research session*.
Signed ordered requests/ACKs carry strict monotonic sequence IDs, per
process ephemeral 256-bit keys, original immutable source fingerprints
and independently COMMITted receipt IDs. A process crash never becomes
a success ACK; outbox/source-bound replay remains mandatory.

The opt-in `--persistent-receiver` flag is available for the bounded
manual `ci.phase1_forward_endurance_trial` command. The frozen default
`main.py` source and existing one-shot mode are unchanged. Tests
exercise real multi-message receiver PID reuse, replay/MAC denial,
process kill, uncertain ACK recovery and forward subscriber accounting.
Supervisor evidence stores number of worker process starts and measured
max per-request ACK latency, not invented production throughput.

Provisional P1-05 credit rises from 5 to **7/15**, for
**74/100 total engineering evidence**. Continuous 24/7 validated source,
real-network soak, load/RAM/disk/tail-latency acceptance, legacy default
cutover and broad market-data coverage are still unaccepted. Full Phase 1
stays OPEN and Draft PR #28 stays unmerged. See
`PHASE1_PERSISTENT_HMAC_RECEIVER_20261018.md`.

## Operator-duration watch and successful-session gap auditing

`run_forward_research_supervisor(...,watch_duration_s=...)` now
supports an opt-in monitored process target up to 72 hours using the
verified HMAC persistent receiver, time and attempt ceilings, the
existing independent original/ACK attestations and finite per-socket
sessions. The durably recorded watch plan distinguishes target reached
from early exhaustion of its attempt budget. Another table records
**unknown source coverage between any two successful or failed
sessions**, not only network errors. Local wall-clock rollback is
refused. These controls allow an operator to attempt a measured 24h
public-market study, but the current build has **not** collected or
certified that actual 24h network data.

P1-05 earns one further provisional engineering point, from 7/15
to **8/15**, for **75/100 provisional weighted engineering progress**.
No other gate is silently closed. A rolling operator trial does not
satisfy the still-open single continuous verified source, full
24–72h operator evidence, broad dataset admission or default
`main.py` production cutover. See
`PHASE1_OPERATOR_DURATION_WATCH_20261018.md`.

## Remote EOF and socket idle guard — current incremental correction

The public Binance Spot capture adapter previously inherited finite
*offline fixture* iterator EOF semantics. A public WebSocket could close
prematurely after a valid REST snapshot and still be counted as a
successful session. The real-public entry point now enforces explicit
frame-quota completion (`require_frame_limit=True`) and a configurable
per-frame idle timer (default 30s). Unexpected close or idle data feed
fails closed, leaving original/ACK/outage evidence to be classified.
The offline fixture early-EOF policy is retained explicitly but can
never be used as a real public-source closure proof. The opt-in forward
supervisor and operator CLI expose the same idle watchdog.

P1-05 receives one provisional evidence-credit point, from 8/15
to **9/15**, or **76/100 engineering completeness**. This does NOT
turn repeated bounded captures into uninterrupted 24/7 verified
market data and does not substitute for the operator-authorized
24–72h real-network acceptance. See
`PHASE1_PUBLIC_SOCKET_LIVENESS_GATES_20261018.md`.

## Durable live forward event checkpoints — 2026-10-18

`infrastructure/spot_forward_checkpoints.py` establishes an immutable
SHA-256 chain in a distinct WAL/FULL ledger at each eligible forward
event, *during the OPEN source session*, but only after original
COMMIT, producer outbox HMAC receiver ACK and all three durable
research observers have passed. After bounded source completion, a
supervisor independently reopens and compares the checkpoint's
source-bound prefix and digest against final source/outbox evidence.
The existing manually fenced recovery validates all old checkpoints
and reconciles only a missing crash suffix; modified hashes, missing
interior entries and source identity substitutions fail closed.

This earns a **provisional** P1-05 evidence-credit point from 9 to
10/15, total **77/100 engineering completeness**, only if final HEAD
CI succeeds. No counter here or in the runtime certifies missing
public exchange history, an uninterrupted source, 24–72h network soak
or reviewed default `main.py` cutover. See
`PHASE1_DURABLE_LIVE_FORWARD_CHECKPOINTS_20261018.md`.

## Faster closure iteration: pinned single-socket read-only forward research and static legacy boundary gate

This iteration implements a **shared hostname-verified public Binance
Spot WSS lease** across multiple separately checkpointed and
independently HMAC-receiver/observer-attested bounded forward segments.
A successful segment reuses the same socket, but STILL records a
`coverage_verified=0` unknown boundary. When a segment fails, the
lease is destroyed; any later segment must open a fresh connection
and new REST book. A fresh clock preflight runs before each segment.
This is a meaningful network churn/recovery improvement, **not**
an unbounded single-session proof of complete exchange history.

Two additional test gates are included in the SAME implementation:
(1) forced socket failure/reconnect with independent per-segment
snapshot, original/outbox/receiver/derived/checkpoint evidence, and
(2) a Python AST-based startup gate executed by GitHub CI, refusing
any future introduction of a legacy streamer, engine or CCXT call
into verified research branches, missing early return, or unreviewed
default-ingress change. The second gate explicitly records that the
default `main.py` still uses the **unverified legacy** publisher and
that it has not been migrated.

Provisional engineering evidence P1-05 rises from 10→**12/15** and
P1-06 from 3→**4/10**; **80/100** engineering points conditional on
same-HEAD CI PASS. The 100% institutional release gates remain
strictly OPEN: actual 24–72h real Binance source and host-resource
soak, default verified-only migration with full publisher/execution
audit, multi-symbol/OHLCV and applicable derivatives/macro vintages.
This is not production readiness or authority to trade money.

Operator instructions and fault semantics:
`PHASE1_SINGLE_SOCKET_SEGMENTED_SPOT_AND_CUTOVER_AUDIT_20261018.md`.
