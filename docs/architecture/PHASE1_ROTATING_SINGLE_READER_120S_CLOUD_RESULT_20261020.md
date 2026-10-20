# ECONITH Phase 1 — One continuous public Spot reader + 9 rotating original journals (2026-10-20)

**Engineering acceptance for this bounded research pilot: PASS. Institutional Phase 1: OPEN. Provisional frozen score: 81/100.** Evidence is from one 120.000s public Spot observation and independent local raw SQLite forensic reconciliation; it is not 24h service uptime, complete Binance venue history, persistent exchange-side losslessness, live HMAC downstream publication, or hardware power-loss recovery.

## Verified provenance

- Pinned public source commit `eb147a4939bb55c609e2c75ed33d075f13cb36b4`.
- [GitHub Actions run 39362285643](https://github.com/coffee2307/econith/actions/runs/39362285643): both ordered jobs **SUCCESS**, public research job `118431050581`, read-only Binance Spot GET snapshot and one pinned TLS WebSocket, no secrets/orders.
- Aggregate-only artifact `econith-phase1-rotating-source-39362285643` (ID `12237558875`), GitHub ZIP SHA-256 `9479697e8985b84471ae4328d49a1ee795fd0a2014c268a027b87f6f5f10bf8b`, expires `2026-10-23T03:06:18Z`. Raw original SQLite and depth/trade bodies were deliberately not uploaded.
- Follow-up offline pull-request CI [39362523818](https://github.com/coffee2307/econith/actions/runs/39362523818) SUCCESS: **9/9** including real `os._exit(49)` after first journal seal and denial of a silent owner takeover.

## Exact 120-second observation

| Indicator | Verified evidence |
|---|---:|
| Public Spot WebSocket connections owned | **1** |
| Continuously attached upstream reader tasks | **1** |
| Monotonic source observation duration | **120.000s** |
| Captured and stored WebSocket originals | **2,557** |
| Closed and sealed journal segments | **9** |
| Independently audited REST depth snapshots | **9** |
| SHA-verified original rows / capture intent rows | **2,566 / 2,566** |
| Missing frames **within this local reader's admitted sequence** | **0** |
| Source queue max | **6/64** |
| Adapter queue bound | **128** |
| Maximum allowed source/COMMIT original age | **5,000ms** |
| Terminal source silence | **57ms** |
| SHA mismatches or broken local chain | **0** |
| Snapshot/book state required per segment | **SYNCED** |
| Binance time RTT / measured offset estimate | **745ms / +292ms** |
| Broker/exchange order requests | **0** |

Local SHA chain terminus: `c987eee6be8e2217c0429463d3509634026e6b5c3bb962539d538879aa2dc6e5`. The independent CLI produced `PASSED_BOUNDED_LOCAL_SOURCE_ROTATION_AUDIT_ONLY` and read back every original row, its atomic intent, SQLite integrity, each snapshot count and segment seal. All 2,557 **locally read** source frames were durably captured on a single uninterrupted reader. This does not prove Binance sent exactly those frames or none were lost before receipt.

## Adversarial qualification and caveats

- Offline forced EOF, malformed combined-stream name, REST snapshot failure, source burst/overflow, unauthorized timestamp-stamp injection, tampered original bytes, absent opt-in, owner fencing and forced `os._exit(49)` at rollover all fail-closed or preserve forensic originals. The hard exit is OS-process death, **not electrical power loss**.
- First attempt at offline pull-request testing on commit `a4d9c3e024d526d199fbf902ba44f0c634083e7c` had a **1/8 intermittent fixture failure** in the tamper test's pre-tamper capture setup; recorded failed run `39362159055` is preserved. Subsequent preflight checks passed on both push and pull request. No failed result was erased or rewritten; this warrants repeated deterministic and resource-stressed offline gating.
- Cross-journal chain is produced by local code and independently recomputed against local original SQLite. It is **not a signed venue attestation** and does not attest physical durable sync across a whole-machine power cut.
- This work does **not** continuously feed the HMAC receiver/derived/named observer pipeline, so P1-05 institutional closure remains blocked. The segment-rotating data is source-only and the 120s run is insufficient for 24–72h resource survival claims.
- Public TLS provenance exists only for the pinned live capture factory; injected fixtures are never accepted as authentic Binance transport.
- No default `main.py` cutover, production deployment, merged PR, private credentials, order placement, look-ahead assumptions or raised Phase 1 exit score.

## Next integration gate

Introduce a continuous, durable source-to-independent-receiver HMAC projection with checkpoint/cursor parity across every rotation. Capture the original before dispatch; never silently skip a source-to-ACK gap. Inject process hard exits *after* a remote independent ACK but before checkpoint to validate exactly-once replay semantics. Only after that and owner-reviewed private evidence retention should a 24-hour service soak be considered.
