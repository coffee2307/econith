# ECONITH P1-08 — Offline original-source Research Evidence Bridge

Research-only stacked Draft after PR #43. No main merge, order routing, exchange
connection, OpenAI API call, account authorization or new production API.

## What this stage actually implements

`ci.phase1_research_evidence_bridge` consumes one existing saved
`llm_advisory.json` or `finite_scorecard.json` and the corresponding
**private original** independently audited finite Spot archive(s). It runs
the existing original-source and point-in-time recomputation logic, not a
self-declared JSON hash check. It fails closed on altered source bytes,
missing quorum, wrong safety fields, incorrect future outcome or forbidden
tokens/tool flags. Successful checks produce one sanitized
`research_audit_receipt.json` in a fresh local directory (not committed to
Git and not uploaded by the dashboard).

The receipt contains only report SHA-256, evidence/score SHA-256, market
identity, risk veto and non-execution flags. Private archive paths, model
API keys, raw OHLCV and reports' free-form model rationales are not exported.

## Locally re-audit an AI panel

```bash
python -m ci.phase1_research_evidence_bridge \
  --allow-offline-original-audit \
  --kind panel \
  --report-dir /absolute/private/panel_report \
  --decision-root /absolute/private/decision_archive \
  --output-dir /absolute/new/panel_audit_receipt
```

## Locally re-audit a finite scorecard

```bash
python -m ci.phase1_research_evidence_bridge \
  --allow-offline-original-audit \
  --kind scorecard \
  --report-dir /absolute/private/scorecard_report \
  --decision-root /absolute/private/decision_archive \
  --followup-root /absolute/private/later_archive \
  --output-dir /absolute/new/scorecard_audit_receipt
```

Use absolute paths. Output directory must not exist; rerun with a *new*
directory to avoid overwriting prior evidence. CLI returns 0 and a
`PASSED_LOCAL_PYTHON_ORIGINAL_REAUDIT_ONLY` receipt for a successful
read-only audit; invalid evidence returns 2 and no receipt. The Python
auditor reads archives, makes no provider/network calls and places no orders.

To inspect in `/research`, choose the corresponding saved report first,
then choose the `research_audit_receipt.json`. The browser recomputes SHA-256
of exact report bytes with WebCrypto, verifies receipt's claimed report hash
and claimed evidence/score hash, and displays a clear *unsigned receipt*
warning. It does not upload either file.

## Trust model and limitations

**Critical:** Receipts are currently unsigned. A malicious person can
fabricate a report and matching unsigned receipt. Matching SHA-256 values
prove only consistency between supplied files; they do **not** authenticate
the receipt issuer, certify that Python ran, or prove exchange originality.
The reviewer must run the CLI and inspect its status against original private
archives on a trusted machine. Do not label any browser result as
exchange-verified or cryptographically source-certified.

Python source audit detects internal archive changes against source manifests,
but historical exchange-origin signatures, independently signed snapshots,
historic listing eligibility and economic trading alpha are not established.
The model-events metadata cannot prove real provider identity: this is a
source/quorum integrity audit, not independent OpenAI network attestation.
No realized PnL, execution feasibility, slippage, commissions, 24–72h soak,
API charges or default runtime changes are claimed.

Phase 1 remains **81/100 provisional OPEN**. Keep this PR Draft and stacked
on PR #43 until reviewed and CI passes.
