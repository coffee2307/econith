"""ECONITH :: econith.quant.routing.models"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

__all__ = ["RouteLeg", "RoutePlan", "RouterProfile", "PROFILES"]


@dataclass(slots=True)
class RouteLeg:
    symbol: str
    side: str
    quantity: float
    desk: str
    weight: float
    reason: str

    def payload(self) -> dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "quantity": round(self.quantity, 8),
            "desk": self.desk,
            "weight": round(self.weight, 6),
            "reason": self.reason,
        }


@dataclass(slots=True)
class RoutePlan:
    profile: str
    confidence: float
    direction: float
    reduce_only: bool
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    legs: list[RouteLeg] = field(default_factory=list)

    def payload(self) -> dict:
        return {
            "profile": self.profile,
            "confidence": round(self.confidence, 6),
            "direction": round(self.direction, 6),
            "reduce_only": self.reduce_only,
            "created_at": self.created_at,
            "legs": [leg.payload() for leg in self.legs],
        }


@dataclass(slots=True, frozen=True)
class RouterProfile:
    name: str
    symbols: tuple[str, ...]
    max_leg_fraction: float
    bias_multiplier: float

    def payload(self) -> dict:
        return {
            "name": self.name,
            "symbols": list(self.symbols),
            "max_leg_fraction": self.max_leg_fraction,
            "bias_multiplier": self.bias_multiplier,
        }


PROFILES: dict[str, RouterProfile] = {
    "balanced": RouterProfile(
        name="balanced",
        symbols=("BTCUSDT", "ETHUSDT", "SOLUSDT"),
        max_leg_fraction=0.45,
        bias_multiplier=1.0,
    ),
    "aggressive": RouterProfile(
        name="aggressive",
        symbols=("BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "PEPEUSDT"),
        max_leg_fraction=0.60,
        bias_multiplier=1.2,
    ),
    "defensive": RouterProfile(
        name="defensive",
        symbols=("BTCUSDT", "ETHUSDT"),
        max_leg_fraction=0.30,
        bias_multiplier=0.8,
    ),
}

