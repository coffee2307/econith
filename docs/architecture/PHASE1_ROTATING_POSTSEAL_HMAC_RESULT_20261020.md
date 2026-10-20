# ECONITH Phase 1 — Post-seal HMAC handoff and crash-boundary offline results (2026-10-20)

**Acceptance scope: offline-only, source post-seal research.** Specialized HMAC receiver/derived/checkpoint fault gates **PASS**, provisional weighted Phase 1 exit still **81/100**. Phase 1 institutional approval is still OPEN; no live execution or default runtime migration.

## Immutable CI provenance

- Tested source commit `943dc6d83f6483526de54a1d1adfdd858951d097` on research branch `experiment/phase1-rotating-hmac-handoff-20261020`.
- [Push workflow 39364366717](https://github.com/coffee2307/econith/actions/runs/39364366717): **SUCCESS**, 5/5 pytest tests in 5.65s. [PR workflow 39364372314](https://github.com/coffee2307/econith/actions/runs/39364372314) **SUCCESS**, and prior rotating-source offline PR workflow [39364372336](https://github.com/coffee2307/econith/actions/runs/39364372336) **SUCCESS**.
- Initial uncorrected offline push run `39364051820` and PR run `39364076022` both **FAILED** (2/4 tests). One new handoff fixture sometimes ended in a valid fail-closed source quarantine rather than completing the test's assumed capture; the other failure was an expected operator-refusal message regex mismatch. Subsequent diagnostic test update preserved the negative evidence. Later 4/4 then 5/5 offline gates passed; the test suite is still bounded and requires further repeated stress runs.
- No public Spot network I/O in the HMAC post-seal workflow; source inputs are deterministic/injected fixtures. The earlier 120s real Binance single-reader and nine-rotating-journal proof belongs to **PR #32**, not this new test.

## What is actually proven

1. Previously sealed SIMULATION raw SQLite originals and atomic capture intents are verified before a projection handoff. Each source segment uses a unique `rec_rot_` research identity that the existing outbox and checkpoint validators enforce.
2. The existing source reconstruction builds market-eligible envelopes from original as-of receipts. It then invokes ECONITH's authenticated *separate child process* which owns its own receiver SQLite. The producer accepts the receiver-owned ACK, sends protected derived events to the primary and two named read-only subscribers and seals a source-order checkpoint hash chain. None of this places orders.
3. A new **read-only independent local inspector** checks every original/intent SHA, forward projection fingerprint, receiver-owned receipt identity and ordinal checkpoint SHA chain. It cross-reconciles the per-segment count and rejects incomplete or tampered evidence; it **cannot** independently re-attest the ephemeral HMAC wire secrets after process shutdown.
4. Offline Linux `os._exit(59)` is injected after a receiver-owned SQLite COMMIT and **before** the producer's outbox ACK. Global + segment owner markers persist across the process death, the run is not falsely completed, and an unreviewed resume is rejected.
5. In a separate explicitly flagged test *only*, the supervising test process verifies the child exited, reads its abandoned marker and receiver receipt, simulates a human-approved offline marker clearance, invokes existing idempotent source replay, preserves the original receiver-owned receipt exactly once and reconciles the missing producer checkpoint suffix. The partial global multi-segment handoff remains uncertified.
6. Tampering a completed checkpoint chain forces the independent post-seal audit to refuse the run.

## Limits and next gate

- **Post-seal only**: the market source socket has already closed before this HMAC stage starts. This test does not prove HMAC ACK age, subscriber lag, high-rate receiver backpressure or uninterrupted original→HMAC forwarding while nine SQLite journals rotate.
- Process death is not physical AC power loss or OS/storage corruption. A manually simulated operator recovery in tests is not an approved production/automated orphan-lock clearance workflow.
- HMAC challenge/authenticated IPC is exercised by the implementation. The independent on-disk report verifies observable provenance/hash parity but cannot itself independently re-attest the wire authentication key. It does not prove a broker or venue signature.
- No exactly-once external trade/order effects, verified historical Binance completeness, 24–72h soak, or safe real-capital execution. No new market data API keys or orders, no `main.py` default ingestion cutover.
- P1-04 14/15, P1-05 13/15, P1-06 4/10, P1-07 0/10, remaining gates 50/50. **81/100 provisional unchanged.**

**Next:** move the per-segment authenticated receiver handoff into a bounded asynchronous *co-running* projector that never blocks the one continuously attached source-reader; enforce FIFO 64/128 and strict 5s original admission while separately measuring HMAC and observer end-to-end ACK age. Then run a source+receiver crash at the actual in-session rollover boundary, audit private original/receipt/checkpoint parity and operator-reviewed recovery. No 24h service soak until these qualify.