# ECONITH Phase 1 — Single-socket ownership and terminal liveness qualification (2026-10-20)

**Research-only, staged public-data-only bounded pilot after deterministic offline qualification.** Source branch builds upon the verified 90.001s public WebSocket read and adds defensive tests before any 24h planning. Default `main.py` remains legacy. No 24h public continuity, HMAC downstream proof or real-order authorization. Frozen weighted engineering score stays **81/100 provisional**.

## Focused changes

1. The opt-in single-socket research runner creates a *new private evidence root* and acquires the repository's already-reviewed `exclusive_forward_owner(root, role='CAPTURE')` O_EXCL marker **before** clock preflight, opening the network, creating the original SQLite, and initial source intake. It holds the marker across all source processing and aggregate reporting. Graceful finish removes the marker; real OS hard-exit leaves it orphaned. This is not distributed fencing and does not guard an unrelated process deliberately ignoring the marker.
2. A second process cannot acquire either CAPTURE or STAGED_RECOVERY ownership while the marker exists. It may only be cleared using a separately reviewed operator recovery protocol; the runner does **not** auto-recover. Test forces `os._exit(47)` after source frames are started, verifies still-open SQLite source, a durable orphan marker and denial of silent takeover.
3. The 90-second monotonic deadline now requires a recent received WebSocket frame within 5s of completion. An idle socket that reaches the deadline without recent evidence is classified `WS_SILENT_AT_DEADLINE`; a successful sleep alone does not satisfy freshness. The existing 5s original receipt/commit admission-age gate remains unchanged.
4. The independent read-only SQLite inspector rejects a successful aggregate report with a live/orphaned owner marker, missing original/intent consistency, or terminal source silence above the 5s threshold.

## Explicit limits

- The OS marker is process-level advisory O_EXCL, not distributed transactional fencing or proof of physical disk flush under power loss. `os._exit` demonstrates process death only, not hardware power cut.
- This iteration does **not** implement durable journal rotation, a continuously operating service, multiple-source event-rate fairness, true losslessness against Binance exchange events, HMAC receiver ACK or independent research observers.
- No source-replay artifacts are uploaded to a public GitHub repository. The workflow runs deterministic offline fixtures and real local process hard exits first; only if those pass, a separate push-only cloud job makes a bounded read-only public Spot single-socket observation for 120 seconds. Pull-request workflows do not make public market requests. Any failure is retained and no automatic retry is performed.
- P1-04 remains 14/15, P1-05 13/15, P1-06 4/10, P1-07 0/10; Phase 1 exit remains OPEN.

## Offline qualification

```bash
python -m pytest -q --tb=short tests/test_phase1_single_socket_duration.py
```

Later gates: owner-reviewed orphan takeover workflow with independent source-byte replay and generation fencing; bounded durable segment rotation; continuous HMAC downstream admission; 24h–72h operator-controlled real host evidence with zero execution authority.
