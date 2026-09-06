# ECONITH :: Production Architecture Blueprint

Institutional-grade, event-driven quantitative trading, distributed ML pipeline,
aviation-cockpit dashboard, and sovereign geopolitical-macro simulation platform.

The spine of the system is the deterministic **5-phase Tick Engine** running
inside a stateful async `EventBus` loop:

```
[1 SNAPSHOT] -> [2 APPLY_EVENTS] -> [3 RESOLVE_CONFLICTS] -> [4 UPDATE_WORLD] -> [5 EMIT_SIGNALS]
```

Implemented in `core/engine.py` (`TickPipeline`, `TickPhase`, `TickContext`) and
consumed by every domain below.

---

## 1. Expanded System Directory Map

```
econith/
├─ core/                              # The Omniscient Knowledge Engine
│  ├─ engine.py                       # 5-phase deterministic TickPipeline + TimeEngine
│  ├─ event_bus.py                    # async pub/sub backbone
│  ├─ mode.py                         # REALITY | SIMULATION sovereignty gate
│  ├─ telemetry.py                    # MetricsHub read-model
│  ├─ ingestion/                      # [NEW] zero-cost macro ingestion topology
│  │  ├─ config.py                    #   FREDConfig, WorldBank/IMF/Eurostat/YFinance configs
│  │  ├─ context_state.py             #   AssetUniverse, ExhaustiveContextState, feature isolation
│  │  ├─ adapters.py                  #   fault-tolerant per-source adapters + backoff
│  │  └─ macro_hub.py                 #   MacroIngestionHub scheduler/consolidator
│  └─ cockpit/                        # [NEW] aviation cockpit telemetry
│     ├─ schemas.py                   #   Pydantic contracts mirroring the TS interfaces
│     └─ ws.py                        #   CockpitTelemetryHub + FastAPI WS/REST router
│
├─ quant/                            # [NEW] Binance Production & Simulation Bridge
│  ├─ payloads.py                     # ExecutionPayload, CCXTOrderPayload, algo slices
│  ├─ context_slicer.py               # BrainSlicingAdapter, CausalContextVector, DeskPolicyHead
│  └─ ccxt_bridge.py                  # CCXTBinanceBridge (REALITY) + synthetic fills (SIMULATION)
│
├─ ai/
│  ├─ simulator_engine/               # THE WORLD (multi-agent butterfly simulator)
│  │  ├─ world_kernel.py              #   existing macro kernel
│  │  ├─ agents.py                    #   market-aware corporate/gov/social ensemble
│  │  └─ sovereign_graph.py           # [NEW] Gov/CentralBank/Enterprise/Public + 5-phase loop
│  └─ journalist/                     # [NEW] Journalist LLM news terminal
│     └─ aggregator.py                #   async EventBus consumer -> synthesized news
│
├─ infrastructure/
│  ├─ websocket/ storage/ alternative/ preprocessing/  # existing data plane
│  └─ daemon/                         # [NEW] 24/7 VPS telemetry ingestion daemon
│     └─ vps_telemetry_daemon.py      #   self-healing WS + ring buffer + async persistence
│
├─ training/
│  ├─ orchestrator.py train_ppo.py train_world.py       # existing training
│  └─ h200/                           # [NEW] RunPod H200 pipeline orchestrator
│     └─ orchestrator.py              #   data processing + partitioned training + HBM3e flags
│
├─ dashboard/                         # Next.js 16 / React 19 cockpit UI
│  ├─ app/ components/ contexts/ hooks/
│  └─ lib/cockpit/types.ts            # [NEW] cockpit TS telemetry contracts
│
├─ config/                            # typed env + settings (FRED_API_KEY wired)
├─ sentinel/                          # risk governance (veto authority in PHASE 3)
└─ main.py                            # FastAPI app + lifespan wiring
```

---

## 2. Component Ledger

| Domain | Component | Module | Key Types |
|---|---|---|---|
| CORE | Macro ingestion | `core/ingestion/macro_hub.py` | `MacroIngestionHub` |
| CORE | Source contracts | `core/ingestion/config.py` | `FREDConfig`, `WorldBankConfig`, `IMFConfig`, `EurostatConfig`, `YFinanceConfig` |
| CORE | Feature isolation | `core/ingestion/context_state.py` | `AssetUniverse`, `ExhaustiveContextState`, `Macro/MicroFeatureBlock` |
| QUANT | Context slicing | `quant/context_slicer.py` | `BrainSlicingAdapter`, `CausalContextVector`, `DeskPolicyHead` |
| QUANT | Execution | `quant/ccxt_bridge.py`, `quant/payloads.py` | `CCXTBinanceBridge`, `ExecutionPayload`, `CCXTOrderPayload` |
| WORLD | Sovereign graph | `ai/simulator_engine/sovereign_graph.py` | `GovernmentAgent`, `CentralBankAgent`, `EnterpriseAgent`, `PublicAgent`, `SovereignWorldGraph` |
| WORLD | Chronology | `ai/simulator_engine/sovereign_graph.py` | `ScenarioChronology`, `ScenarioNode` |
| INFRA | VPS daemon | `infrastructure/daemon/vps_telemetry_daemon.py` | `VPSTelemetryDaemon`, `SelfHealingConnection`, `RingBuffer`, `PersistenceHandler` |
| ML | H200 orchestrator | `training/h200/orchestrator.py` | `H200Orchestrator`, `PartitionedTrainer`, `ComponentPartition`, `H200HardwareProfile` |
| UI | Cockpit backend | `core/cockpit/ws.py` | `CockpitTelemetryHub`, `build_cockpit_router` |
| UI | Cockpit contracts | `dashboard/lib/cockpit/types.ts` | `IMatchedOrderLog`, `IPnLTelemetryHUD`, `IMarginSecurityMatrix`, `IAssetAllocationRadar` |
| NEWS | Journalist LLM | `ai/journalist/aggregator.py` | `JournalistLLM`, `NumericDelta`, `NewsLog` |

---

## 3. EventBus Topic Contract

| Topic | Producer | Consumers |
|---|---|---|
| `time.tick`, `engine.tick_complete` | `TickPipeline` | world kernel, subsystems |
| `core.macro.<source>.update` | `MacroIngestionHub` | regime layer, journalist |
| `core.macro.context` | `MacroIngestionHub` | cockpit hub, journalist |
| `world.macro` | `SovereignWorldGraph`, `WorldKernel` | telemetry, cockpit, journalist |
| `world.micro_impact` | `SovereignWorldGraph` | journalist, quant (SIMULATION only) |
| `quant.fill` | `CCXTBinanceBridge` | cockpit flight log, journalist |
| `journalist.news` | `JournalistLLM` | cockpit news ticker |
| `sentinel.status`, `sentinel.emergency` | `Sentinel` | telemetry, cockpit |

---

## 4. Sovereignty & Isolation Invariants

1. **Mode sovereignty** (`core/mode.py`): in `REALITY`, `world.micro_impact` is
   hard-blocked from the trading brain; synthetic anomaly injection is disabled.
2. **Epistemic feature isolation** (`context_state.py`): macro and per-asset
   micro blocks are structurally separate fields; a tokenizer physically cannot
   read another desk's tape.
3. **Parameter isolation** (`training/h200`): the Meta-Brain, per-desk PPO nets
   and the Neural World model train as distinct `ComponentPartition`s.
4. **Deterministic conflict resolution** (`TickPhase.RESOLVE_CONFLICTS`): the
   Sentinel veto carries the highest authority and suppresses lower-authority
   trade intents within the same tick.
