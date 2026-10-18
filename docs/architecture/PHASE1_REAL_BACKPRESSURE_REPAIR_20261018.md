# Phase 1 — First real Binance backpressure failure and hot-path repair

## Operator evidence — preserved; no retrospective PASS

The operator's new 3×500-frame Diagnostic trial
`F:\\ECONITH-Phase1-Evidence\\20261018T134158Z_diagnostic_ddfa43b9`
recorded:

- `attempts=3`, `passed=2`, `failed=1`, exit code 2.
- Failed iteration 2 has **`SPOT_TRANSPORT_QUEUE_BACKPRESSURE`**,
  `source_queue_high_watermark=128`, i.e. a full 128-slot original
  ingress FIFO. This is a genuine observed source admission failure,
  not a generic broken Binance connection.
- 1 outage window eventually restored but 2 between-segment history
  boundaries remain UNKNOWN.
- RAM available minimum 2.845 GiB across 36 sampled host observations.
  This makes the old 0.37 GiB RAM admission issue an inadequate sole
  explanation; it does NOT prove CPU vs disk vs receiver latency.
- Two accepted segments: 1002 committed originals, 988 independent
  receiver ACKs; observed maximum receiver ACK latency 16 ms
  on successful segments only. Failed source is NOT certified.
- `no_live_trading=true`, zero exchange orders, continuous and gap-free
  market evidence remain explicitly FALSE.

Do not delete or rewrite the failed original evidence. Do not declare
Phase 1 acceptance from the restored next book.

## Code review finding: avoidable O(N²) historical verification on live path

Prior to the repair, **each eligible forward event** executed:
`outbox.items()` (full SQLite outbox load), then
`checkpoints.append(envelope,completed_outbox=...)`, which itself
called `verified_prefix(full_items)` TWICE to rescan the entire
hash-chain, re-hash all previous entries, and then verify it a third
time after insertion. This causes quadratic historical I/O and hash
work as a segment grows from 160 to 500 or 1000 frames. Since the
single source writer awaits `on_persisted` before dequeuing the next
original, slow historic rechecks create unnecessary ingestion
backpressure in a burst.

The new online path is:
1. Commit immutable original and atomic source intent.
2. Incrementally validate source/snapshot/trade ordering.
3. Persist exact outbox STAGED.
4. Obtain and independently compare signed receiver ACK.
5. Commit read-only derived observers and `DERIVED_ACKED` outbox.
6. Read **only the current `DERIVED_ACKED` event via primary-key index**
   and validate immutable projection identity and deterministic receiver
   receipt ID.
7. Append one SQLite hash-chained receipt after checking the latest
   checkpoint, monotonic capture ID/local arrival time and expected
   admitted-event ordinal.

This replaces the per-event O(N) outbox and hash-chain scans with O(1)
indexed database work and one immutable append; it does **NOT** remove
original-source COMMIT, independent receiver/derived ACK, fail-closed
bounded queue or deferred full independent proof.

**Critical acceptance boundary:** `shadow.finish()`, original
`verify_forward_capture_inventory()`, complete outbox
`verify_source()`, full `verified_prefix()`, independent HMAC
receiver database and three read-only observers are STILL compared on
sealed source completion, and the supervisor reopens them again.
Tampering of a PREVIOUS checkpoint while a session is open may no
longer be detected by every subsequent insertion, but the COMPLETE
hash/source mismatch is detected and fails the closing proof; no
corrupt session can be certified PASSED. Crash recovery retains the
original full-chain prefix repair/refusal path.

An offline regression on 39 WS frames with a REAL independent receiver
subprocess asserts exactly one indexed outbox query per accepted
event but no full outbox/prefix recheck per event, then verifies the
entire chain after finishing. Previously existing tests continue to
assert middle-row tamper rejection, corrupted digest refusal and
post-ACK pre-checkpoint crash suffix recovery.

## Follow-up operator acceptance

When the PR's exact final HEAD passes GitHub Actions, the user may
`git pull --ff-only` on the reviewed feature branch and run
`-Mode Doctor` then **one** `-Mode Diagnostic` on the same host
with at least 1.5 GiB available RAM. The operator script creates an
entirely new evidence directory, preserving both failed past runs.

A new Diagnostic PASS at 3×500 frames is a useful before/after
observation, **not a throughput guarantee or a successful 24-hour
soak**. If any source fails, inspect failure class, peak queue and
host RAM traces, identify remaining bottlenecks, and refuse the
hour-long/24h tests. If the bottleneck persists even on adequate RAM,
benchmark per-event SQLite/fsync and observer IPC cost independently
before spending money on GPU; CPU-only VPS may help test host limits.

Do not increase institutional Phase 1 80/100 estimate from an
unverified optimization. Never turn on REALITY mode/orders,
auto-switch to unreviewed legacy publishers, weaken source-time
checks or label UNKNOWN history intervals as recovered.
