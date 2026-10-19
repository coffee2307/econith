# Phase 1 operator incident — real public Binance Hour trial stopped

This records the **operator-supplied** first real Hour trial; it is not
independent acceptance and does not replace the retained SQLite originals.
The exact trial root is:
`F:\\ECONITH-Phase1-Evidence\\20261018T131048Z_hour_47abc5e9`.

The original `report.json` says: 8 admitted, 1 PASSED, 6 FAILED,
1 STARTED when user stopped the job; 5 `SpotTransportError`,
1 `SpotCaptureError`; 2 outage windows, 1 still open,
7 unknown session boundaries, 1,001 original rows and
979 receiver ACKs from the accepted segment, queue peak 108 (out of
128) for recorded accepted sessions and 16ms max ACK latency
for an accepted session. Exit 130 is operator Ctrl+C.
Duration target 3600 seconds not reached; 0 orders.
This is **NOT a one-hour soak PASS**. No source coverage is certified.

The previous Quick trial passed three bounded sessions with a 31/128
queue peak and 63ms max accepted receiver ACK latency. The Hour preset
used 1,000 frames per bounded segment (vs 160 for Quick), and the
operator host had earlier reported only 0.37 GiB available RAM.
This strongly motivates investigation of sustained ingestion processing
and queue/backpressure, BUT neither aggregate queue nor exception class
alone proves the cause. In particular, the old recorder persisted only
exception class and only observed the queue watermark on PASSED attempts.

## Improvements committed after incident

- `spot_forward_failure_diagnostics.safe_failure_code`: sanitize and
  persist allowlisted Spot transport reasons, such as
  `SPOT_TRANSPORT_QUEUE_BACKPRESSURE`,
  `SPOT_TRANSPORT_CAPTURE_TIMEOUT`, `SPOT_TRANSPORT_WS_IDLE_TIMEOUT`
  or `SPOT_TRANSPORT_REST_SNAPSHOT_FAILED`, and known original
  `SpotCaptureError` source-time/data integrity reasons. Never store
  exception message, raw frame, URL or private machine paths.
- `spot_public_capture_adapter.py`: observe peak queue size while
  enqueuing; carry that peak on transport/capture failure, including
  queue overflow and absolute timeout.
- `spot_forward_supervisor.py`: durable failed-attempt row now saves
  sanitized reason + bounded queue peak and evidence storage bytes,
  not only a generic exception class.
- `ci.phase1_operator_lab`: derived `report.json` includes
  `failed_attempt_diagnostics` and sampled lowest physical RAM
  available during operator run; host resource JSONL is kept
  separate from venue/source evidence and **is observational only**.
- On hosts with under 1.5 GiB free RAM, refuse longer Diagnostic,
  Hour and Day trials before creating new evidence. Quick remains
  allowed with the previous minimum 0.25 GiB headroom check.
- The new `Diagnostic` entrypoint executes up to three 500-frame,
  120-second bounded segments with a 150 MB disk cap, avoiding a
  one-hour repeat just to find an actionable failure reason. It is
  still an explicitly user-controlled PUBLIC market-data only test,
  no keys, no orders, no production/default source change.

## Suggested operator procedure after updating branch

1. Keep the original failed Hour evidence directory unchanged.
2. Close memory-hungry applications, run `-Mode Doctor` and verify
   **at least 1.5 GiB RAM available** before any additional longer test.
3. Run `-Mode Diagnostic` **once**, in a NEW operator lab evidence root.
   It reports 3x500 frames with precise safe failure categories and
   queue/host RAM evidence; do not retry Hour until this is examined.
4. Share only `report.json` and `REPORT.md` (plus sanitized
   `stderr.log` if needed). A diagnostic PASS is not Phase 1 100%,
   nor proof that all 1,000-frame errors have disappeared.

Commands in an already-synced authorized Windows feature checkout:

```powershell
cd F:\\econith
git pull --ff-only
powershell -NoProfile -ExecutionPolicy Bypass -File .\\scripts\\phase1-test.ps1 -Mode Doctor
powershell -NoProfile -ExecutionPolicy Bypass -File .\\scripts\\phase1-test.ps1 -Mode Diagnostic
```

Do NOT delete failed data, override a source clock, weaken freshness
thresholds, silently switch providers, merge the Draft PR, enable
live exchange execution or use capital based on a successful
diagnostic test.
