"""ECONITH :: quant.context_slicer

The Brain-Slicing Adapter pattern.

The CORE's HRL Meta-Brain emits a high-dimensional :class:`CausalContextVector`
(the parent macro regime embedding). A per-asset QUANT sub-brain must never see
another desk's tape, so the :class:`BrainSlicingAdapter` intercepts that vector
and produces a :class:`SlicedObservation` that fuses:

    parent macro regime vector  (shared, read-only)
        +  the target asset's OWN local micro dynamics
        =  a masked observation exclusive to that asset's tokenizer

The slice is then run through a deterministic policy head to emit an
:class:`ExecutionPayload` (discrete/continuous action, TWAP/VWAP weights, target
position delta) destined for the CCXT Binance engine.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.ingestion.context_state import (
    AssetDesk,
    AssetUniverse,
    ExhaustiveContextState,
)
from quant.payloads import (
    AlgoSlice,
    ExecutionAlgo,
    ExecutionPayload,
    OrderSide,
    OrderType,
)

logger = logging.getLogger("econith.quant.context_slicer")

__all__ = [
    "CausalContextVector",
    "SlicedObservation",
    "BrainSlicingAdapter",
    "DeskPolicyHead",
]


# ---------------------------------------------------------------------------
# The Meta-Brain output
# ---------------------------------------------------------------------------
@dataclass(slots=True, frozen=True)
class CausalContextVector:
    """The Meta-Brain's global causal embedding for a single tick.

    ``embedding`` is the dense structural attention output; ``regime_label`` /
    ``regime_confidence`` are the classifier read-out; ``desk_bias`` carries the
    top-level HRL allocation preference per desk (risk-on/off tilt).
    """

    embedding: tuple[float, ...]
    regime_label: str
    regime_confidence: float
    desk_bias: dict[AssetDesk, float] = field(default_factory=dict)
    generated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def bias_for(self, desk: AssetDesk) -> float:
        return self.desk_bias.get(desk, 0.0)


# ---------------------------------------------------------------------------
# The masked, per-asset observation
# ---------------------------------------------------------------------------
@dataclass(slots=True, frozen=True)
class SlicedObservation:
    """A single asset's isolated view: parent macro vector + its own micro."""

    symbol: str
    desk: AssetDesk
    macro_embedding: tuple[float, ...]
    regime_label: str
    regime_confidence: float
    desk_bias: float
    # local micro-structure exclusive to this symbol
    obi: float
    volume_delta: float
    open_interest: float
    funding_rate: float
    realized_volatility: float
    mark_price: float | None

    def net_signal(self) -> float:
        """Blend the parent macro tilt with local order flow into a directional
        conviction in roughly [-1, 1]."""
        macro_tilt = self.desk_bias
        micro_flow = 0.6 * self.obi + 0.4 * _tanh(self.volume_delta / 500.0)
        raw = 0.45 * macro_tilt + 0.55 * micro_flow
        return max(-1.0, min(1.0, raw))


def _tanh(x: float) -> float:
    import math

    return math.tanh(x)


# ---------------------------------------------------------------------------
# Brain-slicing adapter
# ---------------------------------------------------------------------------
class BrainSlicingAdapter:
    """Executive layer that safely slices the global vector per asset.

    Enforces epistemic isolation: a symbol only ever receives its OWN micro
    block from the :class:`ExhaustiveContextState`, fused with the shared parent
    macro embedding. Cross-desk tape is never materialised into the slice.
    """

    def __init__(self, context: ExhaustiveContextState) -> None:
        self._context = context

    def update_context(self, context: ExhaustiveContextState) -> None:
        self._context = context

    def slice_for(
        self, symbol: str, vector: CausalContextVector
    ) -> SlicedObservation:
        """Produce the masked observation for a single symbol."""
        sym = symbol.upper()
        desk = AssetUniverse.desk_of(sym)
        asset = self._context.micro_for(sym)  # raises if not tracked
        micro = asset.micro
        return SlicedObservation(
            symbol=sym,
            desk=desk,
            macro_embedding=vector.embedding,
            regime_label=vector.regime_label,
            regime_confidence=vector.regime_confidence,
            desk_bias=vector.bias_for(desk),
            obi=micro.order_book_imbalance,
            volume_delta=micro.volume_delta,
            open_interest=micro.open_interest,
            funding_rate=micro.funding_rate,
            realized_volatility=micro.realized_volatility,
            mark_price=asset.mark_price,
        )


# ---------------------------------------------------------------------------
# Deterministic desk policy head
# ---------------------------------------------------------------------------
class DeskPolicyHead:
    """Deterministic policy converting a slice into an execution intent.

    This is the structural seam a trained PPO desk network plugs into: replace
    :meth:`decide`'s body with a forward pass and the rest of the pipeline is
    unchanged. The default implementation is a transparent, reproducible
    heuristic so the desk is fully operable pre-training.
    """

    def __init__(
        self,
        mode: str,
        *,
        base_notional: float = 1_000.0,
        entry_threshold: float = 0.15,
        twap_notional_threshold: float = 25_000.0,
        twap_slices: int = 5,
    ) -> None:
        self._mode = mode
        self._base_notional = base_notional
        self._entry_threshold = entry_threshold
        self._twap_notional_threshold = twap_notional_threshold
        self._twap_slices = max(1, twap_slices)

    def decide(
        self, obs: SlicedObservation, current_weight: float = 0.0
    ) -> ExecutionPayload | None:
        """Map a masked observation to a deterministic execution payload.

        Returns ``None`` when conviction is below threshold (a hold).
        """
        signal = obs.net_signal()
        if abs(signal) < self._entry_threshold:
            return None

        going_long = signal > 0
        # Position-lifecycle-aware side selection.
        if current_weight >= 0:
            side = OrderSide.LONG_OPEN if going_long else OrderSide.SHORT_OPEN
        else:
            side = OrderSide.SHORT_CLOSE if going_long else OrderSide.SHORT_OPEN

        mark = obs.mark_price or 1.0
        conviction = min(1.0, abs(signal))
        notional = self._base_notional * (0.5 + conviction)
        # Volatility-scaled position sizing: bleed exposure in high vol.
        vol_scale = 1.0 / (1.0 + 4.0 * obs.realized_volatility)
        notional *= vol_scale
        quantity = max(notional / mark, 1e-9)

        target_weight = max(-1.0, min(1.0, signal))
        target_delta = target_weight - current_weight

        algo, slices = self._plan_execution(notional, quantity)
        payload = ExecutionPayload(
            symbol=obs.symbol,
            desk=obs.desk.value,
            mode=self._mode,
            side=side,
            order_type=OrderType.MARKET,
            algo=algo,
            quantity=round(quantity, 8),
            leverage=self._leverage_for(obs.desk),
            target_position_delta=round(target_delta, 6),
            slices=slices,
            confidence=round(conviction * obs.regime_confidence, 4),
            macro_regime=obs.regime_label,
            client_order_id=self._client_id(obs.symbol),
        )
        return payload

    def _plan_execution(
        self, notional: float, quantity: float
    ) -> tuple[ExecutionAlgo, tuple[AlgoSlice, ...]]:
        """Decide immediate vs TWAP slicing for large notional."""
        if notional < self._twap_notional_threshold:
            return ExecutionAlgo.IMMEDIATE, ()
        n = self._twap_slices
        weight = 1.0 / n
        per_qty = quantity / n
        slices = tuple(
            AlgoSlice(
                sequence=i,
                weight=weight,
                scheduled_offset_ms=i * 750,
                quantity=round(per_qty, 8),
            )
            for i in range(n)
        )
        return ExecutionAlgo.TWAP, slices

    @staticmethod
    def _leverage_for(desk: AssetDesk) -> float:
        # Risk-tiered leverage caps: memes are strictly de-leveraged.
        return {
            AssetDesk.CRYPTO_MAJORS: 5.0,
            AssetDesk.CRYPTO_HIGH_BETA: 3.0,
            AssetDesk.CRYPTO_MEME: 1.0,
            AssetDesk.TRADFI_FOREX: 10.0,
            AssetDesk.COMMODITIES: 5.0,
            AssetDesk.SOVEREIGN: 3.0,
        }.get(desk, 1.0)

    @staticmethod
    def _client_id(symbol: str) -> str:
        seed = f"{symbol}:{datetime.now(timezone.utc).timestamp()}"
        digest = hashlib.sha1(seed.encode()).hexdigest()[:16]
        return f"ECN-{symbol[:6]}-{digest}"
