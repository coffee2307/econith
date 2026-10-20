# ECONITH P1-11 — Independent one-socket original acceptance hardening

**2026-10-21 · Phase 1 P1-05 safety qualification · stacked Draft after PR #46.**
No `main` merge, default data-source cutover, exchange credentials, order
execution or production deployment.

## Why this was needed

A previously successful 120.002-second single-public-WebSocket source run
(2026-10-20, GitHub Actions 39342455892) produced 1,869 WS frames and
1,870 source SQLite rows. Its existing independent source inspector checked
local raw SHA-256, intent parity, report flags and row counts. That is useful,
but it did **not** independently check the original stream identity and
semantics, its corresponding API endpoint, the capture-session identity from
the report, or monotonic receipt cursor metadata. An adversary could edit a
stored local source and update both its source/intent digests without
triggering that narrow inspector.

## P1-11 implementation

`ci.phase1_single_socket_forensic_acceptance` layers stricter read-only
checking over the existing auditor:

1. Reopen the one sealed source SQLite ledger in `mode=ro` with
   `PRAGMA query_only=ON` and `integrity_check`. Refuse any owner marker.
2. Reconcile the claimed source session ID and source identity with the
   actual single SQLite session; check the Binance read-only WSS origin,
   session open/close clocks and exact final receipt cursor.
3. Require contiguous capture IDs with matching atomic capture-intents,
   nondecreasing local receipt clocks within the session, raw source SHA-256
   and bounds on each original's byte size.
4. Parse each original payload to reject forged symbol, stream, endpoint,
   event-kind mismatch, invalid depth `U/u` or REST snapshot/clock metadata.
   Capture ordinal and source first/last receipt fields are recalculated from
   SQLite, never copied from the aggregate.
5. Reconcile the producer report's `read_frames`, `websocket_frames`,
   source snapshot count, and explicit non-execution and non-completeness
   flags against the actual sealed originals.

**Critical change to the actual operator CLI:** the one-socket
`ci.phase1_single_socket_duration_trial` no longer returns exit code 0 based
only on its producer-reported success. After the owner marker is released,
it must obtain `PASSED_SINGLE_SOCKET_LOCAL_ORIGINAL_SEMANTICS_ONLY` from
the independent auditor. A failed re-audit returns 2 and prints
`REFUSED_INDEPENDENT_SOURCE_AUDIT`. The original evidence is preserved
unaltered for investigation. Producer and audit success are printed as two
separate verdicts.

## Reproducible commands

Read-only independent audit of a previously collected private original:

```bash
python -m ci.phase1_single_socket_forensic_acceptance \
  --root /ABSOLUTE/PRIVATE/ONE_SOCKET_EVIDENCE
```

Opt-in public *bounded* 90-second observation, **not automatically executed**
by any CI job in this PR:

```bash
python -m ci.phase1_single_socket_duration_trial \
  --allow-public-market-data \
  --acknowledge-not-24h-proof \
  --duration-s 90 \
  --output-dir /ABSOLUTE/FRESH/PRIVATE/EVIDENCE
```

No raw SQLite originals are uploaded as artifacts. Offline CI tests inject
only fake Spot data. Independent semantic checks do not authenticate TLS
transport on their own, reconstruct all invisible venue events or prove
historical market completeness. The public source default is unchanged.

## Phase 1 score and what remains

The frozen institutional evidence-credit score **remains 81/100 OPEN**:
P1-04 14/15 (physical restart/takeover acceptance);
P1-05 13/15 (true continuous source+HMAC and 24h operator soak);
P1-06 4/10 (reviewed *default* verified-only cutover and execution isolation);
P1-07 0/10 (historic listing-vintage, broad accepted market evidence and
24–72h proof). Only a future independently observed operator validation may
justify increasing credits against the original exit matrix.

Even after the forensic checks, a malicious writer controlling both original
SQLite and producer report can fabricate internally consistent bytes. Hash
parity, payload validity and local capture metadata are **not** cryptographic
proof of Binance provenance, continuous full-market coverage, actual
receiver/observer HMAC forwarding, historical fitness or trading profit.
The 120s legacy public result is **not** retroactively re-certified using this
new code without its original private SQLite evidence.

References:
- `docs/architecture/PHASE1_OWNER_FENCED_SINGLE_SOCKET_120S_CLOUD_RESULT_20261020.md`
- `docs/architecture/PHASE1_IMMUTABLE_EXIT_MATRIX_20261018.md`
- `docs/architecture/PHASE1_WEIGHTED_RESCORE_20261019.md`
