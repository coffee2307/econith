# ECONITH P1-14 — Crash gap triage, immutable evidence and operator fence

**2026-10-21 · Draft stacked after PR #49. P1-04/P1-05 safety hardening.**
No app cutover, trading, source socket restart, new automatic cloud service
or claimed 24-hour source continuity.

## Failure model

Existing source-first WAL/FULL market research has separate durable
original+atomic capture-intent, producer outbox, HMAC receiver-owned ACK,
derived observers and hashed checkpoint. Already implemented staged
recovery may replay staged intents, independently reconstruct eligible
unstaged originals under explicit operator opt-in, and repair checkpoint
**suffix only**. It must not infer a continuous external market history.

There are **different crash windows**:

- Raw source+capture-intent committed, but projection not eligible or STAGED
  (this cannot be inferred only by subtracting outbox from source rows).
- Projection STAGED, receiver child may have independently COMMITted its ACK
  before the producer updated status.
- Receiver ACK and EventBus derived observers may have committed before
  the producer append of source-bound checkpoint.
- The source session itself may be open/aborted when the process dies.
  The local O_EXCL owner marker survives `os._exit` and MUST NOT be
  automatically reclaimed without identifying the active process.

## New independent local crash triage

`ci.phase1_crash_gap_triage` opens **only existing private ledgers, read-only**
using SQLite `mode=ro`, `query_only=ON` and `integrity_check`. No DB,
owner lock, checkpoint or source bytes are modified. The bounded audit:

1. Verifies single session, raw originals and **atomic capture intents**
   are contiguous with matching receive clocks, kinds and SHA-256 bytes.
2. Recomputes canonical source-bound staged-envelope fingerprints and
   verifies each record's source SHA/capture ID/observed availability.
3. Matches producer ACK state to the receiver's independently owned
   receipt ID, original payload, source timestamp and event identity.
   A receiver ahead of producer is classified as a known crash window
   rather than silently ignored.
4. Recomputes the exact checkpoint hash chain and ensures each checkpoint
   is an ordered **prefix** of source-bound fully derived-ACKed outbox
   envelopes. Middle holes, rewrites and forged source hashes fail closed.
5. Returns `QUARANTINED_MANUAL_OWNER_AND_RECOVERY_REVIEW` when a lock
   persists, the source was aborted/unclosed, any outbox is partial or a
   durable checkpoint suffix is missing. A fully closed coherent local
   source returns `CLOSED_LOCAL_LEDGER_PREFIX_CONSISTENT_ONLY`; this
   only means the inspected local ledgers agree, not full market history.

An operator can inspect a stopped existing evidence root:

```bash
python -m ci.phase1_crash_gap_triage \
  --trial-dir /ABSOLUTE/PRIVATE/EXISTING_SOURCE
```

Exit code 0 is allowed ONLY for coherent **fully closed** local ledgers;
exit 2 is required for quarantine or unverifiable evidence.
An unclosed/orphaned source is **never** promoted to passing capture.
This command never starts recovery or removes `.forward_producer.lock`.

## Tested boundaries

The dedicated CI uses deterministic local Spot synthetic fixtures, source
hash/intent and checkpoint corruption, missing independent receipts,
forged middle checkpoint, incomplete checkpoint suffix, and a **real
separate Python process terminated by `os._exit(47)` immediately after
SQLite checkpoint COMMIT**. The interrupted producer's orphaned owner
marker remains; subsequent recovery must refuse to steal it, and all
original ledger bytes must remain unchanged after inspection.

The event-to-checkpoint 300-frame public pilot and 120s public rotating
co-run evidence from preceding PRs are NOT a 24h soak and cannot be used to
make an aborted source gap disappear.

## Future authorized host procedure (not run here)

1. Disable any runtime/source autostart on the actual isolated VPS or PC.
   Check **externally** that the original owner has stopped; this
   read-only inspector cannot authenticate host process liveness.
2. Preserve a read-only snapshot/copy of every original, intent, outbox,
   receiver, observer and checkpoint file (including WAL files if present).
3. Run independent forensic inspection; review every partial source,
   ACK and checkpoint boundary. Preserve every unknown interval between
   sessions in the operating evidence record.
4. Only with separate human approval and verified inactive producer
   ownership may the ordinary existing **staged-only manual recovery**
   be considered. Do not delete or relocate a live owner's fence based
   on a PID or elapsed time alone. Do not re-open the same WS as proof of
   historical gap-free completion.
5. A restart after process loss begins a **new externally gapped source
   observation period**; uptime before and after is not additive proof
   of 24h continuous operation.

## Frozen Phase 1 status

Institutional engineering credit stays **81/100 provisional OPEN**:
P1-04 **14/15** pending external hardware/power-loss host acceptance;
P1-05 **13/15** pending continuous verified source+HMAC 24h operation,
bounded reconnect/gap and actual operator resources;
P1-06 **4/10** pending approved app-default verified-only cutover;
P1-07 **0/10** pending historical multiasset and 24–72h quality.
No auto-orders, no Exchange-origin signed-history/completeness, no verified
24h continuity, no real market profit and no default-app migration.

References: `PHASE1_IMMUTABLE_EXIT_MATRIX_20261018.md`,
`PHASE1_FORWARD_OUTBOX_RECOVERY_20261018.md`.
