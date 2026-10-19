# Phase 1 — Real Hour operator incident: repeated Binance HTTPS clock-preflight failures

## Preserved real operator evidence (not a CI/fixture result)

Local trial `F:\\ECONITH-Phase1-Evidence\\20261018T152202Z_hour_7f2e9056`.
Operator confirmed branch/commit `7f3300ec976da5d3b4174f82a560a07a14a65e28`,
then Doctor: Windows 10, 4 logical CPUs, Python 3.12.10,
2.98 GiB RAM available before starting, 71.37 GiB free disk, packages
present, SIMULATION-only.

The **1h watch failed** and was explicitly interrupted by the operator.
The durable `report.json`/recomputed Report shows:

- `attempts=12`, `passed=1`, `failed=10`, `incomplete=1`;
  `child_process_exit_code=130`, `completion_code=null`,
  `duration_target_s=3600`; verdict
  `UNRESOLVED_INTERRUPTED_SOURCE`.
- **7** `SPOT_TRANSPORT_CLOCK_PREFLIGHT_CONNECT_TIMEOUT` on
  iterations 2,3,4,6,7,8,9, all `source_queue_high_watermark=0`.
  These failures occurred *before* admitting raw market data and cannot
  be remedied by increasing queue capacity, changing checkpoint I/O
  or buying RAM/GPU.
- Other failed attempts: 5 `TimeoutError` (queue peak 0), 10
  `RuntimeError` (queue peak 0); exact root causes cannot be established
  from sanitized exception classes alone. The latter may be clock
  admissibility/reliability, but that is NOT proven.
- Iteration 11 `SPOT_TRANSPORT_QUEUE_BACKPRESSURE`, peak **128/128**.
  This is a distinct source performance incident; the earlier 3x500
  Diagnostic PASS does not guarantee 1000-frame/hour throughput.
- 1 accepted segment: 1001 source originals, 993 independent receiver
  ACKs, max observed receiver ACK latency 16ms in passed segment only.
  2.439 GiB minimum available RAM over 72 host samples. No
  evidence of systemwide RAM exhaustion, but no per-handler performance
  profiling exists yet.
- `open_outages=1`, `outage_windows=1`,
  `unknown_session_boundaries=11`, `gap_free_history_verified=false`,
  `continuous_market_feed_certified=false`, `orders_submitted=0`.
  The 11 unknown segment boundaries cannot be filled by a newer
  REST book snapshot. Do not rewrite evidence or certify Hour.

## Root operational risk / conservative mitigation

The original `Hour` profile budget is 2048 attempts; with only an
8-second public REST clock timeout it could attempt hundreds of
failed HTTPS preflights during a transient or persistent network outage.
Its existing failure ledger correctly records each as FAILED but an
operator watching a large error count might expect automatic
circuit-breaking instead of immediate repeated attempts.

This incident demonstrates that the same exact public clock HTTPS
request can time out while an earlier bounded WSS/REST session passed.
One successful TCP443 probe does not prove the next HTTPS request will
succeed. No evidence indicates the GitHub repository, Binance public
Spot account API or market engine itself caused the network issue.

The operator Hour/Day launch now explicitly enables
`--max-consecutive-clock-failures 3` (only allowed on finite
watch-duration research). Three **consecutive preflight-classified**
`SPOT_TRANSPORT_CLOCK_PREFLIGHT_*` failures, with no accepted or
other failed attempt between, trigger a **durable**
`completion_code=NETWORK_PREFLIGHT_CIRCUIT_OPEN`, stop further
attempts and keep the failed rows, outage window and UNKNOWN boundaries
unchanged. The process returns nonzero because the watch target has
NOT been reached and the failed source remains failed.
A recovered accepted segment resets the counter; an unrelated source
error also resets it. All interruption markers remain distinct.

This circuit breaker is a load/safety limit, NOT a connectivity fix,
not evidence of good exchange/network health and not a relaxation of
`source.max_age_ms=10000`, `future_skew=1000`, the 128-slot bounded
ingress FIFO or durable identity/ACK/append-only hash checks.
The CLI default is unchanged for other callers; only the reviewed
operator Hour and Day profiles provide the 3-failure cap.

## Next operator procedure

Do not immediately re-run Hour. First perform a small read-only,
timed HTTPS `/api/v3/time` connectivity probe against
`https://data-api.binance.vision/api/v3/time`, across at least several
trials. If repeated timeouts persist, investigate local ISP/router/
DNS/TLS/proxy and test permitted alternative network paths, or
consider a CPU-only VPS in an authorized region for reproducible
public-data tests. No VPN/geographic access bypass or exchange keys.
A raw `Test-NetConnection :443` success is necessary but not
sufficient. Never disable TLS verification.

Once local clock and network preflights are reproducibly stable,
compare the 1000-frame queue under a separate, fresh source diagnostic
and add latency instrumentation around durable writes/derived
observers/IPC if backpressure persists. Do not increase the
buffer cap just to mask capture loss.

An Hour WATCH duration target does not certify complete continuous
Binance venue history: every independently sealed segment retains
unobserved gaps by design. 24h soak and default verified runtime
cutover remain not accepted. Draft PR only, no merge, no trading.
Phase 1 engineering estimate stays ~80%, NOT institutional 100%.
