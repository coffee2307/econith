# ECONITH P1-10 — Risk-first research cost control + Phase 1 isolation qualification

Date 2026-10-21. **Draft research only**, stacked on PR #45. Unchanged
`main`, no default runtime cutover, no automatic model fees or orders.

## Why this is a substantial step, not a new performance claim

TradingAgents v0.7.0 emphasizes multi-role risk debate, point-in-time
decisions and settlement; v0.5.2 adds parallel research scheduling. Freqtrade
requires look-ahead checks; ECONITH already audits original as-of archived
market snapshots. This PR changes the **optional decision workflow** and
extends Phase 1's *fail-closed runtime-isolation* adversarial matrix.

### Optional risk-first panel

The pre-existing 5-role research panel always calls technical, bull, bear,
risk, portfolio. The new **separate** `ci.phase1_risk_first_research` starts
with risk on an independently audited original Spot source.

- If risk votes `VETO`, execute exactly **one** model call, no further
  analysts or portfolio. The result is `WITHHELD_RISK_FIRST_NO_FIVE_AGENT_QUORUM`
  with `agent_quorum_satisfied=false` and **no** portfolio rating or
  advisory direction. It is **not** an accepted five-role report. In
  particular, it must not enter `llm_advisory.json`, the historical
  scorecard or trading signals. Four of five **potential** requests avoided
  in this branch versus the legacy all-five protocol; no real measured dollar
  saving is claimed.
- If risk votes `ALLOW_RESEARCH`, execute technical, bull, bear, portfolio
  with bounded outputs; validate all five against the existing independently
  reaudited provenance and explicit role enum. The portfolio sees only
  **typed stances**, never free-form rationales that might contain injected
  instructions. Reassemble the completed five roles into the existing full
  panel schema, accepted by the original P1-08 auditor.
- Reject stale/forged original OHLCV before the first provider request.
  Enforce a single future decision deadline, max five calls, per-role
  input/output token limits, structured JSON, no tools/store/retries.
  Incomplete or late provider responses abort; partial spend may occur.
- **No real provider sessions in CI.** Mock tests verify the call budget,
  early veto, canonical full quorum and audit compatibility.
- The separate CLI requires operator-supplied `OPENAI_API_KEY` and three
  explicit switches; calling it with a key can incur real fees and is not
  authorized by these tests. We do not run that CLI with a real key.

Example operator-only, *not executed*:

```bash
python -m ci.phase1_risk_first_research \
  --allow-billed-llm-research \
  --acknowledge-usage-charges \
  --enable-risk-first-protocol \
  --source-root /private/verified_spot_capture \
  --output-dir /private/new_risk_first_result \
  --symbol BTCUSDT --interval 1m --model YOUR_ELIGIBLE_MODEL
```

### Phase 1 P1-06 isolated runtime adversarial gate

Tests exercise **both** `verified_only` (finite historical replay) and
`verified_forward_preview` (bounded public-only preview), with start
functions replaced by local stubs to prevent network, DB/CCXT or order work.

- Exactly one read-only `GET /api/v1/market/verified/status` route works;
  wrong verbs, legacy HTTP mutation/inspection endpoints and OpenAPI/docs
  return 404; two modes both report SIMULATION and zero submitted orders.
- Injected runtime-start fault fails closed and never falls back to legacy
  initialization. Missing opt-in credentials/consent fail preflight.
- Test asserts no legacy engine/streamer/CCXT/world components appear and
  no leftover runtime state after shutdown. The actual isolated lifespan in
  `main.py` now uses an inner `try/finally` to clear research components
  **even when the runtime's `close()` raises**; an injected hard shutdown
  exception is covered for both protected research modes.

This strengthens P1-06 **test evidence**, not the actual frozen P1-06
cutover requirement. The app's default is still `legacy`; it has NOT
been migrated or independently approved. The tests use a stub instead
of an end-to-end market socket / physical trading-device validation.

### Frozen institutional Phase 1 matrix

Read-only technical evidence credit remains fixed at **81/100 provisional**:
P1-01 20/20, P1-02 15/15, P1-03 15/15, P1-04 14/15,
P1-05 13/15, P1-06 4/10, P1-07 0/10.

The unresolved 19 points cannot be awarded for new synthetic tests:
- P1-04: independent host power-loss/takeover recovery evidence.
- P1-05: **continuous** protected source+HMAC ingest, physical 24h
  endurance, freshness/backpressure/resource qualification. Repeated
  segmented sessions are not uninterrupted data.
- P1-06: owner-reviewed default verified-only source migration,
  exhaustively isolated legacy publishers and execution.
- P1-07: historic venue listing/delisting vintages, independent validated
  multi-symbol OHLCV, 24–72h operator evidence and broader-source audit.

**Never claim verified exchange provenance, financial alpha, model superiority,
portfolio Sharpe/PnL, live trade authorization or full institutional signoff.**
Only operator-reviewed future acceptance may change frozen gate credits.

References:
- https://github.com/TauricResearch/TradingAgents
- https://docs.freqtrade.io/en/stable/lookahead-analysis/
- https://github.com/microsoft/qlib
- `docs/architecture/PHASE1_WEIGHTED_RESCORE_20261019.md`
- `docs/architecture/PHASE1_IMMUTABLE_EXIT_MATRIX_20261018.md`
