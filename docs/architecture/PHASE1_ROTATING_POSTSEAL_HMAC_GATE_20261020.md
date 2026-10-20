# ECONITH Phase 1 — Post-seal rotating raw -> HMAC receiver -> checkpoint gate (2026-10-20)

**DRAFT, SIMULATION-only, offline data fixture.** The previous P1-05 source layer established a single continuously attached public Binance Spot WSS reader over 120 seconds, with 2,557 local frames and nine sealed raw SQLite journals. This stage uses the **already reviewed, independently authenticated child-process HMAC receiver** to test handoff of those types of *closed* journal segments. It **does not** deliver live events while the source WebSocket is open; this is a deliberately narrower engineering gate.

## Boundary and architecture

1. The previously sealed rotating journal root must pass its independent local source audit and have no orphan owner lock. Each session uses `rec_rot_` prefix supported by the existing forward outbox/checkpoint identity validators. Raw original and same-COMMIT intents are not altered. A single scoped `STAGED_RECOVERY` owner lock is held across the entire offline handoff and each per-segment reconstruction acquires a second segment-scoped ownership marker. There is no automatic operator takeover.
2. Each segment begins with empty SIMULATION-only forward outbox, primary derived, independent named observer and checkpoint SQLite ledgers. `recover_staged_forward_spot` rescans original receipts *as of their stored timestamps*, reconstructs only forward-eligible projections, sends envelopes over authenticated local subprocess IPC to a separate receiver-owned SQLite, awaits independent ACK, publishes verified read-only research projections to three derived observer topics, then checkpoint-reconciles the exact ACKed ordered source suffix.
3. The separate read-only `phase1_rotating_hmac_audit` reopens all originals, capture intents, outbox, receiver-owned receipt and chained checkpoint SQLite ledgers; verifies original hashes, projection hashes, receiver receipt IDs, checkpoint chain and original source coordinates. It flags that cryptographic IPC channel challenge/signing cannot be independently re-attested after the ephemeral child has exited. It does NOT claim uninterrupted in-session HMAC forwarding.
4. On any failure or partial handoff the operator stage writes a durable quarantine marker and refuses transparent retry. Physical process exit via `os._exit` leaves the global and local marker unreleased: an operator must inspect the uncertain ACK boundary and safely establish process liveness before any manual recovery. No auto-resume, no silent source coverage claim.

## Negative/fault gates

- Tampered original SHA or incomplete raw segment: refuse before constructing any downstream projection.
- Failed/cancelled segment after one completed segment: quarantine entire handoff, never certify a partial suffix.
- Real process hard kill **after receiver SQLite commit but before producer outbox ACK**: one durable receiver receipt may exist, outbox/checkpoint may be incomplete, and owner lock must prevent unreviewed replay.
- Separate subscriber/receipt/checkpoint parity checks. No position management, CCXT, account calls, orders or capital exposure.

## Deliberate nonclaims

- Not a 24–72 hour live soak; downstream HMAC publication is post-seal only, not continuously co-resident with the single live market source.
- No venue history completeness, source-event cryptographic exchange attestation, hardware power failure, external exactly-once effects, distributed fencing, or automatic failover.
- Phase 1 frozen provisional evidence **81/100**. Specifically P1-05 still 13/15 pending concurrent source→HMAC sustained duration proof and independent end-to-end restart power/ACK proof; P1-06 default cutover and P1-07 multiasset acceptance remain open.
