"""ECONITH :: econith.quant.alpha.kernel

Native alpha signal generator — internalizes the ai-hedge-fund idea of fusing a
few orthogonal factor views into one directional candidate, expressed as pure,
deterministic ECONITH logic (no LLM graph, no external package).

Produces an ADVISORY ``ai.alpha.candidate`` the Predictor may fold into its
ensemble. It is not the sole ``ai.signal`` producer and holds no execution
authority.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Optional

from core.event_bus import EventBus

logger = logging.getLogger("econith.quant.alpha")

__all__ = ["AlphaCandidate", "EconithAlphaKernel"]


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


@dataclass(slots=True)
class AlphaCandidate:
    symbol: str
    direction: float     # [-1, 1]
    confidence: float    # [0, 1]
    agent: str = "econith_alpha"

    def payload(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "direction": round(self.direction, 4),
            "confidence": round(self.confidence, 4),
            "agent": self.agent,
        }


class EconithAlphaKernel:
    """Deterministic multi-factor alpha fusion."""

    def __init__(self, bus: Optional[EventBus] = None) -> None:
        self._bus = bus

    def predict(self, features: dict[str, Any], regime: str = "UNKNOWN") -> Optional[AlphaCandidate]:
        """Fuse momentum (OBI) + carry (funding) + macro (yield curve) factors."""
        obi = features.get("obi")
        funding = features.get("funding_rate")
        spread = features.get("yield_spread_10y_2y")
        vol = float(features.get("realized_vol") or 0.0)
        if obi is None and funding is None and spread is None:
            return None

        momentum = math.tanh(float(obi or 0.0))
        carry = -math.tanh(float(funding or 0.0) * 50.0)  # high funding -> fade longs
        macro = math.tanh(float(spread or 0.0) * 5.0)
        raw = 0.5 * momentum + 0.3 * carry + 0.2 * macro
        direction = _clamp(raw, -1.0, 1.0)
        # Confidence decays with turbulence.
        confidence = _clamp(1.0 / (1.0 + 25.0 * vol), 0.05, 1.0)
        symbol = str(features.get("symbol", "BTCUSDT")).upper()
        return AlphaCandidate(symbol=symbol, direction=direction, confidence=confidence)

    async def publish(self, candidate: AlphaCandidate) -> None:
        if self._bus is not None:
            try:
                await self._bus.publish("ai.alpha.candidate", **candidate.payload())
            except Exception:  # noqa: BLE001
                logger.debug("alpha candidate publish failed")
