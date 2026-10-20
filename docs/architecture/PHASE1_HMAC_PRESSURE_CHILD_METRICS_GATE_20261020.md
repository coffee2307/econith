# ECONITH Phase 1 — HMAC resource margins and overload (2026-10-20)

**Research-only, offline test gate. Frozen Phase 1 evidence: 81/100; not institutional acceptance.**

Previous 120-second Binance Public source and HMAC batch-8 pilot (PR #36) completed 2,089 receiver-owned acknowledgements and checkpoints, with a maximum 9.102-second journal-seal-to-checkpoint delay and sealed journal queue peak 1/2. The previous batch-1 pilot reached 23.878 seconds and queue 2/2. These source workloads differed substantially; no controlled speed multiplier is claimed.

This branch adds independently measured Linux subprocess CPU and resource evidence. The counters from RUSAGE_CHILDREN measure CPU for reaped HMAC receiver subprocesses and the **maximum RSS of a single child**, not a sum of process-tree RSS or the parent process. The co-running report now distinguishes waiting journals (queue capacity 2) from the one active worker; peak outstanding work is capped at 3.

A strictly OFFLINE-fixture-only delay injection deliberately slows receiver processing while the source continues closing journals. Overflow of the capacity-2 queue must result in explicit HMAC_SEGMENT_BACKPRESSURE, source FAIL, quarantine and no global PASS. Public run mode must reject this injection before opening sockets. Existing 64-frame source FIFO, 128-item capture FIFO, 5-second receipt-to-original COMMIT age limit, per-journal 25-second HMAC deadline, owner fences, independent original-to-checkpoint audit, and zero-order rule remain unchanged.

GitHub Actions repeats normal independent HMAC ACK/observer/checkpoint qualification, hard-exit receipt replay, tamper denial and deliberate overloaded source failure twice. These are finite offline stress tests; not sustained external venue data or 24-hour host evidence.

Still missing before Phase 1 exit: P1-04 machine-power-loss recovery review, P1-05 authentic reconnect/coverage and 24–72h operations, P1-06 independently approved default verified-only ingress and execution isolation, P1-07 broad asset, listing, timestamp-vintage and long-run quality evidence. No main merge or real trading.
