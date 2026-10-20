# ECONITH Phase 1 — Batched HMAC receiver and replay safety: bounded cloud results (2026-10-20)

**Evidence verdict: bounded opt-in public Spot co-running PASS; institutional Phase 1 OPEN.** Frozen provisional engineering credit remains **81/100**. No authenticated exchange account keys, real orders, default runtime cutover, production deployment or capital authorization.

## Provenance and tested versions

- Prior single-envelope receiver baseline: [GitHub Actions 39369068048](https://github.com/coffee2307/econith/actions/runs/39369068048), immutable commit `105aeef5cacc9412eee8d43dfd5f99b207208b9f`, one 120.001s public BTCUSDT WSS, nine source journals, 3,946 WS frames, 3,352 HMAC receiver ACKs/checkpoints; max HMAC seal→checkpoint age 23,878.472ms; backlog peaked 2/2.
- Batch-8/reused receiver SQLite variant: [GitHub Actions 39377656117](https://github.com/coffee2307/econith/actions/runs/39377656117), immutable commit `6043ae9965a85981717f602c61e38c186bc0da29`, 2/2 ordered jobs SUCCESS. Offline scope/security/crash tests 19/19 PASS before push-only public read-only trial. The independent full SQLite audit returned `PASSED_BOUNDED_PUBLIC_SPOT_CORUN_HMAC_RESEARCH_ONLY`.
- Batch-8 aggregate artifact ID **12244096323**, `econith-public-corun-hmac-39377656117`, GitHub ZIP `sha256:0eb26382cfd19d6609314f647bbc881ebba8d497fb7e2c1b1c6bde571c98c136`, expires **2026-10-23T06:00:53Z**, 3,428 bytes. Raw original SQLite, trade/depth bodies, HMAC IPC secrets and session keys are never uploaded to the public artifact.
- A follow-up offline-only test expanded the Linux real hard-exit gate to verify idempotent **multi-receipt-batch COMMIT → producer ACK loss → operator-reviewed test-only recovery**. Earlier unfinished/failing fixture runs remain in Actions audit history. Current test-only source was subsequently corrected to give the synthetic buffered diff/REST snapshot sufficient observation time under contended runners. Do not attribute that correction to an improvement in the public feed.

## Observed comparison — distinct market workloads

| Exact measurement | Previous one-envelope pilot | Batch-8 + reused receiver SQLite |
|---|---:|---:|
| Pinned public WSS lifetime | 120.001s | 120.001s |
| Source WS frames | 3,946 | 2,312 |
| Rotating original SQLite journals | 9 | 6 |
| Read-only REST snapshots | 9 | 6 |
| Original + atomic capture-intent counts | 3,955 | 2,318 |
| Eligible HMAC projections = independent receiver ACK = forward checkpoint | 3,352 | 2,089 |
| Sealed segments ACKed during source session | 8 | 6 |
| Maximum segment seal → downstream checkpoint age | **23,878.472 ms** | **9,101.916 ms** |
| Sealed-journal downstream backlog peak / capacity | **2/2** | **1/2** |
| Source original COMMIT max age (5,000ms cap) | 226ms | 240ms |
| Upstream FIFO high watermark / cap | 6/64 | 6/64 |
| Peak aggregate journal footprint | 26,610,512 B | 21,079,437 B |
| Linux parent supervisor max RSS | 62,104 KiB | 64,504 KiB |
| Linux parent user/system CPU time | 40.831s / 14.158s | 16.513s / 9.046s |
| Real submitted orders | 0 | 0 |

**Interpretation:** the new sample has lower maximum seal-to-checkpoint delay (9.102s vs 23.878s) and did not fill the sealed-segment backlog (peak 1/2 vs 2/2). This is encouraging evidence about a bounded, resource-safe direction; **it is NOT a controlled equal-volume benchmark** because the Binance feed produced materially fewer frames and fewer journals in the second 120s window. Do not credit a guaranteed throughput multiplier, new uptime availability or Phase 1 exit points from a two-sample comparison.

Source first-receipt→COMMIT age histogram in batch-8 public trial: 2,308 originals within 0–100ms, 10 at 101–500ms, none at 501–1000ms, none at 1001–5000ms; max 240ms. Source originals **2,318** include 2,312 WS frames plus six REST snapshots. **2,089** eligible projections were individually ACKed and checkpointed; not every captured original is forward eligible. Neither source ledger proves unseen Binance venue emissions.

## Safety behavior added

- Existing authenticated persistent HMAC child receives up to **8** source-verified projection envelopes per signed ordered batch. The default remains **one envelope** for old callers. Receiver-owned SQLite COMMIT and receipt identity remain **per envelope**, not a collective best-effort ACK.
- Reusing the independent child SQLite connection is opt-in and restricted to a persistent receiver. Source original/capture-intent SHA, ordered outbox states, derived publisher and all three subscriber ledgers, hash-chained source checkpoints and CLI attestation remain separately verifiable.
- The Linux `os._exit(63)` test kills the producer after the independent receiver has committed a **batch of multiple receipts**, before producer ACK. Global and segment owner markers remain. The test manually checks that the process is dead, performs a **test-only** marker clearance, and uses the existing source-bound recovery to produce exactly one retained receiver-owned receipt identity per original and repair the checkpoint suffix. A partially interrupted *whole* source run is not automatically certified.
- Out-of-bounds batch size, missing public source consent, untrusted transport factory, failed book sync, missing ACK, FIFO overflow, old-or-orphan owner, stale original and incomplete checkpoint must fail-closed. No published raw originals or trade execution.

## Blocking institutional Phase 1 decisions

**P1-04 14/15:** independently reviewed host/power interruption and recovery evidence remain missing. **P1-05 13/15:** continuous service reconnect/catch-up, fault and load envelopes plus true operator-controlled 24h source+HMAC soak remain missing. **P1-06 4/10:** default `main.py` remains legacy and is not approved for verified-only cutover; execution isolation and mode/fallback AST + runtime checks require independent review. **P1-07 0/10:** multiasset, listing/delisting and historical vintage/long-run evidence matrix remain incomplete. Other frozen engineering gates 50/50; **81/100 provisional is not a certification and Phase 1 stays OPEN.**

**Next acceptance decision:** persist encrypted/private raw original and receiver evidence outside the ephemeral CI runner, qualify source+receiver under deliberately high sustained/replayed market rates and forced network/IPC loss, measure child-process CPU/RSS independently, require safe observed queue and lag margins, then run an operator-owned true 24–72h host trial. Plan the audited default runtime cutover as a separate P1-06 gate, never as an unreviewed side-effect of this bounded performance pilot.
