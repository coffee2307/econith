# ECONITH — Current domain map and controlled target layout

**Status:** Phase 0 audit / migration design, 2026-10-17. No wholesale Python module moves have been authorized by tests. Keep the live-capital execution lock.

This document distinguishes **logical ownership** from current physical paths.
Reorganizing names without resolving imports, API paths, tests, Docker COPY
instructions, model lineage and data migration would be cosmetic and unsafe.

## Existing runtime topology (observed)

```text
main.py (FastAPI / lifespan)
 ├── config/                Environment, settings, database
 ├── api/endpoints/         Control, vendor and social routes
 ├── core/                  EventBus, mode, controller, telemetry
 │   ├── cockpit/           Public read model
 │   ├── ingestion/         Macro intake
 │   └── observability/     Compatibility wrapper for infra alerts
 ├── bridges/               Quant/World/Social/vendor contracts
 ├── quant/                 CCXT adapter, payload, pretrade gate
 ├── ai/                    AI inference, agents and world simulators
 ├── econith/               Shared research namespace
 │   ├── quant/             New offline paper/data/strategy modules
 │   ├── world/             Sovereign World model
 │   └── data/              Source/provider abstractions
 ├── sentinel/              Capital/risk veto and circuit breakers
 ├── infrastructure/        WS, storage, indicator, observability services
 ├── collectors/            Modernized market/macro/tradfi collectors
 └── econith-vps-collector/ Separate legacy VPS collector app

training/                    Training, OOS deploy gate, backtest, RunPod jobs
scripts/                     Operational entrypoints and research runners
ci/                          Read-only source-tree hygiene guard (new)
.github/workflows/            GitHub Actions configuration
dashboard/                    Next.js/TypeScript UI, hooks and API bindings
econith_quant/                Separate large GPL-3.0 project/subtree
KHKT_Evaluation/              Original experimental evidence and final report
archive/vendors/              Third-party sparse-checkout manifest and local clones
docs/                         Current documentation and phase records
```

## Target *logical* ownership (without immediate breaking renames)

| Target bounded context | Current physical owner | Contract / notes |
| --- | --- | --- |
| `core` | `core/`, `config/` | EventBus, clock, read models; no broker orders from World |
| `quant/strategy` | `ai/agents/`, `econith/quant/alpha/`, `training/quant/` | Signal candidates only; promote only frozen OOS evidence |
| `quant/risk` | `sentinel/`, `quant/pretrade_gate.py` | Independent veto; cannot be overruled by LLM |
| `quant/execution` | `quant/ccxt_bridge.py`, `bridges/quant_bridge.py` | A single order authority with explicit paper/demo/live boundaries |
| `quant/research` | `econith/quant/`, `training/evaluation/` | Pure, reproducible offline backtests, no credential ingestion |
| `world` | `econith/world/`, `ai/simulator_engine/` | Differentiate canonical World model from legacy scenario simulation |
| `data/market` | `collectors/`, `econith-vps-collector/`, `infrastructure/websocket/` | Choose a single owner for public exchange ingestion after tests |
| `data/macro` | `core/ingestion/`, `collectors/macro_global/` | Point-in-time and publication-time guarantees |
| `ci` | `.github/workflows/`, `ci/`, `tests/` | GitHub Actions, unit/integration, hygiene, provenance, release gates |
| `apps/dashboard` | `dashboard/` | Preserve deployed Next.js path until Docker and API references migrate |
| `third_party/isolated` | `econith_quant/`, `archive/vendors/` | License/provenance/SBOM gate before movement |
| `research/evidence` | `KHKT_Evaluation/` | Immutable scientific experiment artifacts; NOT cleanup trash |

### Migration rules

1. **Physical directory renames happen one domain at a time** after a
   reverse-dependency scan (Python imports + JS imports + CLI + Dockerfile
   + Compose + Makefile + persisted paths). Introduce compatibility wrappers
   where necessary; do not mass-replace import paths blindly.
2. Preserve `core/`, `ai/`, `quant/`, `econith/`, `collectors/` and
   `dashboard/` until tests run on a full authorized local checkout.
3. Do not collapse the standalone `econith_quant/` project into native
   Quant. Its `pyproject.toml` states GPLv3, and dependencies differ from
   root `requirements.txt`. Root LICENSE states MIT: compliance review first.
4. Do not merge `core/observability/alerts.py` (a documented wrapper)
   with `infrastructure/observability/alerts.py` (the implementation):
   the duplication is intentional backward compatibility.
5. Do not delete `KHKT_Evaluation/results_archive_pre_EXP006` merely
   because older plots and CSVs match newer files; it is provenance.
6. Never rename the control-plane `REALITY` mode to mean permission
   to trade with capital. Live execution requires a separately authorized
   signed risk gate; remain research-only in this PR series.
7. Do not move binary wheels, test data or historical reports until
   references, licenses and reproducibility of their sources are audited.
8. Audit Docker COPY paths and file permissions on the target Windows
   checkout before a directory refactor is merged.

### Proposed incremental migration sequence

**A. Hygiene foundation (current PR):** untrack root `node_modules/`
while preserving `package-lock.json`; expand `.gitignore`; add
read-only `ci/repo_hygiene_check.py` and CI invocation; repair vendor
manifest drift; publish manifest of review-only duplicates.

**B. Legacy decisions (separate follow-up PRs):** choose canonical
collector, alert and execution contracts; map imports/call sites. Any
obsolete module must demonstrate zero active references and have a
replacement/rollback. Quarantine imported GPL-licensed subtrees.

**C. Structure and interfaces:** create tested `econith/quant/data`,
`econith/quant/portfolio`, `econith/quant/execution` boundary modules
by extracting logic without changing public behavior. Stop before merging
if CI, integration or deployment checks fail.

**D. Data/artifact storage:** move genuinely large reproducible data
outside Git only after a hashed artifact manifest, retention policy and
full recovery test. Keep historical experiment hashes and reports.

**E. API/UI migration:** only after stable core changes, align dashboard
layout/API contracts. World and Quant must remain isolated.

## Immediate blockers

- GitHub Actions runs fail with **zero jobs**, including previous PR
  revisions; no evidence that full `pytest`, Next.js build or Docker
  integration currently pass.
- Desktop Commander currently permits `F:/pham-store` and `F:/CHAM`,
  not `F:/econith`. No access-control setting has been changed.
- Core default auth is off for local/dev in `config/environment.py`,
  while production Compose explicitly enables auth. All order-bearing
  configurations still require a negative-path security audit.

This is a **design/reference map**, not a false claim that the entire
repository has already moved to this hierarchy.
