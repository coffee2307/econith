# Phase 1 — public Spot socket liveness and premature-close refusal

**Scope:** Opt-in, SIMULATION-only, read-only Binance Spot original-byte
capture feeding the reviewed independent HMAC research graph. No account
authentication, exchange orders or strategy execution. This change does
NOT make the source a continuous 24/7 data plane or establish complete
exchange market history.

## Why the previous public policy needed correction

The original `capture_open_stream()` intentionally treats a finite
injected offline iterator EOF as a successful bounded fixture capture
when snapshot + depth book are valid. Before this correction,
`capture_public_once()` reused that same EOF policy and could accept
an **unexpected remote WebSocket close** before reaching the requested
public observation frame quota. That was false-positive source liveness
evidence and could later be counted as a successful supervised research
attempt.

## New fail-closed public policy

`OptInSpotIngressAdapter.capture_open_stream()` now accepts:
- `require_frame_limit: bool=False` — intended for finite offline fixtures
  or explicitly bounded source simulation only.
- `idle_timeout_s: float|None=None` — optional per-next-original-frame
  idle deadline, measured with the asynchronous network iterator wait.
  The valid range is 0.1–120 seconds when configured.

`capture_public_once()` **always** passes
`require_frame_limit=True` and uses `idle_timeout_s=30` seconds
by default. A genuine public source is only eligible to close a
*bounded successful source window* once it has observed its configured
`max_frames` or else must fail closed. The reader never claims the
market stream was unbroken after that endpoint. An unplanned EOF raises
`WS_EARLY_EOF`; a silent/blocked frame-read raises
`WS_IDLE_TIMEOUT`. Both abort the session, preserve all previously
committed immutable original bytes, and do not authorize replay of
unknown observations. The existing bounded writer/backpressure,
atomic raw/source-intent recording, snapshot/diff book bridge, primary
and independently owned derived research receipts, and source/outbox
recovery rules remain enforced.

The forward research entry point, multi-session supervisor and
manual operator CLI propagate `public_ws_idle_timeout_s`/
`--ws-idle-timeout-s`. The offline fixture path retains its finite
EOF semantics; it is never a substitute for live transport evidence.

## Manual public source trial

On an authorized test host with a fresh absolute output directory:

```powershell
Set-Location F:\econith
python -m ci.phase1_forward_endurance_trial `
  --allow-public-market-data `
  --persistent-receiver `
  --output-dir "$env:TEMP\econith-ws-liveness-fresh-01" `
  --attempts 3 --max-frames 160 --timeout-s 45 `
  --ws-idle-timeout-s 15 --max-storage-mb 100
```

The CLI never starts on application default boot and needs no account
credentials. A source timeout/outage leaves durable failed evidence. A
successful finite quota does NOT claim market continuity before the
first observed frame or after the last.

## Acceptance evidence

New regression tests exercise premature source iterator EOF with a
valid original depth book, real blocked async-frame read after capture
COMMIT, closed invalid sessions, invalid idle policy and expected finite
fixture EOF. An existing public fake-TLS test now reaches its explicit
quota rather than relying on unexpected EOF as success.

**Still required to close institutional Phase 1:** a genuinely sustained
continuous verified source with explicit reconnect/depth reset and
operator-certified multi-day actual Binance public data-plane, throughput
and resource proofs; reviewed default source cutover; broad
multi-symbol/OHLCV and applicable derivative/macro source provenance.
PR #28 remains Draft; no production merge or live orders.
