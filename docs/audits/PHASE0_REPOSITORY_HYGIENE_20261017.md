# Phase 0 repository hygiene audit — 2026-10-17

**Source:** GitHub tree for `coffee2307/econith`; starting point was
PR #21 commit `6ed8775a40cdbc6b8cc5bc7fd008588eabb18bd8`. The
cleanup branch is `chore/phase0-inventory-hygiene-20261017`.
All removal described here is reversible through Git history, and has
not reached `main`.

## Measurements

| Metric | PR #21 starting tree | After verified safe cleanup |
|---|---:|---:|
| Tracked blobs | 3,911 | 1,320 after adding new audit/CI/docs files |
| Root `node_modules/` tracked blobs | **2,594** | **0** |
| Root `node_modules/` tracked bytes | **5,749,293** | **0** |
| Root JS package and lockfiles | present | preserved |
| Independent `econith_quant/` tracked blobs | 776 | 776 |
| Independent `econith_quant/` tracked bytes | 59,749,852 | unchanged |
| Original empty files | 36 | unchanged |
| Nonempty duplicate-content groups after removing root `node_modules/` | — | 12 at first post-cleanup measurement |
| Forbidden generated/sensitive paths on final reviewed tree | — | **0** by direct tree-path classification |

The exact tree-diff audit confirmed **all 2,594 deleted blob paths
were beneath root `node_modules/`** and every other pre-existing file
had an unchanged Git blob SHA. Removing vendored installs from the
index does not imply deleting `node_modules/` on the operator's PC;
new rules ignore locally rebuilt dependencies. Use `npm ci` at repo
root to restore root JS dependencies from `package-lock.json`.

## Actions actually performed in this branch

1. Removed only root `node_modules/` from Git history's *current tree*
   in a single audited commit (not a Git history rewrite).
2. Updated root `.gitignore` for nested `node_modules`, build,
   test and local editor artifacts; kept tracked research data unchanged.
3. Repaired `scripts/setup_vendors.sh` manifest resolution from a
   nonexistent `vendors/manifest.json` to tracked
   `archive/vendors/manifest.json`. Replaced automatic
   `rm -rf` overwrite of a non-Git destination with an explicit failure.
   Added full 40-hex SHA pin validation. The script was **not run**,
   no external vendor folder was created or deleted.
4. Updated documentation of the canonical vendor manifest path.
5. Added `ci/repo_hygiene_check.py` (read-only inventory) and
   `tests/test_repo_hygiene.py`; added a guard in
   `.github/workflows/ci.yml`. GitHub Actions previously failed
   before any job began; the full CI is **not** yet verified.
6. Wrote `docs/architecture/REPOSITORY_DOMAIN_MAP.md` mapping
   Quant, World, Core, Data, AI, CI, Dashboard, third-party libraries
   and scientific evidence without wholesale physical renames.

## Explicit keep / later-review matrix

| Item | Evidence | Decision |
|---|---|---|
| `node_modules/` | Root `package.json` + `package-lock.json`; npm-install output | **Untrack now**, 2,594 paths |
| `econith_quant/` | Different embedded project with GPLv3 license, `pyproject.toml`, tests/binary wheels | **KEEP**, legal/provenance review before relocation |
| `KHKT_Evaluation/results_archive_pre_EXP006/` | Prior experiment outputs and provenance | **KEEP**; not a disposable backup |
| `KHKT_Evaluation/experiments/*/results/plots/` | Some exact duplicate figures in experiment result and final-report locations | **KEEP** until reference+evidence audit |
| `econith_quant/bridge/api_bridge.py` and `strategy_bridge.py` | Empty files | REVIEW; not sufficient grounds to delete |
| `econith_quant/execution/{executor,maker}.py` | Empty files | REVIEW, possible namespace/stub imports |
| `econith_quant/recovery/{checkpoint,recovery}.py` | Empty files | REVIEW, must check imports and interfaces |
| `econith_quant/econith/templates/*minimal.j2` | Some zero-byte templates | REVIEW; template loaders may expect these |
| `econith_quant/tests/testdata/UNITTEST_BTC-8m.feather` | Empty test fixture | REVIEW; do not silently remove failing-data case |
| Empty Python `__init__.py` | Valid namespace-package markers | **KEEP** |
| `core/observability/alerts.py` vs `infrastructure/observability/alerts.py` | First is documented compatibility import wrapper | **KEEP**; not an identical implementation |
| `collectors/market_coin/daemon.py` and `econith-vps-collector/daemon.py` | Both consume market streams, but implement different lifecycle/retention behaviors | MERGE ONLY after provenance+interface tests |
| `quant/ccxt_bridge.py` vs `econith_quant/bridge/*` | Separate execution and advisory implementation paths | HIGH-PRIORITY AUTHORITY / event-route audit |
| Root `*.pdf`, `*.docx` reports | Scientific/historical and operator documents | KEEP pending reference review |
| `archive/vendors/manifest.json` | Explicit vendor provenance and pin table | KEEP; now used by setup script |

**Why not delete all similar files:** equality of contents proves byte duplication,
not equality of purpose. A CSV or PNG copied into a final report can
be essential to reproducing the original KHKT result. A zero-byte
`__init__.py` can be mandatory to stable Python import behavior.

## Risk register

- **P0 — Order authority:** current platform spans
  `core/system_controller.py`, `core/mode.py`, `bridges/quant_bridge.py`,
  `quant/ccxt_bridge.py`, `econith_quant/bridge/ai_bridge.py`.
  Trace all producers of `order.intent` and permission boundaries.
  Never enable capital-bearing orders from a refactor.
- **P0 — CI:** latest observed GitHub Actions run for predecessor PR
  reported `failure` with **zero jobs**, before this cleanup. The
  source of the failure is unproven. No clean full-repo test exists yet.
- **P0 — Computer permission:** Desktop Commander connected but
  `F:/econith` is *not* an allowed directory. Access settings were
  not widened; no local files were altered.
- **P1 — Licensing:** root MIT, embedded `econith_quant/` GPLv3.
  Its declared NumPy dependency (>2) also differs from core pinned
  NumPy 1.26.4, so do not install them blindly in one environment.
- **P1 — Configuration:** `config/environment.py` defaults
  `API_AUTH_ENABLED=false` for development and defaults the CCXT
  market type to futures; production Compose explicitly enables API
  authentication and sets execution environment to demo. Audit all
  entrypoints and runtime override semantics before any live gateway.
- **P1 — Vendor setup:** manifest location drift corrected without
  executing external pull; keep advisory-only vendor contracts.
- **P2 — Layout:** current paths reflect historical migrations. Adopt
  the domain map incrementally; move imports only with integration
  coverage and rollback.

## Verification and limitations

- Direct Git tree comparison verified all non-node_modules blob SHAs
  were preserved in the first cleanup commit.
- Latest tree has 1,320 tracked blobs, zero root `node_modules/` blobs,
  zero disallowed paths by the hygiene guard's path classification.
- Two pure Python hygiene checks passed on the connected device by
  loading GitHub-fetched source **in memory**. Two temporary-directory
  tests remain for normal pytest CI and were not executed remotely.
- Script shell syntax, vendor download, full Python tests, Dashboard
  Node build, Docker E2E and a fresh local checkout were **not verified**.
- Git cleanups are on a separate branch based on draft PR #21;
  `main` and historical experiment commits remain untouched.

## Next *safe* actions before package renaming

1. Restore GitHub Actions and validate the new hygiene and existing
   runtime tests. Collect actual job logs; don't guess failure reason.
2. Authorize `F:/econith` **locally** in Desktop Commander, then
   run `git status`, check worktree for uncommitted data and backup,
   and execute tests before any local cleanup.
3. Generate reverse dependency/import map; tag canonical collectors,
   event producers, execution adapters and World engines.
4. Run third-party licensing and software bill of materials checks.
5. Only then propose high-risk folder moves/deletions as separate PRs.

**Governance:** no real exchange orders, no merge, no research evidence loss.

## Confirmed GitHub Actions root cause and repair

Read-only investigation of GitHub Actions run `38924252770` showed a
workflow conclusion of `failure` with **zero jobs**. Parsing the current
`.github/workflows/ci.yml` with PyYAML on the connected Windows
machine raised `yaml.scanner.ScannerError` at workflow **line 42**,
the inline `run:` scalar containing a Python dictionary with `: `
pairs. This invalid YAML prevented GitHub from scheduling jobs.

**Repair:** encode the same Python deploy-gate smoke command as a
YAML literal block using `run: |` instead of an unquoted inline
scalar. Preserve command semantics. Add `PyYAML==6.0.2` to development
requirements and `tests/test_ci_workflow.py` to prevent recurrence.

After this correction, GitHub run
[38997163293](https://github.com/coffee2307/econith/actions/runs/38997163293)
successfully created **both** `dashboard` and `pytest` jobs. The
`dashboard` job passed checkout, `npm ci`, `npm run lint` and
`npm run build`. The `pytest` job passed environment setup and
`python -m ci.repo_hygiene_check --root . --enforce`, and continued
to the Python test step when checked. **Do not label the entire
workflow successful until the Python job concludes.**

## Static execution-authority checkpoints

The new `ci/architecture_inventory.py` performs source AST analysis
without importing production modules. Three pure unit tests passed
on the authorized in-memory Python session. An initial static sample
identified:

| File | Evidence | Pending action |
|---|---|---|
| `quant/ccxt_bridge.py` | Direct `create_order` call plus `quant.fill` publication | Verify hard mainnet kill switch, idempotency and reconciliation |
| `bridges/quant_bridge.py` | `order.intent` event contract | Map subscribers and Sentinel veto |
| `econith_quant/bridge/ai_bridge.py` | `order.intent` advisory→intent | Determine actual active runtime binding |
| `econith_quant/bridge/exchange_bridge.py` | `order.intent` observer and mock execution | Distinguish telemetry from capital-bearing authority |
| `econith/world/abides_kernel.py` | Publishes `quant.fill` in simulation | Verify World/Quant isolation and mode tags |
| `sentinel/manager.py` | Consumes `quant.fill` for account risk | Verify fill provenance and ledger truth |

These are **static observations**, not proof that every publisher is
active concurrently or that capital can actually be double counted.
The full import/event graph requires a complete checkout and tests.
