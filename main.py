"""ECONITH :: backend_core entrypoint (unified production integration)

FastAPI application that boots the AI-001 Core Engine and wires together BOTH
the legacy pipeline and the eight advanced production subsystems on a single
deterministic 5-phase Tick Engine and shared EventBus.

    TimeEngine     ──▶ drives the deterministic 5-phase TickPipeline
    Streamer       ──▶ md.aggTrade / md.depth        (mock/live Binance frames)
    Pipeline       ──▶ indicator.obi / .volume_delta / md.ticker
    Sentinel       ──▶ sentinel.status / .emergency  (risk governance loop)
    MetricsHub     ──▶ consolidated legacy read-model for the dashboard

    -- advanced subsystems --------------------------------------------------
    MacroIngestionHub  ──▶ core.macro.* (FRED/World Bank/IMF/Eurostat/yfinance)
    SovereignWorldGraph──▶ world.sovereign (4-agent butterfly, chronology forks)
    CockpitTelemetryHub──▶ /api/v1/stream/cockpit (flight log / PnL / margin / radar)
    JournalistLLM      ──▶ journalist.news (semantic narrative synthesis)
    CCXTBinanceBridge  ──▶ quant.fill (REALITY live / SIMULATION synthetic)

Bridges (bridges/):
    WorldBridge          reconciles WorldKernel <-> SovereignWorldGraph
    QuantExecutionBridge routes order.intent -> CCXT by REALITY/SIMULATION gate

Run locally:
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ai.inference.predictor import Predictor
from ai.journalist import JournalistLLM
from ai.meta import CoreAIOrchestrator
from ai.simulator_engine.llm_scenario import LLMScenarioEngine
from ai.simulator_engine.sovereign_graph import SovereignWorldGraph, default_world
from ai.simulator_engine.world_kernel import WorldKernel
from bridges.quant_bridge import QuantExecutionBridge
from bridges.world_bridge import WorldBridge
from config.database import dispose_database, init_database, is_fallback
from config.environment import get_environment
from config.settings import TIME_SPEED_MULTIPLIERS, get_settings
from core.api import install_api_security
from core.cockpit import CockpitTelemetryHub, build_cockpit_router
from core.engine import get_engine
from core.ingestion import MacroIngestionHub, MacroIngestionSettings
from core.mode import QuantMode, get_mode_manager
from core.telemetry import MetricsHub
from econith_quant.bridge.ai_bridge import AIBridge
from econith_quant.bridge.exchange_bridge import ExchangeBridge
from infrastructure.alternative.provider import AlternativeDataProvider
from infrastructure.preprocessing.pipeline import MarketDataPipeline
from infrastructure.storage.recorder import StateRecorder
from infrastructure.websocket.streamer import BinanceWebSocketStreamer
from quant.ccxt_bridge import CCXTBinanceBridge
from sentinel.manager import Sentinel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("econith")
settings = get_settings()

# Concurrent runtime components, populated during lifespan startup.
components: dict[str, object] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    env = get_environment()

    # Storage resiliency: probe primary DB, seamlessly fail over to local SQLite.
    await init_database()
    if is_fallback():
        logger.warning("persistence running on local SQLite failover matrix")

    engine = get_engine()
    await engine.startup()  # starts EventBus + TimeEngine + TickPipeline

    # =====================================================================
    #  SUBSCRIBERS FIRST (registered before producers so no frame is missed)
    # =====================================================================
    # --- Legacy read-model + processing graph ----------------------------
    hub = MetricsHub(engine.bus, engine.time)
    hub.register()

    pipeline = MarketDataPipeline(engine.bus)
    pipeline.register()

    recorder = StateRecorder(engine.bus)
    recorder.register()

    sentinel = Sentinel(
        engine.bus,
        starting_capital=settings.starting_capital,
        max_drawdown_pct=env.sentinel_max_drawdown_pct,
        var_limit_pct=env.sentinel_var_limit_pct,
        latency_limit_ms=env.sentinel_latency_limit_ms,
        freeze_cooldown_s=env.sentinel_freeze_cooldown_s,
    )
    sentinel.register()

    # --- Advanced read-models (CORE / cockpit / journalist) --------------
    macro_hub = MacroIngestionHub(engine.bus, MacroIngestionSettings.from_environment())

    cockpit_hub = CockpitTelemetryHub(
        engine.bus, starting_capital=settings.starting_capital
    )
    cockpit_hub.register()

    journalist = JournalistLLM(engine.bus)
    journalist.register()

    # Core AI orchestrator: fuses HF micro + LF macro context and broadcasts
    # meta.quant.directive / meta.risk.directive so sub-agents recalibrate.
    core_ai = CoreAIOrchestrator(engine.bus)
    core_ai.register()

    # --- AI Quant layer (Phase 2 + Phase 4) ------------------------------
    predictor = Predictor(engine.bus)
    predictor.register()

    ai_bridge = AIBridge(engine.bus)
    ai_bridge.register()

    # Legacy mock TWAP bridge: retained purely for its order.update UI feed.
    exchange_bridge = ExchangeBridge(engine.bus)
    exchange_bridge.register()

    # Advanced execution: state-isolation gate over CCXT (REALITY/SIMULATION).
    ccxt_bridge = CCXTBinanceBridge(
        engine.bus,
        api_key=env.effective_binance_trade_api_key,
        api_secret=env.effective_binance_trade_api_secret,
        testnet=env.binance_testnet,
        credentialed=env.has_binance_trade_credentials,
    )
    quant_exec = QuantExecutionBridge(engine.bus, ccxt_bridge)
    quant_exec.register()

    # --- ECONITH World layer (legacy kernel + sovereign graph) -----------
    world_kernel = WorldKernel(engine.bus)
    world_kernel.register()
    llm_scenario = LLMScenarioEngine(engine.bus, world_kernel)

    sovereign_world: SovereignWorldGraph = default_world(engine.bus)
    sovereign_world.register(engine.pipeline)  # wires the 4 agents into 5 phases

    world_bridge = WorldBridge(world_kernel, sovereign_world)

    # =====================================================================
    #  PRODUCERS
    # =====================================================================
    streamer = BinanceWebSocketStreamer(engine.bus, engine.time, symbol="BTCUSDT")
    alt_provider = AlternativeDataProvider(engine.bus, symbol="BTCUSDT")
    alt_provider.register()

    # =====================================================================
    #  START CONCURRENT LOOPS
    # =====================================================================
    await sentinel.start()
    await predictor.start()
    await alt_provider.start()
    await streamer.start()
    await macro_hub.start()      # launches one scheduler task per macro source
    await journalist.start()     # starts the narrative synthesis flush loop
    await core_ai.start()        # starts the cross-asset context fusion loop
    await ccxt_bridge.connect()  # authenticates only in REALITY mode

    # Mount the cockpit router (GET /cockpit/snapshot + WS /stream/cockpit).
    app.include_router(build_cockpit_router(cockpit_hub, settings.api_prefix))

    components.update(
        env=env,
        engine=engine,
        hub=hub,
        pipeline=pipeline,
        recorder=recorder,
        sentinel=sentinel,
        macro_hub=macro_hub,
        cockpit_hub=cockpit_hub,
        journalist=journalist,
        core_ai=core_ai,
        predictor=predictor,
        ai_bridge=ai_bridge,
        exchange_bridge=exchange_bridge,
        ccxt_bridge=ccxt_bridge,
        quant_exec=quant_exec,
        world_kernel=world_kernel,
        llm_scenario=llm_scenario,
        sovereign_world=sovereign_world,
        world_bridge=world_bridge,
        streamer=streamer,
        alt_provider=alt_provider,
    )
    app.state.components = components
    logger.info(
        "%s backend_core online (mock=%s, mode=%s, macro_sources=%d)",
        settings.app_name, streamer.is_mock,
        get_mode_manager().mode.value, len(MacroIngestionSettings.from_environment().enabled_sources()),
    )
    try:
        yield
    finally:
        # =================================================================
        #  GRACEFUL SHUTDOWN (strict reverse-priority: producers -> engine)
        # =================================================================
        await streamer.stop()
        await alt_provider.stop()
        await ccxt_bridge.close()
        await macro_hub.stop()
        await journalist.stop()
        await core_ai.stop()
        await predictor.stop()
        await sentinel.stop()
        await engine.shutdown()   # stops TimeEngine + EventBus last
        await dispose_database()
        components.clear()


app = FastAPI(
    title=f"{settings.app_name} :: backend_core",
    version=settings.app_version,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Guarded API security: gate sensitive mutating routes + audit-trail every
# operator state-mutation command (no-op auth when API_AUTH_ENABLED is false).
install_api_security(app, settings)


# --- typed request bodies ----------------------------------------------------
class SpeedRequest(BaseModel):
    multiplier: int


class InjectRequest(BaseModel):
    kind: Literal["shock", "latency", "vol"] = "shock"


class ScenarioRequest(BaseModel):
    prompt: str


class MutateRequest(BaseModel):
    group: str = ""          # "" => top-level (e.g. gdp_growth), else vector name
    field: str
    value: float


class TariffRequest(BaseModel):
    source: str
    target: str
    value: float


class ModeRequest(BaseModel):
    mode: Literal["REALITY", "SIMULATION"]


# --- component accessors -----------------------------------------------------
def _engine():
    return components.get("engine") or get_engine()


def _hub() -> MetricsHub:
    return components["hub"]  # type: ignore[return-value]


def _sentinel() -> Sentinel:
    return components["sentinel"]  # type: ignore[return-value]


def _streamer() -> BinanceWebSocketStreamer:
    return components["streamer"]  # type: ignore[return-value]


def _llm_scenario() -> LLMScenarioEngine:
    return components["llm_scenario"]  # type: ignore[return-value]


def _world_kernel() -> WorldKernel:
    return components["world_kernel"]  # type: ignore[return-value]


def _world_bridge() -> WorldBridge:
    return components["world_bridge"]  # type: ignore[return-value]


def _sovereign() -> SovereignWorldGraph:
    return components["sovereign_world"]  # type: ignore[return-value]


def _cockpit() -> CockpitTelemetryHub:
    return components["cockpit_hub"]  # type: ignore[return-value]


def _journalist() -> JournalistLLM:
    return components["journalist"]  # type: ignore[return-value]


def _macro_hub() -> MacroIngestionHub:
    return components["macro_hub"]  # type: ignore[return-value]


# --- health / introspection --------------------------------------------------
@app.get(f"{settings.api_prefix}/health")
async def health() -> dict:
    streamer = components.get("streamer")
    ccxt = components.get("ccxt_bridge")
    return {
        "status": "ok",
        "service": "backend_core",
        "version": settings.app_version,
        "mock": getattr(streamer, "is_mock", None),
        "quant_mode": get_mode_manager().snapshot(),
        "execution": ccxt.execution_status() if ccxt else None,
        "subsystems": sorted(components.keys()),
    }


@app.get(f"{settings.api_prefix}/metrics")
async def metrics() -> dict:
    return _hub().snapshot()


# --- time engine controls ----------------------------------------------------
@app.get(f"{settings.api_prefix}/time")
async def get_time() -> dict:
    engine = _engine()
    return {
        "sim_day": engine.time.sim_day,
        "multiplier": engine.time.multiplier,
        "running": engine.time.running,
    }


@app.post(f"{settings.api_prefix}/time/speed")
async def set_time_speed(req: SpeedRequest) -> dict:
    if req.multiplier not in TIME_SPEED_MULTIPLIERS:
        return {"error": f"multiplier must be one of {TIME_SPEED_MULTIPLIERS}"}
    engine = _engine()
    engine.time.set_speed(req.multiplier)
    return {"multiplier": engine.time.multiplier}


@app.post(f"{settings.api_prefix}/time/pause")
async def pause_time() -> dict:
    _engine().time.pause()
    return {"running": _engine().time.running}


@app.post(f"{settings.api_prefix}/time/resume")
async def resume_time() -> dict:
    _engine().time.resume()
    return {"running": _engine().time.running}


# --- quant operating mode (REALITY vs SIMULATION) ----------------------------
@app.get(f"{settings.api_prefix}/mode")
async def get_quant_mode() -> dict:
    return get_mode_manager().snapshot()


@app.post(f"{settings.api_prefix}/mode")
async def set_quant_mode(req: ModeRequest) -> dict:
    mgr = get_mode_manager()
    mgr.set(QuantMode(req.mode))
    # Re-authenticate the CCXT session when entering REALITY.
    ccxt = components.get("ccxt_bridge")
    if ccxt is not None and mgr.is_reality():
        await ccxt.connect()  # type: ignore[attr-defined]
    return mgr.snapshot()


# --- sentinel controls (drive the risk demo) ---------------------------------
@app.post(f"{settings.api_prefix}/sentinel/inject")
async def sentinel_inject(req: InjectRequest) -> dict:
    # SOVEREIGNTY GATE: synthetic anomaly injection is only permitted in the
    # SIMULATION sandbox. In REALITY mode the sovereign trading brain must never
    # be perturbed by fabricated shocks, so the request is refused.
    mgr = get_mode_manager()
    if not mgr.anomaly_injection_enabled():
        return {
            "injected": None,
            "error": "anomaly injection disabled in REALITY mode",
            "mode": mgr.mode.value,
        }
    _streamer().inject_anomaly(req.kind)
    return {"injected": req.kind, "mode": mgr.mode.value}


@app.post(f"{settings.api_prefix}/sentinel/reset")
async def sentinel_reset() -> dict:
    _sentinel().reset()
    return {"status": "re-armed"}


@app.get(f"{settings.api_prefix}/state")
async def get_state() -> dict:
    return {"state": _engine().state.snapshot()}


# --- ECONITH World simulator (legacy kernel + sovereign graph bridge) --------
@app.post(f"{settings.api_prefix}/world/scenario")
async def world_scenario(req: ScenarioRequest) -> dict:
    if not req.prompt.strip():
        return {"error": "prompt is empty"}
    return await _llm_scenario().run_scenario(req.prompt)


@app.get(f"{settings.api_prefix}/world/state")
async def world_state() -> dict:
    return _world_kernel().state_dict()


@app.get(f"{settings.api_prefix}/world/country/{{code}}")
async def world_country(code: str) -> dict:
    data = _world_kernel().country_dict(code.upper())
    return data or {"error": f"unknown country {code}"}


@app.post(f"{settings.api_prefix}/world/country/{{code}}/mutate")
async def world_mutate(code: str, req: MutateRequest) -> dict:
    # Bridged: legacy kernel (immediate) + sovereign graph (next-tick fork).
    return await _world_bridge().mutate(code.upper(), req.group, req.field, req.value)


@app.post(f"{settings.api_prefix}/world/tariff")
async def world_tariff(req: TariffRequest) -> dict:
    # Bridged: forks the scenario chronology on the next deterministic tick.
    return await _world_bridge().apply_tariff(
        req.source.upper(), req.target.upper(), req.value
    )


@app.get(f"{settings.api_prefix}/world/sovereign")
async def world_sovereign() -> dict:
    return _sovereign().snapshot()


@app.get(f"{settings.api_prefix}/world/chronology")
async def world_chronology() -> dict:
    return _world_bridge().chronology()


# --- CORE macro ingestion ----------------------------------------------------
@app.get(f"{settings.api_prefix}/macro/snapshot")
async def macro_snapshot() -> dict:
    return _macro_hub().snapshot()


# --- Journalist LLM news terminal --------------------------------------------
@app.get(f"{settings.api_prefix}/journalist/news")
async def journalist_news(limit: int = 20) -> dict:
    return {"news": _journalist().recent(limit=limit)}


# --- Cockpit snapshot (also exposed via the mounted cockpit router) ----------
@app.get(f"{settings.api_prefix}/cockpit")
async def cockpit_snapshot_alias() -> dict:
    return _cockpit().snapshot()


# --- live metrics WebSocket (consumed by the Next.js dashboard) --------------
@app.websocket(f"{settings.api_prefix}/stream/metrics")
async def stream_metrics(ws: WebSocket) -> None:
    await ws.accept()
    hub = _hub()
    try:
        while True:
            await ws.send_json(hub.snapshot())
            await asyncio.sleep(0.2)  # 5 Hz push
    except WebSocketDisconnect:
        return
    except Exception:  # noqa: BLE001 -- never let the socket loop crash the app
        logger.exception("metrics websocket error")
        await ws.close()


if __name__ == "__main__":
    import uvicorn

    from config.environment import get_environment

    env = get_environment()
    uvicorn.run(
        "main:app",
        host=env.app_host,
        port=env.app_port,
        reload=not env.is_production,
        log_level=env.log_level.lower(),
    )
