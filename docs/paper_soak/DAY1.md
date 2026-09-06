# Paper soak — Day 1 (2026-07-27)

## Boot

```powershell
$env:SCALPER_CHECKPOINT = "F:\econith\models\agents\scalper_hf_ppo.zip"
$env:MODEL_DIR = "F:\econith\models"
$env:MODEL_REGISTRY = "F:\econith\models\registry"
$env:WORLD_SIMULATION_DEFAULT = "false"
$env:LOCAL_LLM_ENABLED = "false"
$env:ECONITH_MOCK_TWAP = "false"
$env:BINANCE_EXECUTION_ENV = "demo"
python main.py   # NOT python -m backend.main
```

Log confirmed: `scalper=PPO`, `trend=heuristic`, `mean_reversion=heuristic`, `desks=trained`.

## Checks (Day 1 start)

| Check | Result |
|-------|--------|
| GET `/api/v1/health` | `status=ok`, `execution_env=demo` |
| POST `/api/v1/mode` → SIMULATION | OK |
| GET `/api/v1/control/state` | `agent_brain=trained`, `world_simulation_enabled=false`, `autonomous_loop=false`, `autonomous_loop_implemented=false` |
| GET `/api/v1/cockpit/snapshot` | mode=SIMULATION, equity=100000, flightLog empty at t0, regime=CALM |
| POST `/api/v1/sentinel/inject` `{"kind":"latency"}` | OK in SIM |
| POST `/api/v1/sentinel/reset` | `re-armed` |
| `python -m scripts.paper_soak_check` | all PASS |
| pytest HF + deploy_gate | 13 passed |
| HF holdout gate | PASS (rows=877251) |

## Ops rules (ongoing)

- Keep World OFF (`world_simulation_enabled=false`) unless intentionally testing World→Quant.
- Do **not** enable FULLY_AUTONOMOUS.
- Do **not** use live TRADE keys; stay SIM / DEMO.
- Each day: note cockpit PnL / equity + any Sentinel freezes or fill REJECTED/UNKNOWN in Event Log.
- Optional UI: `cd dashboard && npm run dev` → `/quant`.

## Next days

- Continue SIM soak N days with backend left running or restarted with same env.
- After clean soak: train remaining desks (trend / mean_reversion) on H200 if desired, then DEMO testnet.
