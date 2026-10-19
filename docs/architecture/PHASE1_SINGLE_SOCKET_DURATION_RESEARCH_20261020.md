# ECONITH Phase 1 — One public socket wall-clock observation (2026-10-20)

**Status: experimental, source-only bounded research; NOT full institutional Phase 1 acceptance.** The pilot is not a 24h service, a broker or execution component, a gap-free market feed, or proof that downstream HMAC observers remain continuously live. Preserve provisional 81/100 until reviewed original acceptance.

## What changes from segmented 605.499s watch

The original segmented watch ran 11 independent 1,000-frame sources with 10 UNKNOWN gaps. This pilot holds exactly ONE Binance Spot WebSocket open and reads frames until an explicit monotonic duration target of 90 seconds, not until a 1,000-frame quota is exhausted. Frames are persisted as immutable original bytes plus same-COMMIT source-intent SHA to a single SQLite WAL/FULL ledger. A fresh REST depth snapshot after observed depth is mandatory, with as-of synchronized book validation; no partial-depth20 bypass.

An elapsed-time budget of 60–180 seconds is allowed for actual public research; it is not an indefinite background service. Reader receives frames cooperatively; a source writer refuses FIFO overflow, stale/delayed source, writer admission age > 5,000ms, unexpected EOF/idle and 4,998-frame budget exhaustion before duration completion. Queue capacity stays exactly 128. All stopped/failed session raw bytes remain private to the runner. There are no real orders, no credentials, no EventBus default runtime change, and no external API mutations.

## Commands

```bash
python -m pytest -q tests/test_phase1_single_socket_duration.py
python -m ci.phase1_single_socket_duration_trial --allow-public-market-data --acknowledge-not-24h-proof --duration-s 90 --output-dir /ABSOLUTE/FRESH/PRIVATE/DIR
python -m ci.phase1_single_socket_duration_audit --root /ABSOLUTE/FRESH/PRIVATE/DIR
```

The independent local SQLite inspector checks one closed source session, original and source-intent SHA agreement, frame/snapshot counts, bounded FIFO, duration report and fail-closed flags. It cannot independently revalidate exchange-side historical event completeness or HMAC downstream ACK: those are explicitly false in the output.

## Follow-on acceptance

Single-socket pilot is a bounded negative/positive experimental signal only. Institutional P1-05 requires an owner-fenced *persistent* service with durable recovery and journals beyond 5,000 records, explicit no-silent-loss reconnect gap provenance, continuous HMAC outbox/observer ACK, OS crash/power-loss testing, and approved 24h–72h host/network soak. No stage passes simply because the current test duration ended without network failure.
