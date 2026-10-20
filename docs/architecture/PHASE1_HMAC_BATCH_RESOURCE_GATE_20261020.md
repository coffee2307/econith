# ECONITH Phase 1 — Bounded HMAC receiver batch/connection-reuse qualification (2026-10-20)

**Draft experimental engineering gate; SIMULATION-only, read-only public Spot market data, no orders, no merge.** P1-05 remained 13/15 and total Phase 1 81/100 after the real 120.001s HMAC co-running campaign on pinned commit `105aeef5`. The prior run read 3,946 frames and completed 3,352 independently ACKed projections across 9 source journals; the peak sealed-journal worker backlog was **2/2** and largest seal-to-checkpoint time **23,878.472ms out of 25,000ms**. A PASS with such little headroom is not an institutional 24h soak acceptance.

## Change in this branch

- The existing authenticated independent `PersistentHmacResearchReceiver.accept_batch` protocol already supports up to **32** entries per authenticated sequence, with individual source-bound ACK receipts and independent SQLite COMMIT for every entry. This gate opts into a much smaller **1–8 envelope batch**, default **1**, and optionally reuses the receiver-child SQLite connection. Legacy recovery callers preserve old behavior by default.
- Rebuild + stage each eligible original as before. Before delivering a batch, verify **every** original against its indexed immutable SQLite source. The HMAC child replies with all receipt IDs or the operation fails closed. The producer still transitions **each envelope's ACK individually**, emits individual primary/audit/monitor derived projections and reconciles each checkpoint. An uncertain partial batch COMMIT is not globally accepted; operator-owned source replay must reconcile exact idempotent receiver receipts.
- The co-running worker can request `receiver_batch_size=8`, `reuse_receiver_connection=True`, without modifying source reader, original SQLite, FIFO **64**, writer FIFO **128**, sealed-segment queue **2**, source-to-original deadline **5,000ms**, segment seal-to-checkpoint deadline **25,000ms**, or default runtime.
- The new public research CLI keeps three independent explicit opt-ins and adds `--receiver-batch-size 8 --reuse-receiver-connection`. Public network work is **after** offline source/derived/crash tests on GitHub push events only. PR events run offline only. Both still use exact commit tarballs.
- Offline gates include 8-entry HMAC batch with an actual separate receiver and three durable observers, invalid batch refusal, and a real Linux `os._exit` after receiver COMMIT but before producer ACK **with outstanding batch receipts**. An independent SQLite auditor must validate every raw/intent/outbox/receipt/observer/checkpoint source coordinate.
- No raw Spot bodies, SQLite databases, credentials or ephemeral HMAC secrets are placed in public artifacts, only aggregate reports and independent audit JSON. Raw forensic retention for a future 24-hour acceptance remains separately unfulfilled.

## What would count as improvement

Compare *observed* bounded real public runs on equal duration and segment frame limits, not a benchmark guarantee under identical market conditions. Require downstream queue **below its maximum** at peak, a materially safer tail vs 25s and **no lost admitted originals**. A negative result remains FAILED/QUARANTINED, not new Phase 1 credit. Also check child process resource usage and crash recovery before concluding sustainable operation.

## Remaining frozen exit gates

P1-04 external power/recovery 14/15; P1-05 continuous reconnect/soak 13/15; P1-06 independently reviewed default verified-only source cutover 4/10; P1-07 multiasset and true long-duration/coverage 0/10. Total **81/100 provisional**, institutional Phase 1 remains **OPEN**. Never claim Phase 1 is 100% based on a finite research pilot or numerous unit tests.
