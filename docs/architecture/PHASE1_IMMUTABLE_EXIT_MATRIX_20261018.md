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
| P1-04 | Forward event source-intent, monotonic outbox ACKs, staged crash reconciliation, process fencing | 15 | 12 | PARTIAL: unstaged eligibility rebuilding not certified |
| P1-05 | Sustained continuous protected ingress, reconnect/catch-up, bounded backlog, long-duration host soak | 15 | 3 | PARTIAL: finite samples and in-session research prototype only |
| P1-06 | Reviewed default-app source cutover, no legacy data bypass and fully isolated execution | 10 | 0 | BLOCKED: default legacy publisher remains |
| P1-07 | Original dataset coverage beyond BTCUSDT and real operator-certified 24–72h continuity/quality evidence | 10 | 0 | BLOCKED: no current-HEAD external soak or full dataset matrix |
| **TOTAL** | | **100** | **65** | **Phase 1 remains OPEN** |

**65% is a conservative, explicitly subjective weighted engineering
estimate.** The *certified institutional Phase 1 exit score is not 65%*:
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
