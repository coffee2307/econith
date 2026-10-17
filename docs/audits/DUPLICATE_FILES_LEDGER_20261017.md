# ECONITH — Exact duplicate and empty-file disposition ledger

**Phase 0 · 2026-10-17 · review only; NO automatic deletion.**

Source: recursive Git tree of draft PR #28 after removal of tracked
`node_modules/` and eight malformed vendor gitlinks. Matching Git **blob
SHA** establishes identical bytes; it does not prove duplicate operational
purpose or safe removal.

## Current identical nonempty Git-blob groups: 12

| Source A | Source B / related paths | Disposition |
| --- | --- | --- |
| `KHKT_Evaluation/experiments/EXP_001_baseline_vs_econith/results/plots/vol_forecast_sample.png` | `KHKT_Evaluation/experiments/EXP_001_baseline_vs_econith/results/vol_forecast_sample.png` | KEEP; inspect historical report references |
| `KHKT_Evaluation/experiments/EXP_006_Historical_Event_Replay/results/plots/event_mae.png` | `KHKT_Evaluation/final_report/figures/historical_event_mae.png` | KEEP; report figure |
| `KHKT_Evaluation/experiments/EXP_008_Calibration_Validation/results/plots/moment_l1.png` | `KHKT_Evaluation/final_report/figures/calibration_moment_l1.png` | KEEP; report figure |
| `KHKT_Evaluation/experiments/EXP_008_Calibration_Validation/results/plots/dist_interest_rate.png` | `KHKT_Evaluation/final_report/figures/calibration_dist_interest_rate.png` | KEEP; report figure |
| `KHKT_Evaluation/experiments/EXP_004_random_shock_control/results/control_mae.png` | `KHKT_Evaluation/experiments/EXP_004_random_shock_control/results/plots/control_mae.png` | KEEP; referenced scientific outputs must be verified before de-dup |
| `EXP_001`, `EXP_004`, `EXP_006`, `EXP_007`, `EXP_008` results `metrics.csv` | Their corresponding `raw_results.csv` (five separate duplicate groups) | KEEP; potential naming/provenance contracts, require consumer-reference scan and hash archive |
| `econith_quant/econith/templates/subtemplates/exchange_kucoin.j2` | `econith_quant/econith/templates/subtemplates/exchange_okex.j2` | KEEP; two vendor-specific Jinja template names are not interchangeable, separate GPL project |
| 11 `KHKT_Evaluation` experiment/final-report `__init__.py` files containing identical 24-byte text | Same exact byte content in 11 packages | KEEP; package import boundaries, not clutter |

**Key finding:** None of the 12 nonempty identical-content groups
is a directly duplicated first-party production service module. Runtime
overlap across `core`, `quant`, `world`, `collectors` and
`infrastructure` is **semantic overlap**, requiring lifecycle/event/import
tests, not SHA-based file deletion.

## Zero-byte tracked files (original baseline: 36)

- Intentional package markers (`__init__.py`) and occasional placeholders:
  **KEEP** until packaging and imports are proven after a migration.
- `econith_quant/bridge/{api_bridge,strategy_bridge}.py`,
  `econith_quant/execution/{executor,maker}.py`,
  `econith_quant/recovery/{checkpoint,recovery}.py`:
  **REVIEW** in the isolated GPL subtree, not native first-party runtime.
- Zero-byte Jinja template variants under
  `econith_quant/econith/templates/strategy_subtemplates/`:
  **KEEP** until template-loader behavior confirmed.
- `econith_quant/tests/testdata/UNITTEST_BTC-8m.feather`:
  **KEEP**; possibly an intentional empty/corrupted fixture.
- No bulk delete of 0-byte files or archives is authorized on evidence
  that size is zero alone.

## Operationally overlapping (NOT identical bytes)

| Overlap | Existing roles / treatment | Next verification |
| --- | --- | --- |
| `quant/ccxt_bridge.py`, `bridges/quant_bridge.py`, `econith_quant/bridge/exchange_bridge.py` | Exchange connection, event routing and opt-in legacy mock TWAP | Trace all `order.intent`, fills, Sentinel vetoes, capital credentials, double-writer risks |
| `infrastructure/storage/recorder.py`, `econith_quant/recovery/state.py` | **Distinct** SQLite state and trade lifecycle journals | Both now mounted to separate named persistent files; do not combine two SQL schemas blindly |
| `infrastructure/observability/alerts.py`, `core/observability/alerts.py` | Canonical implementation + compatibility re-export | KEEP; explicit public import contract |
| `collectors/market_coin/daemon.py`, `econith-vps-collector/daemon.py` | Two deployment-era collector implementations | Map ownership, brokers, schemas, schedules, active deployment before declaring either deprecated |
| `ai/simulator_engine/`, `econith/world/` | Legacy scenario engine + sovereign World model | Compare model clocks, physics invariants, event bus coupling |
| `training/evaluation/`, `econith/quant/`, `quant/pretrade_gate.py` | Statistical evaluation, offline research, order admission | Separate evidence-production from authority to activate a model |

## Non-destructive methodology for future moves

1. Freeze a baseline Git tree SHA and record current CI/E2E passes.
2. Run the first-party static inventory: `python -m ci.architecture_inventory
   --output phase0-architecture.json` (read-only). Download CI artifact.
3. Search consumer references beyond Python imports: Docker COPY/mount paths,
   CLI invocations, Next.js fetch URLs, SQL table names, artifact indexes and
   GitHub Actions entrypoints.
4. Assign one canonical owner per bounded context and keep compatibility
   imports while migrating its callers.
5. Move **one domain at a time**, run unit tests + actual Docker integration
   + mock exchange path; verify backup/recovery where data is involved.
6. Only delete when users and tests demonstrate zero active references, there
   is a rollback snapshot, and license/provenance requirements are satisfied.

**Nothing in this ledger authorizes deletion of scientific evidence,
licensed vendor files, persisted runtime state, backups or Git history.**
