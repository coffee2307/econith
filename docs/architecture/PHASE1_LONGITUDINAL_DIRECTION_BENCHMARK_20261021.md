# ECONITH P1-09 — Source-recomputed longitudinal direction-quality cohort

**Research-only stacked Draft** on PR #44. This phase adds a finite, read-only,
multi-decision evaluation layer. It does NOT add a new trading strategy, trading
API, market order, fee model, live execution, paid model call, or production cutover.

## External research references (inspiration; no wholesale integration)

- [TradingAgents v0.7.0](https://github.com/TauricResearch/TradingAgents):
  separate roles, point-in-time decision logging and forward outcome settlement,
  including a ticker/date grid. ECONITH deliberately keeps its smaller
  five-role, bounded research panel and avoids claiming a portfolio simulator.
- [Freqtrade lookahead-analysis](https://docs.freqtrade.io/en/stable/lookahead-analysis/):
  explicit detection of future data leakage; motivates repeated original re-audit
  and strict decision/follow-up capture timestamp ordering.
- [Microsoft Qlib](https://github.com/microsoft/qlib):
  out-of-sample signal evaluation and distinction between prediction metrics and
  portfolio strategy simulation.

## Mechanism

`ci.phase1_longitudinal_direction_cohort` reads one explicit manifest with
1–96 previously generated *finite scorecard* triples:

```json
{
  "entries": [
    {
      "report_dir": "/absolute/private/scorecard_001",
      "decision_root": "/absolute/private/capture_before_001",
      "followup_root": "/absolute/private/capture_after_001"
    }
  ]
}
```

This is only a **path schema example**, not real observations.

For every entry the program calls the existing P1-08 independent audit,
which opens original archives and recomputes the P1-07 scorecard. It checks the
exact original report-file SHA again, rejects duplicate asset/interval/decision
identities, duplicate asset/interval/future outcome candles and duplicated
report bytes. Any invalid row refuses the whole cohort, not just that row.
Results are sorted chronologically, not in the manifest-provided order.

It computes both conditional per-model accuracy and paired same-observation
accuracy. The paired comparison uses only decisions with **both** a scorable AI
direction and scorable two-closed-bar momentum baseline. Risk veto, Hold and
flat forward price are kept in population counts rather than silently scored
as correct predictions. The dashboard includes denominator and AI coverage,
so high accuracy on a few selective calls is visually distinguishable from
high call-rate performance. Aggregation is grouped by BTC/ETH/BNB and 1m/5m.

**Statistical boundaries:** Samples are not necessarily independent and may
share market shocks or model logic. Results are descriptive, no confidence
interval or p-value is claimed. An operator chooses which manifest entries to
include: **completeness, random sampling and selection bias are not audited**.
A paired direction advantage is not realized alpha or tradeable net return.
Do not optimize/rank production variants on the same tiny test cohort.

## CLI

```bash
python -m ci.phase1_longitudinal_direction_cohort \
  --allow-offline-research-cohort \
  --acknowledge-no-executed-pnl \
  --manifest /absolute/private/eval_manifest.json \
  --output-dir /absolute/new/local_quality_cohort_001
```

Output root must not already exist and must be outside the source/report and
manifest roots. Run on a trusted local machine with the original private
Spot source archives. A successful run writes
`research_quality_cohort.json`; an inconsistent or corrupted input returns
code 2 and no new report. No browser upload or network required.

Open `/research/overview` (linked from `/research`) and select that
aggregate JSON. The browser validates row/count consistency and renders
an AI versus baseline paired comparison, split by asset and interval, and
a per-decision table. **It cannot authenticate an unsigned JSON object.**
Only a trusted local source re-audit can establish local internal provenance,
not exchange-signed data authenticity.

## What is NOT certified

- No real-market sample, long-run sample adequacy or economic alpha proven.
- No broker ledger, orders, transaction costs, slippage, capital sizing,
  short feasibility, funding, exchange venue completeness, Sharpe, drawdown
  or realized PnL.
- No authentic Binance exchange signature, historical listing delisting
  vintage or independent proof of actual LLM provider identity.
- No random/complete sample guarantee, independence, significance or robust
  out-of-sample generalization.
- No production deployment, main merge or Phase 1 long soak.

Phase 1 remains **81/100 provisional OPEN**. CI uses deterministic synthetic
market originals only and cannot be interpreted as real trading observations.
