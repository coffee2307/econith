# ECONITH Phase 1 — Public Binance Spot co-running HMAC bounded cloud result (2026-10-20)

**Verdict:** `PASSED_BOUNDED_PUBLIC_SPOT_CORUN_HMAC_RESEARCH_ONLY`. This is genuine bounded *public-source + downstream HMAC overlap* research evidence. **Phase 1 remains OPEN; weighted engineering exit score stays 81/100 provisional. No trade orders or default runtime cutover.**

## Pinned provenance

- Research source commit: `105aeef5cacc9412eee8d43dfd5f99b207208b9f` on branch `experiment/phase1-public-corun-bounded-20261020`, stacked Draft PR #35 on Draft PR #34.
- [GitHub Actions #39369068048](https://github.com/coffee2307/econith/actions/runs/39369068048): ordered `qualify-public-optin-offline` job ID `118452554347` **SUCCESS** (**12/12** offline consent/cross-ledger/crash tests), then `public-120s-corun-research` job ID `118452779457` **SUCCESS** on first attempt.
- Public aggregate-only artifact: `econith-public-corun-hmac-39369068048`, ID **12241180695**, archive SHA-256 `9f8cbb14cf43e738a4801570aca70da25a9006a51a34568855c542aca375b89d`, size 3731 B, expires **2026-10-23T04:24:11Z**. Contains aggregate source report, HMAC aggregate and independent audit only; **no raw SQLite originals, depth/trade bodies, secrets or HMAC keys**.
- Source-only and downstream read-only audit verdicts both separately required by the job, not inferred from job status alone.

## Real public observation — numeric evidence

| Indicator | Observed |
|---|---:|
| Pinned Binance Public TLS WebSocket source sessions | **1** |
| Monotonic observation time | **120.001s** |
| Received and SQLite-committed public WS frames | **3,946** |
| Sealed source SQLite journal segments | **9** |
| Read-only REST depth snapshots | **9** |
| SHA-rehashed raw originals = same-COMMIT intents | **3,955 = 3,955** |
| Eligible forwarded HMAC projections | **3,352** |
| Separate receiver-owned durable ACK receipts | **3,352** |
| Source-ordered durable forward checkpoints | **3,352** |
| Segments fully handed to HMAC before source stream stopped | **8** |
| Source FIFO peak vs limit | **6 / 64** |
| Sealed-segment worker backlog peak vs limit | **2 / 2** |
| Maximum source local first receipt → committed original age | **226 ms** (hard refusal above 5,000ms) |
| Original COMMIT age histogram (0–100, 101–500, 501–1000, 1001–5000ms) | **3,928 / 27 / 0 / 0** |
| Maximum per-segment seal → receiver/derived/checkpoint completion lag | **23,878.472 ms** (25,000ms experimental budget) |
| Maximum accumulated journal bytes reported | **26,610,512 B** (150MB cap) |
| Linux parent-process maximum RSS | **62,104 KiB** (~60.65 MiB) |
| Linux parent process user CPU | **40.831 s** |
| Linux parent process system CPU | **14.158 s** |
| Trade orders / broker API requests | **0** |

**Source/consumer semantics:** `3,955` originals include all `3,946` locally accepted WS messages plus nine REST order-book snapshots. **3,352** original-derived projections passed the eligibility reconstruction and were checked through independent HMAC receiver + three durable observer SQLite ledgers and checkpoint; nonforwarded originals are not automatically lost market messages. Independent inspection did not discover any original/intent hash or forward checkpoint/observer identity mismatch. It cannot verify *unseen* venue events.

## Important resource and timing warning

**HMAC segment backlog hit the configured capacity 2/2** (no recorded overflow), and **23.878472 seconds out of the permitted 25** were consumed by the slowest completed segment seal→ACK/checkpoint. This is a **narrow ~1.122-second margin**, not production stability headroom. Long-running host soak is NOT justified by one positive 120-second run. Tail latency, burst sustained event rates, worker memory growth, disk pressure, crash recovery and source connection loss must be load- and fault-tested.

The local commit histogram and 226ms maximum measure **local first observed receipt to original SQLite COMMIT**, not Binance event generation→client delivery or socket buffering. The 23.878s consumer figure covers **segment seal→all downstream receipts/checkpoint**, not each WS original's full event→HMAC ACK latency. The Linux `ru_maxrss` and user/system CPU values refer to the supervisor process and **do not include peak RSS/CPU of every independent receiver subprocess**. This is not an audited whole-host memory or CPU budget.

## Nonclaims and exit decision

- **Not real-time per-frame HMAC.** A sealed segment is forwarded after its source journal has been fsynced; its delivery overlaps **later** source segments. This is a meaningful live co-running pipeline, but not a continuously dispatched per-event market bus.
- **Not full Binance exchange-side completeness,** cryptographic venue-origin signature, externally replicable original dataset (private raw not uploaded), exchange-to-user network latency or all-subscriber EventBus transactional guarantee.
- **Not a 24/72-hour service,** not a hard electrical power-loss/restart certification, not distributed fencing, nor an approved default `main.py` verified-only cutover. No live trading permission.
- The root evidence is private to the ephemeral runner and expires unless the operator intentionally archives it using a reviewed secure process; public aggregate artifact cannot itself reproduce raw source SHA rechecks.
- P1-04 remains **14/15**, P1-05 **13/15**, P1-06 **4/10**, P1-07 **0/10**, other frozen gates **50/50**. Total **81/100 provisional; Phase 1 OPEN**.

## Next specific gate

Reproduce with deliberately adverse sustained source rates and delayed independent receiver, requiring HMAC segment backlog to remain below capacity and seal→ACK tail to remain comfortably within budget; instrument child-process peak resource use and checkpoint durability under kill/restart. Only after reproducible resource margin and raw evidence retention should a supervised longer 24h source+HMAC soak or verified-only runtime migration be discussed.
