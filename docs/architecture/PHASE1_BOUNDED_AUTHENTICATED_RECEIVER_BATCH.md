# Phase 1 — bounded authenticated local receiver batch (research only)

## Scope and motivation

Previously the saved-public-market source -> verified projection -> independent
receiver acceptance chain launched **one Python interpreter per trade**. On a
184-trade capture this was 184 process startups **before** historical ASGI
readiness, despite the fact that each receiver wrote only one SQLite row.
This is a performance problem, not a justification to skip independent ACK.

The optional `authenticated_subprocess` branch of the isolated offline
verified research chain now prepares every source-verified projection in the
fresh producer SQLite, submits chunks of **at most 32 envelopes** to a fresh
independent local subprocess per chunk, and then separately ACKs each claim
in the producer inbox. The normal in-process branch is unchanged. The
`accept_via_authenticated_subprocess` legacy single-message API remains
available for other callers.

The child has no network listener. A new ephemeral 256-bit HMAC key is
provided through its environment; packet and complete response use separate
versioned HMAC domains (`BATCH-REQUEST-v1` / `BATCH-RESPONSE-v1`). Parent
and child impose strict bounded JSON, schema and batch count requirements.
No pickle, API credentials, order events, broker integration, or real trade.

## Proof and failure behavior

1. Each original public captured byte record is validated and promoted into
   a source-bound durable producer projection, preserving the existing
   original journal and verified lineage.
2. The independent child still persists **one durable receiver-owned
   SQLite receipt per source projection** in a separate SQLite database.
   Duplicate source IDs re-open to the original receipt; incompatible data
   are rejected. No unverified callback or external operation runs here.
3. The child returns a **complete ordered receipt array**, with one HMAC over
   the response. The parent validates the count, each source/receiver/ingest
   identity, event ID, projection fingerprint, receipt ID, and receive time.
   An incomplete or mismatched response returns *no* usable ACK.
4. Only after the complete authenticated response has been validated does
   `OfflineCrossProcessProjectionSender.deliver_once` independently claim
   each projection, compare the claimed envelope with the child-confirmed
   envelope and COMMIT each **individual producer ACK**. There is no fake
   queue-level success and no batch-level substitution for a row receipt.
5. If the child dies after some receiver COMMITs, the sender has no authenticated
   response, so it **does not source-ACK any of that batch**. Idempotently
   repeating the exact batch can recover the already-committed receiver rows;
   different payloads still fail closed. Crash during later producer ACKs may
   leave a verified subset in the producer inbox; remaining claims can be
   independently recovered using the persisted receiver ledger and original
   source. This does not claim atomic transactions across SQLite databases.

The report preserves `cross_process_authenticated_transport=true` and
`transport_scope=HMAC_AUTHENTICATED_LOCAL_SUBPROCESS_PIPE_ONLY`, and adds
`authenticated_receiver_batch_limit=32` and
`authenticated_receiver_process_invocations` for auditable observability.
For a *184-trade* session, the new code's deterministic batch arithmetic
requires **6 child invocations**, instead of 184; this is not a measured
wall-clock speedup, and end-to-end latency must be benchmarked separately.

## Verification and explicit exclusions

`pytest tests/test_phase1_batched_receiver_ipc.py
tests/test_phase1_authenticated_receiver_ipc.py
tests/test_phase1_offline_real_capture_chain.py -q`

The new tests exercise actual separate Python subprocesses, idempotent
retry across process restart, authenticated packet tampering, duplicate/
oversized batches, partial receiver COMMIT plus retry, malicious incomplete
or inconsistent signed ACK bodies, and exact end-to-end source/receiver counts.
Full Python CI and production-image/container smoke remain separate gates.

**This is bounded *historical* verified-only research preflight.** It does
not make 24/7 public market ingress continuous, repair unobserved outages,
provide durable receipts for every arbitrary EventBus subscriber, enable
verified-only default `main.py`, validate profitable strategies or grant
broker execution. Keep the PR in Draft, `main` unchanged, and real trading
disabled until separately reviewed and certified.
