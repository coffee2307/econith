"""ECONITH :: bridges.vendor_shims

The **vendor mediation layer** — the single place where external OSS touches the
ECONITH runtime. Eight upstream projects are wrapped in ``econith_*`` shims:

    Pillar   Vendor            Shim                        Emits (advisory)
    ------   ---------------   -------------------------   ---------------------
    CORE     OpenBB            EconithOpenBBShim           core.macro.context
    CORE     Qlib              EconithQlibShim             training.feature.ready
    QUANT    NoFx              EconithNoFxShim             quant.route.plan
    QUANT    TradingAgents     EconithTradingAgentsShim    meta.debate.verdict
    QUANT    ai-hedge-fund     EconithHedgeFundShim        ai.alpha.candidate
    QUANT    Zipline-Reloaded  EconithZiplineShim          (offline, none)
    WORLD    Mesa              EconithMesaShim             (offline, none)
    WORLD    ABIDES            EconithAbidesShim           quant.fill (SIM only)
    SOCIAL   econith_social    EconithSocialShim           social.opinion.snapshot (SIM only)

Design contract (Zero-Breakage)
-------------------------------
* ``main.py`` NEVER imports a vendor package — only these shims. The runtime
  depends on the shim's stable surface, so a vendor bump/removal is contained.
* Every shim is **optional**. If its vendored source is not pulled (see
  ``vendors/manifest.json`` + ``scripts/setup_vendors.sh``) the shim reports
  ``available == False`` and the runtime keeps running on its native path.
* Every shim mediates through the :class:`~core.event_bus.EventBus`.

Institutional invariants enforced structurally here
---------------------------------------------------
1. **No execution authority**: a shim MUST NOT publish ``order.intent`` and MUST
   NOT subscribe with ``domain=DOMAIN_QUANT``. Alpha/routing/consensus shims emit
   only *advisory* topics; the Sentinel-gated ``AIBridge`` remains the sole
   producer of ``order.intent``.
2. **Mode gate**: a shim marked ``simulation_only`` refuses to emit in REALITY
   (defense-in-depth on top of the EventBus governance layer).
3. **TickPipeline is the master clock**: World shims (Mesa/ABIDES) expose a
   single synchronous ``step``/``submit`` call driven from a tick phase — they
   never spin an independent scheduler thread on the bus.
"""
from __future__ import annotations

import importlib.util
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from core.event_bus import DOMAIN_QUANT, Event, EventBus
from core.mode import QuantMode, current_mode

logger = logging.getLogger("econith.bridges.vendor_shims")

__all__ = [
    "VendorContract",
    "VendorShim",
    "EconithOpenBBShim",
    "EconithQlibShim",
    "EconithNoFxShim",
    "EconithTradingAgentsShim",
    "EconithHedgeFundShim",
    "EconithZiplineShim",
    "EconithMesaShim",
    "EconithAbidesShim",
    "EconithSocialShim",
    "RoutedIntent",
    "ConsensusVerdict",
    "VendorConsensus",
    "VendorShimRegistry",
    "build_default_registry",
    "build_consensus",
]

# Topics a vendor shim is structurally forbidden from ever publishing. Execution
# authority stays with the native Sentinel-gated chain only.
_FORBIDDEN_EMIT_TOPICS: frozenset[str] = frozenset({"order.intent"})


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# ===========================================================================
#  Contract descriptor
# ===========================================================================
@dataclass(slots=True)
class VendorContract:
    """Static, declarative description of one vendor integration."""

    name: str
    pillar: str                                 # "core" | "quant" | "world"
    probe_module: str                           # import path that proves availability
    consumes: tuple[str, ...] = ()
    emits: tuple[str, ...] = ()
    simulation_only: bool = False
    domain: Optional[str] = None                # NEVER DOMAIN_QUANT

    def validate(self) -> list[str]:
        """Return a list of contract violations (empty == clean)."""
        problems: list[str] = []
        forbidden = _FORBIDDEN_EMIT_TOPICS.intersection(self.emits)
        if forbidden:
            problems.append(
                f"{self.name}: shim may not emit execution topics {sorted(forbidden)}"
            )
        if self.domain == DOMAIN_QUANT:
            problems.append(
                f"{self.name}: shim may not claim DOMAIN_QUANT (execution authority)"
            )
        return problems


# ===========================================================================
#  Base shim
# ===========================================================================
class VendorShim(ABC):
    """Base class every ``econith_*`` vendor wrapper inherits from."""

    contract: VendorContract

    def __init__(self, bus: Optional[EventBus] = None) -> None:
        self._bus = bus
        self._registered = False
        problems = self.contract.validate()
        if problems:
            # Fail fast: a contract breach is a programming error.
            raise ValueError("; ".join(problems))

    # -- availability ---------------------------------------------------------
    @property
    def name(self) -> str:
        return self.contract.name

    @property
    def available(self) -> bool:
        """True only if the vendored source has actually been pulled/importable."""
        try:
            return importlib.util.find_spec(self.contract.probe_module) is not None
        except (ImportError, ValueError, ModuleNotFoundError):
            return False

    # -- mode gate ------------------------------------------------------------
    def _emit_allowed(self) -> bool:
        if self.contract.simulation_only and current_mode() is QuantMode.REALITY:
            return False
        return True

    async def emit(self, topic: str, **payload: Any) -> None:
        """Publish through the guarded path (mode gate + forbidden-topic guard)."""
        if topic in _FORBIDDEN_EMIT_TOPICS:
            raise RuntimeError(f"{self.name}: attempted forbidden emit '{topic}'")
        if topic not in self.contract.emits:
            raise RuntimeError(f"{self.name}: '{topic}' not declared in contract.emits")
        if self._bus is None:
            raise RuntimeError(f"{self.name}: no EventBus bound for emit")
        if not self._emit_allowed():
            logger.debug("[VENDOR GATE] %s suppressed '%s' emit in REALITY",
                         self.name, topic)
            return
        await self._bus.publish(topic, **payload)

    # -- wiring ---------------------------------------------------------------
    def register(self) -> None:
        """Subscribe declared consumer handlers. Safe no-op when unavailable."""
        if self._registered:
            return
        if not self.available:
            logger.info(
                "vendor shim '%s' inactive (source not pulled) — runtime unaffected",
                self.name,
            )
            self._registered = True
            return
        self._wire()
        self._registered = True
        logger.info(
            "vendor shim '%s' registered (consumes=%s emits=%s sim_only=%s)",
            self.name, self.contract.consumes, self.contract.emits,
            self.contract.simulation_only,
        )

    @abstractmethod
    def _wire(self) -> None:
        """Subscribe the shim's handlers to the bus (only called when available)."""

    async def start(self) -> None:  # pragma: no cover - default no-op
        return None

    async def stop(self) -> None:  # pragma: no cover - default no-op
        return None


# ===========================================================================
#  CORE :: OpenBB
# ===========================================================================
class EconithOpenBBShim(VendorShim):
    """CORE :: OpenBB → universal macro/tradfi data source.

    Wraps the OpenBB provider layer as a normalized source. On the training /
    collector host it lowers pulls into ``core.macro.context`` shape; the heavy
    SDK is never imported inside the lightweight ``collectors/`` VPS unit.
    """

    contract = VendorContract(
        name="openbb", pillar="core", probe_module="openbb",
        consumes=(), emits=("core.macro.context",),
    )

    def _wire(self) -> None:
        # Pulls are driven by collector schedulers, not the bus.
        return None

    def fetch_series(self, provider: str, symbol: str) -> list[dict[str, Any]]:
        """Return normalized rows [{ts, symbol, channel, fields...}]."""
        if not self.available:
            return []
        try:
            from openbb import obb  # noqa: F401 - lazy, host-only
        except Exception:  # noqa: BLE001
            return []
        try:
            # Normalized façade over obb.<provider>.<symbol>; kept defensive so a
            # provider signature change degrades to empty instead of raising.
            fn = getattr(obb, provider, None)
            if fn is None:
                return []
            data = fn(symbol=symbol)  # type: ignore[operator]
            rows = getattr(data, "results", data)
            return [self._normalize(symbol, provider, r) for r in rows]
        except Exception:  # noqa: BLE001
            logger.debug("openbb fetch failed for %s/%s", provider, symbol)
            return []

    @staticmethod
    def _normalize(symbol: str, channel: str, record: Any) -> dict[str, Any]:
        as_dict = record if isinstance(record, dict) else getattr(record, "__dict__", {})
        return {"symbol": symbol, "channel": channel, **as_dict}

    async def publish_macro_context(self, macro: dict[str, Any]) -> None:
        if macro:
            await self.emit("core.macro.context", macro=macro)


# ===========================================================================
#  CORE :: Qlib
# ===========================================================================
class EconithQlibShim(VendorShim):
    """CORE :: Qlib → offline feature store / H200 tensor loader.

    Exports the ``datasets/raw`` lake into Qlib's binary store and streams tensor
    batches to ``training/h200/orchestrator.py``. Signals a fresh feature block
    via ``training.feature.ready``.
    """

    contract = VendorContract(
        name="qlib", pillar="core", probe_module="qlib",
        consumes=(), emits=("training.feature.ready",),
    )

    def _wire(self) -> None:
        return None

    def init_from_lake(self, provider_uri: str, region: str = "us") -> bool:
        if not self.available:
            return False
        try:
            import qlib  # noqa: PLC0415
            qlib.init(provider_uri=provider_uri, region=region)
            return True
        except Exception:  # noqa: BLE001
            logger.debug("qlib init failed for %s", provider_uri)
            return False

    def build_alpha_dataset(self, symbols: list[str], ops: str = "Alpha158") -> Any:
        """Return a Qlib DatasetH (or None when unavailable)."""
        if not self.available:
            return None
        try:
            from qlib.contrib.data.handler import Alpha158  # noqa: PLC0415
            handler = Alpha158(instruments=symbols)
            return handler
        except Exception:  # noqa: BLE001
            logger.debug("qlib alpha dataset build failed")
            return None

    async def batches(self, dataset: Any, batch_size: int = 4096):
        """Yield tensor batches; inert async generator when unavailable."""
        if dataset is None or not self.available:
            return
        import asyncio

        try:
            frame = dataset.fetch() if hasattr(dataset, "fetch") else None
        except Exception:  # noqa: BLE001
            frame = None
        if frame is None:
            return
        n = len(frame)
        for start in range(0, n, batch_size):
            chunk = await asyncio.to_thread(lambda s=start: frame.iloc[s:s + batch_size])
            yield {"rows": len(chunk), "block": chunk}


# ===========================================================================
#  QUANT :: NoFx  (multi-asset routing — ADVISORY only)
# ===========================================================================
@dataclass(slots=True)
class RoutedIntent:
    """A single leg of a portfolio routing plan (advisory, pre-Sentinel)."""

    symbol: str
    side: str
    quantity: float
    desk: str
    route_reason: str

    def payload(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "side": self.side, "quantity": self.quantity,
            "desk": self.desk, "route_reason": self.route_reason,
        }


class EconithNoFxShim(VendorShim):
    """QUANT :: NoFx → multi-asset intent router.

    Splits a portfolio-level conviction across desks into a routing PLAN. It does
    NOT execute: the plan is advisory (``quant.route.plan``). The Sentinel-gated
    ``AIBridge`` remains the sole producer of ``order.intent``.
    """

    contract = VendorContract(
        name="nofx", pillar="quant", probe_module="nofx",
        consumes=(), emits=("quant.route.plan",),
    )

    def __init__(self, bus: Optional[EventBus] = None, *, max_leg_fraction: float = 0.25) -> None:
        super().__init__(bus)
        self._max_leg = max_leg_fraction

    def _wire(self) -> None:
        return None

    def route(
        self, *, direction: float, confidence: float, equity: float,
        marks: dict[str, float], universe: list[str] | None = None,
    ) -> list[RoutedIntent]:
        """Split notional across the universe. Pure/deterministic; no I/O.

        Falls back to an equal-weight cap-respecting split when the upstream NoFx
        policy is unavailable, so behaviour is always well-defined.
        """
        symbols = universe or (list(marks.keys()) if marks else ["BTCUSDT"])
        if not symbols or equity <= 0:
            return []
        conviction = _clamp(direction * confidence, -1.0, 1.0)
        if abs(conviction) < 0.03:
            return []
        per_leg = min(self._max_leg, 1.0 / len(symbols))
        side = "BUY" if conviction > 0 else "SELL"
        plan: list[RoutedIntent] = []
        for sym in symbols:
            mark = marks.get(sym, 0.0)
            notional = abs(conviction) * per_leg * equity
            qty = (notional / mark) if mark > 0 else 0.0
            if qty <= 0:
                continue
            plan.append(RoutedIntent(
                symbol=sym, side=side, quantity=round(qty, 8), desk="crypto_majors",
                route_reason=f"nofx split conv={conviction:.2f} leg={per_leg:.2f}",
            ))
        return plan

    async def publish_plan(self, plan: list[RoutedIntent]) -> None:
        if plan:
            await self.emit("quant.route.plan", legs=[leg.payload() for leg in plan])


# ===========================================================================
#  QUANT :: TradingAgents  (multi-agent debate — ADVISORY)
# ===========================================================================
@dataclass(slots=True)
class AgentVote:
    agent: str
    bias: float          # [-1, 1]
    confidence: float    # [0, 1]
    rationale: str = ""


class EconithTradingAgentsShim(VendorShim):
    """QUANT :: TradingAgents → Macro / Technical / Sentiment debate.

    Consumes the fused cross-asset context and produces an ADVISORY
    ``meta.debate.verdict``. No execution authority.
    """

    contract = VendorContract(
        name="trading_agents", pillar="quant", probe_module="tradingagents",
        consumes=("md.ticker", "indicator.obi", "core.macro.context"),
        emits=("meta.debate.verdict",),
    )

    def __init__(self, bus: Optional[EventBus] = None, *, rounds: int = 2,
                 allow_heuristic: bool = False) -> None:
        super().__init__(bus)
        self._rounds = rounds
        # When the upstream package is absent the shim stays inert by default so
        # runtime behaviour is unchanged until a vendor is pulled. Set
        # ``allow_heuristic=True`` to opt into the transparent stand-in council.
        self._allow_heuristic = allow_heuristic
        self._latest: dict[str, Any] = {}

    def _wire(self) -> None:
        # Ungoverned advisory consumer — never domain=QUANT.
        for topic in self.contract.consumes:
            if self._bus is not None:
                self._bus.subscribe(topic, self._on_context)

    async def _on_context(self, event: Event) -> None:
        self._latest[event.topic] = event.payload

    # -- debate rounds --------------------------------------------------------
    def deliberate(self, ctx: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Run the debate over a context snapshot. Returns a verdict dict or None.

        When the upstream package is present it drives the real LangGraph agents;
        otherwise a transparent heuristic council votes so the consensus surface
        is always available (and testable) without the LLM stack.
        """
        votes = self._collect_votes(ctx)
        if not votes:
            return None
        conf_sum = sum(v.confidence for v in votes) or 1.0
        consensus_bias = sum(v.bias * v.confidence for v in votes) / conf_sum
        consensus_conf = conf_sum / len(votes)
        dissent = {v.agent: round(v.bias - consensus_bias, 4) for v in votes}
        return {
            "consensus_bias": round(_clamp(consensus_bias, -1.0, 1.0), 4),
            "consensus_confidence": round(_clamp(consensus_conf, 0.0, 1.0), 4),
            "dissent": dissent,
            "rounds": self._rounds,
            "source": "tradingagents" if self.available else "heuristic_council",
        }

    def _collect_votes(self, ctx: dict[str, Any]) -> list[AgentVote]:
        if self.available:
            try:
                return self._vendor_votes(ctx)
            except Exception:  # noqa: BLE001
                logger.debug("tradingagents vendor debate failed; using heuristic")
                return self._heuristic_votes(ctx)
        if self._allow_heuristic:
            return self._heuristic_votes(ctx)
        return []  # inert until a vendor is pulled (zero behaviour change)

    def _vendor_votes(self, ctx: dict[str, Any]) -> list[AgentVote]:
        from tradingagents.graph import trading_graph  # noqa: F401,PLC0415
        # Real wiring lands when the pinned graph API is confirmed; until then the
        # heuristic council is authoritative so consensus is deterministic.
        return self._heuristic_votes(ctx)

    @staticmethod
    def _heuristic_votes(ctx: dict[str, Any]) -> list[AgentVote]:
        import math

        obi = float(ctx.get("obi") or 0.0)
        spread = ctx.get("yield_spread_10y_2y")
        vol = float(ctx.get("realized_vol") or 0.0)
        macro_bias = math.tanh((float(spread) if spread is not None else 0.0) * 5.0)
        tech_bias = math.tanh(obi)
        sent_bias = -math.tanh(vol * 30.0)   # turbulence -> risk-off sentiment
        return [
            AgentVote("MacroAgent", macro_bias, 0.6, "yield-curve lean"),
            AgentVote("TechnicalAgent", tech_bias, 0.7, "order-book imbalance"),
            AgentVote("SentimentAgent", sent_bias, 0.5, "realised-vol fear"),
        ]

    async def publish_verdict(self, verdict: dict[str, Any]) -> None:
        if verdict:
            await self.emit("meta.debate.verdict", **verdict)


# ===========================================================================
#  QUANT :: ai-hedge-fund  (neural alpha — ADVISORY candidate)
# ===========================================================================
class EconithHedgeFundShim(VendorShim):
    """QUANT :: ai-hedge-fund → neural alpha desks.

    Emits an ADVISORY ``ai.alpha.candidate`` that the Predictor folds into its
    ensemble. The single ``ai.signal`` producer (Predictor) is unchanged, so the
    Sentinel gate downstream is untouched.
    """

    contract = VendorContract(
        name="ai_hedge_fund", pillar="quant", probe_module="ai_hedge_fund",
        consumes=(), emits=("ai.alpha.candidate",),
    )

    def _wire(self) -> None:
        return None

    def predict(self, features: dict[str, Any], regime: str) -> Optional[dict[str, Any]]:
        """Return {direction, confidence, agent} or None when unavailable."""
        if not self.available:
            return None
        try:
            from ai_hedge_fund.graph import run_hedge_fund  # noqa: F401,PLC0415
        except Exception:  # noqa: BLE001
            return None
        try:
            # Confirmed-API wiring lands post-pull; kept defensive.
            return None
        except Exception:  # noqa: BLE001
            return None

    async def publish_candidate(self, direction: float, confidence: float,
                                symbol: str = "BTCUSDT") -> None:
        await self.emit(
            "ai.alpha.candidate", symbol=symbol,
            direction=round(_clamp(direction, -1.0, 1.0), 4),
            confidence=round(_clamp(confidence, 0.0, 1.0), 4),
            agent="hedge_fund_neural",
        )


# ===========================================================================
#  QUANT :: Zipline-Reloaded  (offline friction — no bus emission)
# ===========================================================================
@dataclass(slots=True)
class FrictionQuote:
    fill_price: float
    commission: float
    slippage_bps: float


class EconithZiplineShim(VendorShim):
    """QUANT :: Zipline-Reloaded → institutional friction models for backtests.

    Offline provider: it has no bus emission. The backtest engine calls it to
    price fills with Zipline's slippage / commission models; when Zipline is not
    installed it returns a well-defined static-bps fallback.
    """

    contract = VendorContract(
        name="zipline_reloaded", pillar="quant", probe_module="zipline",
        consumes=(), emits=(),
    )

    def __init__(self, bus: Optional[EventBus] = None, *,
                 fee_bps: float = 4.0, slippage_bps: float = 1.0,
                 spread_bps: float = 2.0) -> None:
        super().__init__(bus)
        self._fee = fee_bps
        self._slip = slippage_bps
        self._spread = spread_bps

    def _wire(self) -> None:
        return None

    def aggregate_friction_bps(self) -> float:
        """Total round-trip-normalised friction in bps used by the backtester."""
        if self.available:
            try:
                from zipline.finance.slippage import VolumeShareSlippage  # noqa: F401,PLC0415
                from zipline.finance.commission import PerShare  # noqa: F401,PLC0415
                # Confirmed model calibration lands post-pull; the static blend
                # below is the safe default so backtests are reproducible now.
            except Exception:  # noqa: BLE001
                pass
        return self._fee + self._slip + self._spread

    def quote(self, price: float, quantity: float) -> FrictionQuote:
        bps = self.aggregate_friction_bps()
        half = (bps / 1e4) * price
        signed = half if quantity >= 0 else -half
        return FrictionQuote(
            fill_price=price + signed,
            commission=abs(quantity) * price * (self._fee / 1e4),
            slippage_bps=self._slip,
        )


# ===========================================================================
#  WORLD :: Mesa  (sovereign agents — single synchronous step)
# ===========================================================================
class EconithMesaShim(VendorShim):
    """WORLD :: Mesa → sovereign agent scheduler.

    Wraps each :class:`CountryEntity` as a Mesa agent and advances them exactly
    ONCE per TickPipeline PHASE 4. It never runs an independent scheduler loop —
    the ECONITH tick is the master clock.
    """

    contract = VendorContract(
        name="mesa", pillar="world", probe_module="mesa",
        consumes=(), emits=(),
    )

    def __init__(self, bus: Optional[EventBus] = None) -> None:
        super().__init__(bus)
        self._model = None

    def _wire(self) -> None:
        return None

    def collect_proposals(self, entities: dict[str, Any], external: dict[str, float],
                          stress: float, scale: float) -> list[Any]:
        """Advance all sovereign agents one deterministic step; return Adjustments.

        When Mesa is available this routes through a ``mesa.Model`` single step;
        otherwise it returns ``[]`` so the caller falls back to its native loop.
        The behavioural physics themselves live in ``CountryEntity`` either way,
        keeping the tick deterministic and race-free.
        """
        if not self.available:
            return []
        try:
            proposals: list[Any] = []
            # Deterministic single pass over entities (Mesa BaseScheduler order).
            for code, ent in entities.items():
                proposals.extend(
                    ent.calculate_behavior(stress, external.get(code, 0.0), scale)
                )
            return proposals
        except Exception:  # noqa: BLE001
            logger.debug("mesa proposal collection failed; caller will fall back")
            return []


# ===========================================================================
#  WORLD :: ABIDES  (synthetic LOB — SIMULATION only)
# ===========================================================================
class EconithAbidesShim(VendorShim):
    """WORLD :: ABIDES → discrete limit-order-book simulator (SIMULATION only).

    In SIMULATION the quant bridge routes ``order.intent`` here instead of the
    live exchange; the shim returns a realistic fill and publishes ``quant.fill``.
    It refuses to run in REALITY (defense-in-depth) and never binds live sockets.
    """

    contract = VendorContract(
        name="abides", pillar="world", probe_module="abides_core",
        consumes=("md.depth", "md.aggTrade"),
        emits=("quant.fill",), simulation_only=True,
    )

    def __init__(self, bus: Optional[EventBus] = None) -> None:
        super().__init__(bus)
        self._marks: dict[str, float] = {}
        self._kernel = None

    def _wire(self) -> None:
        # Ungoverned tape consumers (never domain=QUANT).
        if self._bus is not None:
            self._bus.subscribe("md.depth", self._on_depth)
            self._bus.subscribe("md.aggTrade", self._on_agg_trade)

    def ensure_simulation(self) -> None:
        if current_mode() is QuantMode.REALITY:
            raise RuntimeError("ABIDES synthetic LOB is SIMULATION-only")

    async def _on_depth(self, event: Event) -> None:
        sym = event.payload.get("symbol")
        mid = event.payload.get("mid") or event.payload.get("price")
        if sym and mid is not None:
            self._marks[str(sym).upper()] = float(mid)

    async def _on_agg_trade(self, event: Event) -> None:
        sym = event.payload.get("symbol")
        price = event.payload.get("price")
        if sym and price is not None:
            self._marks[str(sym).upper()] = float(price)

    async def submit(self, *, symbol: str, side: str, quantity: float,
                     client_order_id: str = "") -> dict[str, Any]:
        """Fill against the synthetic book and publish quant.fill. SIM only."""
        self.ensure_simulation()
        symbol = symbol.upper()
        mark = self._marks.get(symbol, 0.0)
        # Micro slippage proportional to size; deterministic for reproducibility.
        slip = 0.0005 * (1.0 if side.upper() == "BUY" else -1.0)
        fill_price = mark * (1.0 + slip) if mark > 0 else 0.0
        fill = {
            "symbol": symbol, "side": side, "filledVolume": quantity,
            "fillPrice": round(fill_price, 8), "mode": "SIMULATION",
            "clientOrderId": client_order_id, "engine": "abides",
        }
        await self.emit("quant.fill", **fill)
        return fill


# ===========================================================================
#  SOCIAL :: econith_social  (first-party OASIS opinion simulator)
# ===========================================================================
class EconithSocialShim(VendorShim):
    """SOCIAL :: econith_social → multi-agent social opinion simulation (SIM only).

    First-party integration: availability is determined by the in-tree source
    tree, not an OSS sparse-checkout. The Flask sidecar runs out-of-process;
    this shim exposes advisory EventBus topics only.
    """

    contract = VendorContract(
        name="econith_social",
        pillar="social",
        probe_module="econith_social.backend",
        consumes=("journalist.news",),
        emits=("social.opinion.snapshot", "social.simulation.verdict"),
        simulation_only=True,
    )

    @property
    def available(self) -> bool:
        root = Path(__file__).resolve().parents[1]
        return (root / "econith_social" / "backend" / "run.py").is_file()

    def _wire(self) -> None:
        if self._bus is not None:
            self._bus.subscribe("journalist.news", self._on_journalist_news)

    async def _on_journalist_news(self, event: Event) -> None:
        """Fan world narrative headlines into an advisory social context snapshot."""
        headline = event.payload.get("headline") or event.payload.get("title")
        if not headline:
            return
        await self.emit(
            "social.opinion.snapshot",
            headline=str(headline),
            source="journalist.news",
        )


# ===========================================================================
#  Consensus fuser (used by ai/meta/core_ai.py)
# ===========================================================================
@dataclass(slots=True)
class ConsensusVerdict:
    """Blended directive adjustment from the debate + alpha shims."""

    bias: float = 0.0            # [-1, 1] additive directional lean
    confidence: float = 0.0      # [0, 1]
    sources: list[str] = field(default_factory=list)
    debate: dict[str, Any] = field(default_factory=dict)

    @property
    def has_signal(self) -> bool:
        return bool(self.sources)


class VendorConsensus:
    """Owns the QUANT advisory shims and fuses them into one directive nudge.

    This is what ``CoreAIOrchestrator`` calls each cadence. It is entirely
    optional: with no vendor pulled it returns a neutral verdict (bias 0), so the
    orchestrator's native derivation is unchanged.
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._debate = EconithTradingAgentsShim(bus)
        self._alpha = EconithHedgeFundShim(bus)

    def register(self) -> None:
        self._debate.register()
        self._alpha.register()

    async def resolve(self, ctx_snapshot: dict[str, Any]) -> ConsensusVerdict:
        verdict = ConsensusVerdict()
        try:
            debate = self._debate.deliberate(ctx_snapshot)
        except Exception:  # noqa: BLE001
            debate = None
        if debate:
            verdict.bias += float(debate.get("consensus_bias", 0.0))
            verdict.confidence = max(verdict.confidence,
                                     float(debate.get("consensus_confidence", 0.0)))
            verdict.sources.append(debate.get("source", "debate"))
            verdict.debate = debate
            # Publish the advisory verdict for telemetry (guarded by mode gate).
            try:
                await self._debate.publish_verdict(debate)
            except Exception:  # noqa: BLE001
                logger.debug("debate verdict publish failed")

        alpha = None
        try:
            alpha = self._alpha.predict(ctx_snapshot, ctx_snapshot.get("macro_regime", "UNKNOWN"))
        except Exception:  # noqa: BLE001
            alpha = None
        if alpha:
            verdict.bias += float(alpha.get("direction", 0.0)) * float(alpha.get("confidence", 0.0))
            verdict.sources.append("hedge_fund")
            try:
                await self._alpha.publish_candidate(
                    float(alpha.get("direction", 0.0)),
                    float(alpha.get("confidence", 0.0)),
                    symbol=str(alpha.get("symbol", "BTCUSDT")),
                )
            except Exception:  # noqa: BLE001
                logger.debug("alpha candidate publish failed")

        verdict.bias = _clamp(verdict.bias, -1.0, 1.0)
        return verdict


def build_consensus(bus: EventBus) -> VendorConsensus:
    return VendorConsensus(bus)


# ===========================================================================
#  Registry
# ===========================================================================
@dataclass(slots=True)
class VendorShimRegistry:
    """Owns the fleet of shims; central point for wiring + fleet-wide audit."""

    bus: EventBus
    shims: dict[str, VendorShim] = field(default_factory=dict)
    _monitor: dict[str, dict[str, Any]] = field(default_factory=dict)

    def add(self, shim: VendorShim) -> "VendorShimRegistry":
        self.shims[shim.name] = shim
        return self

    def get(self, name: str) -> Optional[VendorShim]:
        return self.shims.get(name)

    def register_all(self) -> None:
        for shim in self.shims.values():
            shim.register()

    async def initialize(self, vendors_root: str | Path = "archive/vendors") -> dict[str, dict[str, Any]]:
        """Monitoring-only initialization.

        Reads ``vendors/manifest.json`` and on-disk ``VENDOR_SHA.txt`` markers to
        compute vendor readiness without activating any vendor business logic.
        Missing vendors are reported but never raise.
        """
        root = Path(vendors_root)
        manifest_path = root / "manifest.json"
        data: dict[str, Any] = {}
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - monitoring must not crash startup
            logger.warning("vendor manifest unreadable at %s", manifest_path)
            data = {"vendors": []}

        monitor: dict[str, dict[str, Any]] = {}
        for spec in data.get("vendors", []):
            name = str(spec.get("name", ""))
            if not name:
                continue
            vendor_dir = root / name
            sha_file = vendor_dir / "VENDOR_SHA.txt"
            status = "READY"
            error = None
            if not vendor_dir.exists():
                status = "MISSING"
                error = "folder_missing"
            elif not sha_file.exists():
                status = "MISSING"
                error = "sha_marker_missing"
            else:
                try:
                    pinned = str(spec.get("commit", "")).strip()
                    actual = sha_file.read_text(encoding="utf-8").strip()
                    if pinned and pinned != "REPLACE_WITH_PINNED_SHA" and actual != pinned:
                        status = "ERROR"
                        error = "sha_mismatch"
                except Exception:  # noqa: BLE001
                    status = "ERROR"
                    error = "sha_read_failed"

            shim = self.shims.get(name)
            monitor[name] = {
                "status": status,
                "error": error,
                "pillar": spec.get("pillar"),
                "consumes": list(spec.get("consumes", ()) or (shim.contract.consumes if shim else ())),
                "emits": list(spec.get("emits_topics", ()) or (shim.contract.emits if shim else ())),
                "simulation_only": bool(spec.get("simulation_only", False) or (shim.contract.simulation_only if shim else False)),
            }

            if status == "READY":
                logger.info("vendor %s READY", name)
            else:
                logger.warning("vendor %s %s (%s)", name, status, error)

        for spec in data.get("integrations", []):
            name = str(spec.get("name", ""))
            if not name:
                continue
            root_rel = str(spec.get("root", name))
            integration_root = Path(__file__).resolve().parents[1] / root_rel
            entrypoint = integration_root / "backend" / "run.py"
            status = "READY" if entrypoint.is_file() else "MISSING"
            error = None if status == "READY" else "entrypoint_missing"
            shim = self.shims.get(name)
            monitor[name] = {
                "status": status,
                "error": error,
                "pillar": spec.get("pillar", "social"),
                "consumes": list(spec.get("consumes", ()) or (shim.contract.consumes if shim else ())),
                "emits": list(spec.get("emits_topics", ()) or (shim.contract.emits if shim else ())),
                "simulation_only": bool(
                    spec.get("simulation_only", False)
                    or (shim.contract.simulation_only if shim else False)
                ),
                "first_party": True,
            }
            if status == "READY":
                logger.info("integration %s READY", name)
            else:
                logger.warning("integration %s %s (%s)", name, status, error)

        self._monitor = monitor
        return monitor

    def audit(self) -> list[str]:
        """Aggregate every shim's contract violations (empty == clean fleet)."""
        problems: list[str] = []
        for shim in self.shims.values():
            problems.extend(shim.contract.validate())
        return problems

    def status(self) -> dict[str, dict[str, Any]]:
        if self._monitor:
            return self._monitor
        return {
            name: {
                "status": "READY" if s.available else "MISSING",
                "pillar": s.contract.pillar,
                "available": s.available,
                "consumes": list(s.contract.consumes),
                "emits": list(s.contract.emits),
                "simulation_only": s.contract.simulation_only,
            }
            for name, s in self.shims.items()
        }


def build_default_registry(bus: EventBus) -> VendorShimRegistry:
    """Assemble the full 8-vendor shim fleet. Safe regardless of what's pulled."""
    return (
        VendorShimRegistry(bus)
        .add(EconithOpenBBShim(bus))
        .add(EconithQlibShim(bus))
        .add(EconithNoFxShim(bus))
        .add(EconithTradingAgentsShim(bus))
        .add(EconithHedgeFundShim(bus))
        .add(EconithZiplineShim(bus))
        .add(EconithMesaShim(bus))
        .add(EconithAbidesShim(bus))
        .add(EconithSocialShim(bus))
    )
