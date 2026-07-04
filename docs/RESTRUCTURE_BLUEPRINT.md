# ECONITH :: Architectural Restructure Blueprint

Target: decouple the monolith into **4 strictly isolated tiers** so a lightweight
data collector can be deployed to a VPS with zero ML dependencies, multi-coin
data never cross-contaminates, macro history is persisted for training, and a
Meta/Core AI orchestrates the sub-agents from a unified cross-asset state.

> Migration posture: the live backend imports the current modules, so the new
> tiers are introduced **additively** first (`collectors/`, `training/quant/`,
> `ai/meta/`). Physical relocation of legacy files happens in a later cut-over
> once the new tiers are validated. This document is the contract for that
> cut-over.

---

## 1. Target directory tree + boundary rules

```text
econith/
│
├── collectors/                     # TIER 1 — STANDALONE, ZERO-ML DEPLOYMENT UNIT
│   │                               #   Rule: importable & runnable with ONLY
│   │                               #   {polars|pandas, websockets, httpx, pyarrow}.
│   │                               #   MUST NOT import ai/, training/, core/, torch.
│   ├── requirements.txt            #   its own lightweight dependency set
│   ├── README.md                   #   VPS deploy guide (copy this folder only)
│   ├── shared/                     #   cross-collector primitives (no network)
│   │   ├── schemas.py              #   CrossAssetTick / AssetClass / validation
│   │   ├── partitioning.py         #   raw/<class>/<desk>/<symbol>/<date> paths
│   │   └── persistence.py          #   Polars non-blocking Parquet snapshot writer
│   ├── market_coin/                #   24/7 HF tick/orderbook (from vps_telemetry_daemon.py)
│   │   └── daemon.py
│   ├── macro_global/               #   cron-like macro pulls + snapshot persistence
│   │   └── scheduler.py
│   └── tradfi_assets/              #   session polling (DXY, Gold, SPX, WTI)
│       └── poller.py
│
├── datasets/                       # TIER 2 — UNIFIED TIME-ALIGNED DATA LAYER
│   ├── raw/                        #   append-only, partitioned by class/desk/symbol
│   │   ├── market/
│   │   │   ├── crypto_majors/BTCUSDT/2026-07-04/*.parquet
│   │   │   ├── crypto_high_beta/SOLUSDT/...
│   │   │   └── crypto_meme/DOGEUSDT/...
│   │   ├── macro/{fred,world_bank,imf,eurostat}/YYYY-MM/*.parquet
│   │   └── tradfi/{dxy,gold,spx,wti}/...
│   └── processed/                  #   CROSS-ASSET FEATURE STORE (ts_ms-aligned matrix)
│       ├── per_symbol/             #   safe single-asset labeled sets
│       ├── per_desk/               #   desk-pooled sets (majors, high_beta, meme)
│       └── cross_asset/            #   wide matrix joined on absolute ts_ms
│
├── training/                       # TIER 3 — SEGMENTED ML PIPELINE (heavy deps OK)
│   ├── quant/                      #   alpha desks + regime
│   │   ├── collect_features.py     #   (from training/collect.py live path)
│   │   ├── label_symbol.py         #   [NEW] group-by-symbol forward returns
│   │   ├── train_ppo.py
│   │   └── train_regime.py
│   ├── risk/                       #   risk model training
│   ├── world/                      #   sovereign/country agent + world model
│   ├── journalist/                 #   event-narrator LLM fine-tune/eval
│   ├── meta/                       #   Core AI (context fusion) training
│   └── h200/orchestrator.py        #   RunPod H200 DDP harness (exists)
│
└── ai/                             # TIER 4 — MULTI-AGENT INFERENCE RUNTIME
    ├── meta/                       #   [NEW] orchestration layer
    │   └── core_ai.py              #   reads unified state, recalibrates sub-agents
    ├── quant/                      #   predictor + agents + regime + fusion
    ├── risk/                       #   sentinel AI adapters
    ├── world/                      #   sovereign graph + scenario + narrative
    └── journalist/                 #   news synthesis
```

### Boundary rules (enforced by review + import discipline)

| Tier | May import | MUST NOT import |
|------|-----------|-----------------|
| `collectors/` | stdlib, `polars`/`pandas`, `websockets`, `httpx`, `pyarrow` | `ai/`, `training/`, `torch`, heavy `core/` runtime |
| `datasets/` | (data only, no code) | — |
| `training/` | `collectors.shared` (schemas), ML stack, `core` contracts | live FastAPI runtime, `dashboard/` |
| `ai/` | `core/`, `training` artifacts (registry), model runtimes | direct disk-collector loops |

Key invariant: **`collectors/` is the only tier deployed to the VPS.** It has no
knowledge of models. It writes raw Parquet that the training tier later refines.

---

## 2. File migration mapping (current → target)

| Current file | Target location | Action |
|--------------|-----------------|--------|
| `infrastructure/daemon/vps_telemetry_daemon.py` | `collectors/market_coin/daemon.py` | extract + strip any non-lightweight import; reuse `collectors/shared` |
| `core/ingestion/macro_hub.py` (fetch loop) | `collectors/macro_global/scheduler.py` | fork the *fetching* half; runtime hub stays in `core/` for the live bus |
| `core/ingestion/adapters.py`, `config.py` | shared by both `core/ingestion` (runtime) and `collectors/macro_global` | keep adapters reusable, no ML deps |
| `training/collect.py` | `training/quant/collect_features.py` | rename; it belongs to the quant training family |
| `training/label.py` | `training/quant/label_symbol.py` | **rewrite** with `groupby("symbol")` (Step 3) |
| `training/train_ppo.py` | `training/quant/train_ppo.py` | move |
| `training/fit_regime.py` | `training/quant/train_regime.py` | move |
| `training/train_world.py` | `training/world/train_world.py` | move |
| `ai/inference/predictor.py` | `ai/quant/predictor.py` | move; orchestrated by `ai/meta/core_ai.py` |
| `ai/agents/`, `ai/regime/`, `ai/ensemble/` | `ai/quant/{agents,regime,ensemble}/` | move |
| `sentinel/manager.py` | `ai/risk/` adapter wraps it | keep engine, add AI adapter |
| `ai/simulator_engine/` | `ai/world/` | move |
| `ai/journalist/` | `ai/journalist/` | unchanged |

The cut-over is done with import-shim modules (old path re-exports new path) so
`main.py` keeps working during the transition; shims are deleted once all call
sites are updated.

---

## 3. Cross-asset time alignment (the processed layer)

All collectors stamp an **absolute `ts_ms`** (UTC epoch milliseconds). The
processed feature store builds a wide matrix by:

1. Loading each symbol's raw partitions (isolated).
2. Labeling **per symbol** (Step 3 — never a global sort).
3. As-of joining low-frequency macro/tradfi onto each HF row by `ts_ms`
   (`merge_asof`, backward direction) so a coin row carries the *most recent
   known* macro state without look-ahead.

This yields three consumable shapes: `per_symbol/`, `per_desk/`, `cross_asset/`.

---

## 4. What this blueprint fixes

- **Deployability**: `collectors/` copies to a VPS with a 4-package venv.
- **Labeling correctness**: forward returns computed within `groupby("symbol")`.
- **Macro persistence**: every macro pull is appended to an on-disk history.
- **Orchestration**: `ai/meta/core_ai.py` fuses HF + LF context to steer agents.
