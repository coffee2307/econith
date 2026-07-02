"""ECONITH :: backend_core entrypoint

FastAPI application that boots the AI-001 Core Engine and runs the full Phase 0
+ Phase 1/3 mock pipeline concurrently:

    TimeEngine  ──▶ governs market-data cadence
    Streamer    ──▶ md.aggTrade / md.depth        (mock Binance frames)
    Pipeline    ──▶ indicator.obi / .volume_delta / md.ticker
    Sentinel    ──▶ sentinel.status / .emergency  (risk governance loop)
    MetricsHub  ──▶ consolidated read-model for the dashboard

A WebSocket at ``/api/v1/stream/metrics`` streams the consolidated JSON snapshot
to the Next.js dashboard.

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
from ai.simulator_engine.llm_scenario import LLMScenarioEngine
from ai.simulator_engine.world_kernel import WorldKernel
from config.settings import TIME_SPEED_MULTIPLIERS, get_settings
from core.engine import get_engine
from core.mode import QuantMode, get_mode_manager
from core.telemetry import MetricsHub
from econith_quant.bridge.ai_bridge import AIBridge
from econith_quant.bridge.exchange_bridge import ExchangeBridge
from infrastructure.alternative.provider import AlternativeDataProvider
from infrastructure.preprocessing.pipeline import MarketDataPipeline
from infrastructure.storage.recorder import StateRecorder
from infrastructure.websocket.streamer import BinanceWebSocketStreamer
from sentinel.manager import Sentinel

logging.basicConfig(level=logging.INFO)
settings = get_settings()

# Concurrent runtime components, populated during lifespan startup.
components: dict[str, object] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = get_engine()
    await engine.startup()  # starts EventBus + TimeEngine

    # --- Read-model + processing graph -----------------------------------
    # Subscribers are registered BEFORE producers start so no frames are missed.
    hub = MetricsHub(engine.bus, engine.time)
    hub.register()

    pipeline = MarketDataPipeline(engine.bus)
    pipeline.register()

    recorder = StateRecorder(engine.bus)
    recorder.register()

    sentinel = Sentinel(engine.bus)
    sentinel.register()

    # --- AI Quant layer (Phase 2 + Phase 4) ------------------------------
    predictor = Predictor(engine.bus)
    predictor.register()

    ai_bridge = AIBridge(engine.bus)
    ai_bridge.register()

    exchange_bridge = ExchangeBridge(engine.bus)
    exchange_bridge.register()

    # --- ECONITH World layer (Phase 6-8) ---------------------------------
    world_kernel = WorldKernel(engine.bus)
    world_kernel.register()
    llm_scenario = LLMScenarioEngine(engine.bus, world_kernel)

    # --- Producers -------------------------------------------------------
    streamer = BinanceWebSocketStreamer(engine.bus, engine.time, symbol="BTCUSDT")
    alt_provider = AlternativeDataProvider(engine.bus, symbol="BTCUSDT")
    alt_provider.register()

    # Start the concurrent loops.
    await sentinel.start()
    await predictor.start()
    await alt_provider.start()
    await streamer.start()

    components.update(
        engine=engine,
        hub=hub,
        pipeline=pipeline,
        recorder=recorder,
        sentinel=sentinel,
        predictor=predictor,
        ai_bridge=ai_bridge,
        exchange_bridge=exchange_bridge,
        world_kernel=world_kernel,
        llm_scenario=llm_scenario,
        streamer=streamer,
        alt_provider=alt_provider,
    )
    app.state.components = components
    logging.getLogger("econith").info(
        "%s backend_core online (mock=%s)", settings.app_name, streamer.is_mock
    )
    try:
        yield
    finally:
        await streamer.stop()
        await alt_provider.stop()
        await predictor.stop()
        await sentinel.stop()
        await engine.shutdown()
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


# --- health / introspection --------------------------------------------------
@app.get(f"{settings.api_prefix}/health")
async def health() -> dict:
    streamer = components.get("streamer")
    return {
        "status": "ok",
        "service": "backend_core",
        "version": settings.app_version,
        "mock": getattr(streamer, "is_mock", None),
        "quant_mode": get_mode_manager().snapshot(),
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


# --- ECONITH World simulator (Phase 6-8) -------------------------------------
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
    return await _world_kernel().mutate_country(
        code.upper(), req.group, req.field, req.value
    )


@app.post(f"{settings.api_prefix}/world/tariff")
async def world_tariff(req: TariffRequest) -> dict:
    return await _world_kernel().set_tariff(
        req.source.upper(), req.target.upper(), req.value
    )


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
        logging.getLogger("econith").exception("metrics websocket error")
        await ws.close()
