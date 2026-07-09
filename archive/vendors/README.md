# ECONITH :: vendors/

Lean, pinned vendoring of upstream OSS. **Only** `manifest.json`, `manifest.schema.json`,
`.gitignore`, and this README are tracked in git. The actual upstream source is pulled on
demand and stays untracked (see `.gitignore`).

## Rules (Zero-Breakage)

1. **Never import a vendor directly in runtime code.** All access goes through an
   `econith_*` shim in `bridges/vendor_shims.py`. Runtime depends on the shim contract,
   not on the upstream package.
2. **Sparse checkout only.** We pull the minimal core-logic subtrees listed under each
   vendor's `sparse_paths`, never the full repo (no docs / tests / web assets).
3. **Pinned commits.** Every vendor is locked to an immutable SHA. Bump deliberately.
4. **Graceful degradation.** A missing vendor must never crash `main.py`; the shim reports
   `available == False` and the runtime continues on its native path.
5. **Mode gate is sacred.** No shim subscribes with `domain=QUANT` to a `world.*` topic,
   and no shim publishes `order.intent`. `Sentinel` keeps absolute veto.

## Usage

```bash
# Pin the 3 P0 SHAs in manifest.json first, then:
bash scripts/setup_vendors.sh              # pull all P0 vendors
bash scripts/setup_vendors.sh openbb qlib  # pull a subset
python scripts/verify_invariants.py        # safety gate — must pass before wiring
```

## P0 scope

| Vendor          | Pillar | Shim                       | Emits                    |
| --------------- | ------ | -------------------------- | ------------------------ |
| OpenBB          | Core   | `EconithOpenBBShim`        | `core.macro.context`     |
| Qlib            | Core   | `EconithQlibShim`          | `training.feature.ready` |
| TradingAgents   | Quant  | `EconithTradingAgentsShim` | `meta.debate.verdict`    |

Deferred to later phases: `nofx`, `ai_hedge_fund`, `zipline_reloaded`, `mesa`, `abides`.
