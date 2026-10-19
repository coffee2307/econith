# Phase 1 — durable research indicator consumer receipts and deterministic replay

**Review status:** Draft PR #28, SIMULATION only. This document covers a
bounded research data path. It does not enable live trading, production deploy,
main merge, or a 24/7 continuous verified Binance feed.

## Problem: observing an EventBus callback was not a durable consumer ACK

Previously the bounded public microbatch collector and isolated historical
verified-only ASGI runtime checked the counts of derived `md.ticker`,
`indicator.volume_delta`, and `indicator.obi` callbacks. Even after the
in-process EventBus completion barrier was introduced, a finished callback
count was not independent durable evidence of derived consumer processing,
and a process restart could not distinguish a committed result from a failed
delivery.

## New: source-bound SQLite transaction for this one observer

`infrastructure/market_derived_research_receipts.py` provides an opt-in
`OfflineDerivedResearchReceiptLedger` with SQLite WAL/`synchronous=FULL`.
Each research indicator observation is identified by:
- the original closed Spot session ID `rec_...`;
- the exact derived consumer topic;
- a 1-based ordinal per topic and session;
- an immutable source record identifier tied to that session, and original
  `available_at_ms` local receipt time;
- canonical JSON of the **actual** resulting derived indicator event and a
  SHA-256 digest.

The observer stores **both** the durable result receipt and its cursor within
the same `BEGIN IMMEDIATE` transaction. An injected crash before COMMIT
leaves neither the receipt nor the cursor. On restart, a byte-identical replay
returns the previously committed receipt and does not duplicate the observer
projection. Mismatched same-ordinal replay, repeated source IDs at a new
ordinal, ordinal gaps, corrupted stored JSON or a broken cursor are refused.

The protected `MarketDataPipeline` propagates the exact source coordinates
from guarded raw events to the three derived topics. Ordinary legacy
`MarketDataPipeline` events still have no such coordinates and retain the
existing legacy payload shape. Partial claimed verified coordinates fail closed.

The public multi-session research worker and the historical `main.py`
verified-only branch both register a **single durable research observer**
for those derived topics. Before reporting PASS, they:
1. Check the existing original-byte capture, verified market journal,
   provenance manifest and independent HMAC-authenticated *source projection*
   receiver-owned receipts.
2. Require successful coroutine completion of raw and derived EventBus
   handlers and a completely drained queue.
3. Reopen and revalidate the durable derived observer receipt chain for all
   three topics and require exact counts: one ticker and one volume-delta
   receipt per admitted trade, plus one OBI receipt per final synced book.

**Precise scope:** These receipts belong to the *research indicator
observer* whose projection is the derived event payload. They do **NOT**
establish durable ACK for every possible EventBus consumer, trading strategy,
dashboard, remote broker or side effect. The derived receipt SQLite ledger
is not atomically committed together with the independent source-projection
receiver DB, and external effects remain prohibited. An event that failed
before recording its derived receipt must be replayed deterministically
from its sealed original source; this is not automatic general-purpose
24/7 broker replay.

## Deterministic volume delta as-of replay

The legacy `VolumeDeltaTracker` uses `time.monotonic()`, correctly for
live demo frames but incorrectly for replayable historical observations:
a 10-second historical rolling window could depend on the computer's replay
speed. **Only in verified research** the pipeline now uses original
`verified_source_available_at_ms / 1000` as its indicator clock.
Replaying the same saved source with a delay between processing calls yields
the same volume-delta series. This is not look-ahead: no future source
timestamp is consulted.

Every new sealed public Spot microbatch calls
`MarketDataPipeline.reset_verified_research_session()` **after** the previous
subscriber graph drained. This prevents any rolling volume or last-price
state from crossing an unobserved network gap. The default unverified legacy
pipeline is unchanged.

## Acceptance and limits

The local/offline tests cover cursor persistence across Python object restart,
injected failure before COMMIT, already-committed-but-not-ACKed replay,
poisoned same-ordinal and reused-source conflicts, corrupted projection
fingerprints, invalid topics/ordinals, SIMULATION-only mode, derived source
metadata propagation, source-clock deterministic rolling windows, and reset
across session gaps. The existing research ASGI and finite public source
E2E fixture tests now reopen the SQLite derived ledger and validate its
per-topic receipt counts independently of in-memory counter values.

These safeguards advance crash-safe offline research delivery. They do
**not** certify continuous live low-latency admission, exchange history
completeness during outages, durable ACK for arbitrary independently
deployed consumers, production availability or profitable trading.

### Optional safe manual verification

On the reviewed exact PR head and with your isolated Windows Python venv:

```powershell
& $py -m pytest `
    tests/test_market_derived_research_receipts.py `
    tests/test_spot_public_verified_microbatch.py `
    tests/test_spot_verified_research_runtime.py -q
```

Do not expose the raw capture SQLite, API keys, or user-account access.
The existing opt-in public microbatch read-only CLI retains all its
`--allow-public-market-data` constraints and zero-order guarantee.
