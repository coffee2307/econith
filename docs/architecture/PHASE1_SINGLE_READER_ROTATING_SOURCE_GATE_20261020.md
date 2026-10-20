# ECONITH Phase1 — Single continuous WS reader, bounded rotating original ledgers (2026-10-20)

**Status: draft offline-only engineering gate. Not a 24-hour soak and not continuous downstream HMAC service.** Under PR #31, a public single source ran 120.002s and owner fencing was independently audited. This iteration starts the next prerequisite: retain one read task attached to ONE source stream while each separate SQLite raw journal seals. It does not merge into main or change runtime defaults.

### Algorithm and invariants

- A single `DurationStream` reads a pinned public Binance Spot TLS WS connection until an operator-authorized monotonic bound. The sole source reader stamps each received original immediately and enqueues it in a **64-frame** non-blocking FIFO. It never opens a second source reader at a rollover. Full queue triggers terminal failure and source quarantine, never a drop-and-continue.
- Each new SQLite segment uses the existing audited `OptInSpotIngressAdapter` with a separate **128-event FIFO**, one fresh REST depth snapshot, atomic original+capture-intent WAL/FULL writes, strict source timestamp admission age <=5s, and synchronized-book replay before seal. `SourceStampedSpotFrame` is locally asserted, opt-in, checked against adapter admission time and not misrepresented as exchange signed data.
- Every segment persists `seal.json` containing its locally observed frame ordinal boundaries, one snapshot, source hashes chained through every original, and the resulting book status. The private original rows plus SHA intent remain in each source journal. An independent, read-only inspector reconciles these segment boundaries and global chain, verifies original bytes and SQLite health, and refuses an orphan marker.
- Only the one source owner's capture lock remains held across all journal rotations. The producer can remain attached to the socket while the writer seals/opens the next journal; backlog is bounded and must not silently accumulate past 64 events. If the producer stops before the monotonic target, REST bridging fails, or source cannot reconcile, the whole run is INVALID. No automatic reconnect.
- `original journal -> HMAC independent receiver -> two research subscribers -> signed checkpoint` remains a later separate gate. The rotation audit cannot certify those unimplemented downstream continuous operations.

### Security limits

- Local 64-event upstream FIFO plus separately bounded 128-event adapter FIFO; 5000ms source-to-original admission age; max 800 WS frames per journal; max 32 journals, 150MB source footprint, 180-second public-only experiment. This is not an indefinite 24/7 resident service.
- Journal seal and aggregate SHA chain are **local evidence** with no exchange signature. They do not certify true Binance trade history completeness, TLS peer authorization for injected fixtures, process-owner lease freshness across hosts, or power-loss durable cross-filesystem atomic commit.
- No API keys, private REST, exchange orders, default runtime cutover, CCXT, trading signal dispatch or public uploading of raw source SQLite. CI only uses offline fixture transports until passing explicit risk and resource gates.
- Freeze Phase1 **81/100** (P1-04 14/15, P1-05 13/15, P1-06 4/10, P1-07 0/10).