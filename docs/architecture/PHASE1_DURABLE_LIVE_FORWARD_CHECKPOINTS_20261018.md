# Phase 1 — Durable in-session market forward checkpoints (SIMULATION)

**Status:** Opt-in BTCUSDT Spot research only. This feature improves
independent recoverability and operator inspection DURING an open source
session. It does **not** certify a continuous 24/7 public market feed,
complete exchange history, an arbitrary subscriber transaction, any
execution route or real-money trading permission. PR #28 remains Draft.

## Motivation / exact commit boundaries

The existing forward source already COMMITs the original immutable
public Spot WS/REST bytes and atomic source intent, validates the
incremental as-of book, stages a source-bound forward outbox envelope,
gets a receiver-owned HMAC ACK from an independent Python process,
COMMITs three independent derived research observers and finally marks
the producer outbox `DERIVED_ACKED`. These ledgers do **not** share one
ACID transaction. Before this work they had no compact, auditable
single-session receipt trail that an operator could inspect WHILE a
WebSocket remained open.

`infrastructure/spot_forward_checkpoints.py` introduces a strict
source-session-scoped SQLite WAL/FULL `forward_checkpoints.sqlite`
with an append-only SHA-256 hash chain of independently acknowledged
**eligible** forward events, not all raw transport frames. Each row
binds: ordinal, original capture ID, receipt availability timestamp,
exact `md:forward:<session>:<capture_id>` source event identity,
canonical producer envelope fingerprint, independently owned HMAC
receiver receipt ID, previous chain digest, and current digest.

`spot_forward_verified_research.py` writes each checkpoint immediately
AFTER `outbox.ack_derived`, but BEFORE the source WebSocket closes.
The checkpoint writer independently re-reads the outbox's completed
prefix; it refuses a missing receiver/derived state, a changed envelope,
non-monotonic receipt timestamp, invalid ordinal or a conflicting old
checkpoint. The research result exposes `forward_checkpoints` and
`forward_checkpoint_sha256`, which do not assert market completeness.

`spot_forward_supervisor.py` independently REOPENS the source,
outbox, new checkpoint and receiver/derived ledgers after each
finished bounded source session. Its per-session acceptance now requires
the complete checkpoint chain and result-tail match, not just aggregate
receipt counts.

## Crash recovery contract

A source process may die AFTER outbox `DERIVED_ACKED` but BEFORE
checkpoint COMMIT. The independently durable receiver and observers
remain unchanged. `recover_staged_forward_spot`, while holding its
single local producer/recovery ownership fence, first replays and
verifies the entire original/outbox/receiver/observer graph. If a
checkpoint ledger exists, it validates ALL persisted checkpoint rows as
the **exact oldest prefix** of final recovered outbox entries before
appending only the crash-missing suffix.

It never overwrites a corrupted checkpoint, never reorders an outbox
event and never fills a middle-of-chain hole. Forensic contradictions
fail closed. `checkpoint_suffix_repaired` reports how many rows were
appended in THIS recovery pass, while `forward_checkpoints` reports
the final durable count. A second recovery remains idempotent and
reports zero new repairs. Old pre-feature source roots without a
checkpoint ledger retain the previous recovery contract; they are
not falsely reported as having produced in-session checkpoints.

**Important limitations:** Chain hashes detect accidental tampering,
truncation in the middle, wrong source event/reordering and conflicts
with retained original/outbox evidence. Ordinary SHA-256 is **not a
cryptographic trusted timestamp**, does not prove no exchange data was
missed, and cannot prevent an attacker with full write access to all
local databases from rewriting all history. Missing trailing rows
after a hard crash can be reconstructed only after independent
source- and subscriber-bound proof.

## Independent tests and operational interpretation

The regression suite checks that a real receiver child and open
original capture create readable checkpoints *before session close*;
the final saved chain matches the accepted outbox; middle deletion and
hash alteration fail verification and recovery; a fault injected after
`DERIVED_ACKED` but before checkpoint COMMIT produces an exactly
repairable suffix with subsequent idempotent replay; and attempted
session identity rebinding or REALITY mode is rejected.

Do not equate these passing fixture-driven tests with multi-day real
Binance traffic. For public network observations use the prior
`ci.phase1_forward_endurance_trial` opt-in instructions. That
operator process remains bounded, snapshots a new book after
reconnection and explicitly marks gaps as **unknown**, not recovered.

Institutional Phase 1 exit still requires a real sustained continuous
source, measured public-network/host endurance and incident acceptance,
audited legacy default-app cutover and broader market data coverage.
No `main.py` default change or production deployment is made here.
