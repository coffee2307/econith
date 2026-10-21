# ECONITH P2-06 — Bounded supervised public Spot PAPER, not investment automation

**2026-10-21.** Building on P2-05 prospective bid/ask paper marks,
this increment adapts the **operational** ideas, not code or returns,
from Freqtrade's dry-run/protections and Hummingbot's controller
lifecycle. No trade API, no account key, no borrowed funds and
**no real orders** are present.

## Added

`scripts.paper_supervised_endurance` runs an opt-in foreground bounded
loop on ONE existing, virtual-only ECONITH research ledger.

- Exclusive *local* owner fence at `<ledger>.supervisor-owner` before
  calling any public markets, protecting against two supervisors
  writing the same SQLite ledger. If the process is abruptly killed,
  a stale owner marker is **never automatically reclaimed**: inspect
  the ledger + event trace, then manually clear only after verifying
  no competing owner. It is local file exclusion, not a distributed lease.
- Local resource budget on every iteration: >128MB free and <256MB
  SQLite+WAL+SHM by default. Every sample checks local clock
  monotonicity and a maximum runtime; a failed check is a **HALT**,
  not a fabricated continuous market history.
- Each iteration runs the existing P2-05 finite quote controller:
  public bid/ask mark → CLOSED Spot candle signal → independently
  LATER quote → simulated fill risk review → later post-fill mark →
  independent PnL and cash/position reconciliation.
- **No silent source gap**: the latest actually observed closed candle
  must be no older than its timeframe plus 60s relative to the host
  clock; P2-04 already refuses skipped/edited bars and expired
  pending trade intents. If the machine sleeps through a candle, the
  controller halts rather than inventing a retroactive trade.
- Every START, STEP, STOP/HALT event goes into a local
  `events.jsonl` using append/flush/fsync and SHA-256 link from
  previous event, including decision status, new sample ordinal,
  as-of actual closed candle, mark count and evidence identity.
  The entire chain is **independently re-read and rehashed**
  before a completed summary is written. This is local tamper detection,
  NOT externally signed exchange truth or power-failure proof.
- One `summary.json` preserves complete vs failure outcome,
  exact samples, reason, hash tail and only the most recent VERIFIED
  bid-marked virtual performance vs cash and cost-aware passive Spot.
  A loss is preserved; code PASS never means positive returns.
- Finite 1–1440 samples at 60–3600s polling with <=24h total
  configured budget, requires explicit `--acknowledge-foreground-only`.
  There is **no background/daemon process created**; loss of
  network, sleep, source mismatch, clock rollback, low disk or
  stale pending orders refuses / stops. **Even a completed 24h
  foreground duration alone does not prove 24h venue continuity.**

## Running a NEW virtual-only ledger (do not overwrite previous data)

```powershell
cd F:\econith-paper-p206
python -m econith.quant.forward_quote_dryrun --action init --ledger "F:\ECONITH-Paper-P206.sqlite" --symbol BTCUSDT --strategy trend_vol_v1 --interval 1h --capital 100 --simulation-only
python -m scripts.paper_supervised_endurance --ledger "F:\ECONITH-Paper-P206.sqlite" --evidence-dir "F:\ECONITH-P206-Evidence-001" --cycles 1 --poll-seconds 120 --simulation-only --allow-public-market-data --acknowledge-foreground-only
```

For an **operator-attended** multi-hour experiment, choose a NEW
evidence directory for each session, preserve the SAME SQLite ledger
and set `--cycles 24 --poll-seconds 3600` for approximately 23 hours
plus source fetch time. Keep the PC awake and terminal open. This does
NOT schedule future work, promise background delivery or certify 24h
data completeness. Do not reuse the P2-04 or P2-05 ledger unless
benchmark start mismatch is understood.

## Not yet implemented, or not proven

Independent exchange-attested quote timestamps, venue trade volume,
partial fills, market impact, source event completeness, 24h uninterrupted
execution, fault-tolerant cloud deployment, independently audited
long-horizon realized return, and profitable predictions.
Also, DOG Bitcoin Rune is not DOGE; no DOG trading adapter, fee
schedule or liquidity verification exists.

Any source or host failure must be counted as incomplete evidence, never
filled by interpolated profit. User's original `F:\econith` repo and
GitHub main remain unchanged; branch stays Draft. Original Phase 1
engine credit 81/100 provisional OPEN.
