# ECONITH Phase 1 P1-15 — Independent host resource + HMAC watchdog

**2026-10-21 · Draft stacked on P1-14 PR #50 · default `main` untouched.**
Scope: P1-05 readiness for host soak. No always-on service has been launched,
no 24-hour source evidence, no production cutover or orders.

## The remaining blocker

The latest P1-12/P1-13 experiments confirm a finite public one-source Spot
research receiver with original→HMAC→durable checkpoint evidence. P1-14
quarantines hard-killed sessions and refuses orphan lock takeover. They
**do not** establish continuous 24h capacity, memory, disk and receiver
freshness observation or historical continuity through host loss.

The current source producer remains bounded (typically 300–1,000 frames
or at most 120 seconds), and a 24-hour monitor around multiple such sources
would be **false evidence** of a single uninterrupted stream.

## New watchdog

`ci.phase1_host_soak_watchdog` is an **independent, read-only, optional
operator sidecar**. It measures, once per configured interval:

- Available **physical/cgroup-constrained memory** (minimum 1.5GB);
  actual free disk (minimum 2GB); bounded evidence size (maximum 500MB).
- Exactly one unchanged local source session and its owner marker.
  A still-open session without a verified local marker is a refusal,
  **not** permission to start or recover a new owner.
- Local last-receipt freshness (max 30s), original count vs *atomic*
  capture-intent count, monotonic original/receiver/checkpoint/cursor
  observations across polls, and checkpoint/receiver/outbox ordering.
- Local primary source closed only after source cursor, HMAC receipts,
  derived-ACK and durable checkpoints have settled with exact counts.

Every sample is written to a fresh **private, append-only, fsynced JSONL**
observation trace with hash links and bounded size/count; a separate
`verify_trace` pass detects tampered or reordered records and progress
rollback. The host watcher never opens the public WSS, deletes the owner
marker, starts recovery, reconnects, writes to any producer evidence DB or
sends orders. All real WSS and HMAC output remains private on the runner.

The exact positive verdict is
`PASSED_FINITE_LOCAL_HOST_WATCH_ONLY_NOT_24H`. This means the observer
saw a live source at least once and later saw a clean locally coherent
closed source. It is **not** an institutional continuous market data exit.
If the source is unresponsive, stale, resource-starved, truncated,
internally inconsistent or monitor expires while the source remains
open, exit code 2 preserves evidence and **refuses acceptance**.
A sidecar cannot by itself stop an independently started publisher; run
it under a real process supervisor on a dedicated host where a failing
watchdog terminates/quarantines the corresponding experiment without
restarting or stealing an owner lock.

## Operator-only invocation

Start this as a separate process observing an explicitly authorized
single source. It does NOT launch the source:

```bash
python -m ci.phase1_host_soak_watchdog \
  --observe-existing-research \
  --acknowledge-not-24h-proof \
  --source-dir /ABSOLUTE/EXISTING_RESEARCH_SOURCE \
  --watch-dir /ABSOLUTE/FRESH/PRIVATE_WATCH \
  --poll-s 5 --max-watch-s 86400
```

The 24h **observer** upper limit allows measuring a future genuine
long-lived source once separately approved and built. Running the observer
alone for 86,400 seconds with a stopped source, multiple short source
sessions or no producer cannot pass the existing frozen 24h acceptance
criteria. The source and receiver must be independently qualified for long
runs and externally attested; this PR does NOT enlarge finite producer
contracts or validate arbitrary hours of trading.

## Same-HEAD CI pilot

One opt-in, public read-only **300-frame** in-session source/HMAC/checkpoint
experiment is run **concurrently** with this host sidecar. The sidecar
uses a separate output directory and closes only after the producer has
cleanly finalized. A separate checkpoint age re-audit and crash-ledger
triage are required. This is finite validation, not 24h. Only sanitized
summary/counts are uploaded for three days, never original SQLite, raw
market frames, source IDs, per-event timing traces or HMAC keys.

## Exit matrix unchanged

Phase 1 **81/100 provisional OPEN**. P1-04 14/15 awaits externally
verified host power-failure/takeover. P1-05 13/15 awaits sustained 24h
same-source verified ingress, HMAC, resource and recovery evidence. P1-06
4/10 default `legacy`; P1-07 0/10 multi-asset/vintage/72h acceptance.
No return, alpha or realized PnL claimed.
