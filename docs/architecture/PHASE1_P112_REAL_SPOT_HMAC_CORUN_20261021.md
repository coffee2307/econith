# ECONITH Phase 1 P1-12 — Bounded real Spot + co-running HMAC acceptance

**2026-10-21. Stacked Draft on PR #47. Research-only, read-only public
Binance Spot, no credentials, LLM charges, execution or default cutover.**

## Prior constraint

The existing P1-05 trial had already demonstrated:
- **120.002s single Socket real public Spot** with one original SQLite ledger.
- Multiple original SQLite journals rotating behind a single reader.
- In independent *offline simulations*, authenticated separate-process HMAC
  receiver, durable outbox/checkpoint and two read-only subscriber ledgers
  operated concurrently with a simulated attached source reader.

These are different proof domains. The 120s public source run did not
simultaneously demonstrate authenticated HMAC receiver/checkpoint handoff.

## P1-12 integrated operator test

An explicitly opted-in CLI now exercises the existing
`ci.phase1_rotating_corun_hmac.co_run_rotating_hmac` with the only authorized
public transport factory and preflight. The run fixes:
- Exactly **one** pinned read-only Spot WSS and one reader task; target **120s**
  (hard public bound 90–150s), max 100,000 delivered frames and 32 rotated
  segments, with original source data never uploaded.
- 450 frames per segment (public bound 250–650); local raw SQLite WAL/FULL
  journals, atomic SHA-256 capture-intents and a fresh REST depth bridge per
  segment; an upstream bounded queue of 64 frames and an adapter queue of 128,
  with a strict 5s original source COMMIT-age cap.
- A dedicated worker queue capped at **two sealed journals**, processing
  sealed segments in source order while the source reader stays attached.
  Each is projected and sent through an authenticated separate-process HMAC
  receiver, original/projection identity and recipient-owned durable ACK
  checked, then primary + two named research observer ledgers and source
  checkpoint chain are reconciled.
- HMAC receiver batching **8** and connection reuse, bounded 30-second
  **seal-to-ACK** lag. This is *not* per-event WS-to-HMAC ACK age. Queue
  overflow, broken socket, bad depth bridge, age breach, worker failure,
  mismatch or incomplete session aborts; no automatic reconnect,
  unreviewed orphan takeover or PASS-on-retry.

The new `ci.phase1_public_corun_hmac_acceptance` entrypoint refuses a
producer-reported PASS unless the **existing independent read-only**
`ci.phase1_rotating_hmac_audit.inspect(...,co_run=True)` also accepts
every original, intent, projection, independent receiver receipt, three
observer ledgers and per-segment checkpoint. An additional typed policy
verifier refuses reports claiming institutional completeness, trading
execution, cryptographically signed venue history or event-continuous HMAC.
The existing auditor CLI now supports `--co-run`, to repeat the
independent inspection separately.

CI runs cheap offline contract tests first; **only a same-repo pull request**
can trigger the single public 120-second campaign. Fork PRs cannot trigger
the public job and no broad push/cron schedules exist. Output includes
sanitized aggregate JSON and independent audit metadata only, retained for
three days. All private original journal SQLite, raw Spot packets, receipt
data and transient HMAC keys remain on the ephemeral runner.

Reproducible operator commands:

```bash
python -m ci.phase1_public_corun_hmac_acceptance \
  --allow-public-market-data \
  --enable-independent-hmac-receiver \
  --acknowledge-not-continuous-24h \
  --duration-s 120 --frames-per-segment 450 \
  --max-handoff-age-s 30 \
  --output-dir /ABSOLUTE/FRESH/PRIVATE/CAPTURE
python -m ci.phase1_rotating_hmac_audit \
  --co-run --root /ABSOLUTE/FRESH/PRIVATE/CAPTURE
```

## What counts and does not count

A successful CI trial **will** support: in one bounded run a public read-only
source stayed attached during concurrent per-segment sealed HMAC handoff;
the independent receiver, derived observers and checkpoint chain were
locally inspected and consistent.

A successful CI trial **cannot** certify: independent venue-signed original
events, absolute market-history completeness, continuous per-*event*
HMAC forwarding, 24/72h sustained live service, host power loss/restart
fencing across hardware, default-app verified-only migration, multi-asset
listing/delist vintage, real trading economics or realized PnL.

**Phase 1 remains 81/100 provisional OPEN**, frozen under
`docs/architecture/PHASE1_WEIGHTED_RESCORE_20261019.md` (P1-05 13/15,
P1-06 4/10). Any change of acceptance credit requires independent
operator-reviewed evidence against original frozen criteria.
