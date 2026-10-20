# ECONITH P1-13 — SOURCE to AFTER durable checkpoint completion age

**Phase 1 P1-05 research-only, stacked after Draft PR #48, 2026-10-21.**
No production default data-source cutover, live orders, API keys, paid AI,
24-hour service, autosupervision or authorized live capital.

## Why P1-12 did not yet establish complete timing

The existing finite **120.001-second public single-WebSocket + 5 rotating
journal + HMAC receiver** trial proved concurrent sealed-segment delivery
with 1,896 independently audited HMAC receipts and checkpoint chains.
Its largest *journal seal-to-HMAC/observer/checkpoint completion* lag was
11.71 seconds, not first source event to its own acknowledged checkpoint.

The older finite `spooled_in_session` pipeline **already** forwards
individual original-bound projections while a source session remains open.
Its `projector_p95_admission_age_upper_ms` samples are taken **before**
EventBus publication. These are not full receiver+observer+checkpoint age.

## New hard boundary

An **explicitly opted-in** `experiment_checkpoint_completion_timing`
mode, in the finite bounded spool only, records the local wall-clock sample
**after** the `forward_checkpoint` SQLite FULL-commit call has returned,
following each independently authenticated receiver-owned ACK and durable
derived observers. Any event more than the operator's 100–5000ms budget
after its original locally observed `received_at_ms` fails the entire
source session, even if earlier events reached EventBus on time.

Each bounded event sample is tied to its original source capture ID, local
received timestamp, receiver-owned receipt ID, checkpoint ordinal and
checkpoint chain hash. At successful source close, a size-limited private
JSON inventory is fsynced; a separate **read-only independent audit**
opens the original/receiver/outbox/checkpoint SQLite ledgers and refuses
missing, reordered or mutated samples, false age summary or claimed
unexpected/late source. This research report includes all eligible source
projections rather than all raw WS frames (raw depth may be redundant or
not yet synchronized).

The timestamp is **locally sampled** rather than an independent external
timestamp authority. It is not exchange clock latency, proof of unseen
venue event completeness, wire-HMAC key reattestation, atomic cross-DB
transactions, 24h continuity or internet end-to-end round-trip age.
Crash or aborted sessions have no accepted private timing report. The
existing source/replay/receiver/derived/checkpoint safety gates remain
mandatory; this auxiliary age ledger does not replace them.

## Operator CLI

```bash
python -m ci.phase1_public_live_spool_trial \
  --allow-public-market-data \
  --accept-experimental-spool \
  --measure-checkpoint-completion \
  --reuse-derived-connections \
  --reuse-producer-connections \
  --reuse-receiver-connection \
  --reuse-original-connection \
  --cooperative-reader-yield \
  --frames 300 --timeout-s 120 --max-lag-ms 5000 \
  --output-dir /ABSOLUTE/NEW/PRIVATE/SOURCE
python -m ci.phase1_checkpoint_delivery_audit \
  --root /ABSOLUTE/NEW/PRIVATE/SOURCE
```

Specialized CI runs offline injected-market test gates first, then only
same-repository PRs launch one finite **300-frame** publicly readable Spot
capture, not a 120-second guaranteed single-socket soak. We do not upload
private individual event timestamps, SQLite original ledgers or HMAC keys.
Only a sanitized audit of counts/latencies is retained temporarily.

## Institutional Phase 1 unchanged

Frozen **81/100 provisional OPEN**: P1-05 stays **13/15**, P1-06
**4/10**; remaining P1-04, P1-07 commitments unchanged. A positive event
completion age in a finite session does not satisfy an uninterrupted
24–72h protected source with replayable provenance, power-loss recovery,
reconnection gap semantics and independent operator sign-off.
