"""ECONITH :: econith.quant.routing.router

Native smart-order routing kernel. Internalizes the NoFx routing/liquidity-sweep
flow (order split across a symbol universe under a per-leg cap, conviction-scaled
notional) as pure ECONITH logic — no external package, no threads, deterministic.

Compatibility interface:  route(signal, context) -> RoutePlan
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from econith.quant.routing.models import PROFILES, RouteLeg, RoutePlan, RouterProfile

__all__ = ["EconithRouteKernel", "NoFxNativeRouter"]


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


@dataclass(slots=True)
class EconithRouteKernel:
    """Deterministic smart-order route planner (native NoFx internalization)."""

    profile_name: str = "balanced"

    def __post_init__(self) -> None:
        self.profile_name = self.profile_name if self.profile_name in PROFILES else "balanced"

    @property
    def profile(self) -> RouterProfile:
        return PROFILES[self.profile_name]

    def set_profile(self, profile_name: str) -> RouterProfile:
        key = profile_name.strip().lower()
        if key in PROFILES:
            self.profile_name = key
        return self.profile

    def status(self) -> dict:
        return {
            "active_profile": self.profile.payload(),
            "available_profiles": {k: p.payload() for k, p in PROFILES.items()},
        }

    # -- unified compatibility interface --------------------------------------
    def route(self, signal: dict[str, Any], context: dict[str, Any] | None = None) -> RoutePlan:
        """route(signal, context) -> RoutePlan.

        ``signal`` carries {direction, confidence, base_quantity, reduce_only,
        symbol?}. ``context`` is reserved for live marks / liquidity depth.
        """
        ctx = context or {}
        return self.build_plan(
            direction=float(signal.get("direction", 0.0)),
            confidence=float(signal.get("confidence", 0.0)),
            base_quantity=float(signal.get("base_quantity", 0.0)),
            reduce_only=bool(signal.get("reduce_only", False)),
            symbol=(signal.get("symbol") or None),
            marks=ctx.get("marks"),
        )

    def build_plan(
        self,
        *,
        direction: float,
        confidence: float,
        base_quantity: float,
        reduce_only: bool,
        symbol: Optional[str] = None,
        marks: dict[str, float] | None = None,
    ) -> RoutePlan:
        p = self.profile
        conf = _clamp(confidence, 0.0, 1.0)
        dirn = _clamp(direction * p.bias_multiplier, -1.0, 1.0)
        side = "BUY" if dirn > 0 else "SELL"
        universe = (symbol.upper(),) if symbol else p.symbols
        if not universe or base_quantity <= 0 or abs(dirn) < 1e-9:
            return RoutePlan(profile=p.name, confidence=conf, direction=dirn, reduce_only=reduce_only)

        # Equal split under cap; deterministic and non-blocking.
        n = len(universe)
        per_weight = min(p.max_leg_fraction, 1.0 / n)
        scale = abs(dirn) * conf
        legs: list[RouteLeg] = []
        for sym in universe:
            qty = base_quantity * scale * per_weight
            if qty <= 0:
                continue
            legs.append(
                RouteLeg(
                    symbol=sym,
                    side=side,
                    quantity=qty,
                    desk="crypto_majors",
                    weight=per_weight,
                    reason=f"econith_route profile={p.name} scale={scale:.3f}",
                )
            )
        return RoutePlan(
            profile=p.name,
            confidence=conf,
            direction=dirn,
            reduce_only=reduce_only,
            legs=legs,
        )


# Backward-compatible alias (pre-kernelization symbol).
NoFxNativeRouter = EconithRouteKernel

