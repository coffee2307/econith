"""ECONITH :: quant.pretrade_gate

The **Order Execution Gate** — mandatory pre-trade net-profit filter
(Anti-Overtrading Protocol, Task 3).

Before ANY order reaches the exchange the gate computes:

    Expected Net Profit = Expected Gross Profit
                        - Exchange Fees
                        - Slippage / market-impact cost
                        - Half-spread crossing cost

and **aborts the order instantly** when either:

    * Expected Net Profit <= 0                (no positive expectancy), or
    * risk_score > dynamic_risk_threshold      (risk budget exceeded).

The gate is a *pure*, side-effect-free evaluator so it is trivially unit-tested
and can be reused by the live path, the backtester and the RL reward. Cost
mathematics are delegated to the existing :class:`EconithFrictionModel` so the
live gate and the offline backtest are graded by identical friction curves.

Adaptive-hypothesis note
------------------------
When no calibrated model supplies an expected edge, :func:`edge_bps_from_signal`
projects one from AI conviction. This is an explicit *estimate* (a placeholder
so the pipeline is fully operational pre-training), NOT a measured alpha — it is
labelled ``estimated`` in the decision so telemetry never mistakes it for a
backtested figure.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from econith.quant.backtest.friction import EconithFrictionModel

logger = logging.getLogger("econith.quant.pretrade_gate")

__all__ = ["GateDecision", "PreTradeGate", "edge_bps_from_signal"]


# Regime-conditioned multiplier on the raw conviction->edge projection. Trending
# tape earns more per unit conviction than a choppy/mean-reverting book.
_REGIME_EDGE_SCALE: dict[str, float] = {
    "TRENDING": 1.15,
    "TREND": 1.15,
    "VOLATILE": 0.85,
    "MEAN_REVERTING": 0.90,
    "CALM": 0.70,
    "FLAT": 0.50,
    "UNKNOWN": 0.80,
}


def edge_bps_from_signal(
    direction: float,
    confidence: float,
    regime: str = "UNKNOWN",
    *,
    max_edge_bps: float = 18.0,
) -> float:
    """Project an *estimated* gross edge (bps) from AI conviction.

    Conviction = |direction| * confidence in [0, 1]; scaled by a regime factor
    and capped so a screaming signal can never fabricate unbounded expectancy.
    Returns a NON-negative bps figure (the side is handled by the caller).
    """
    conviction = max(0.0, min(1.0, abs(direction) * max(0.0, confidence)))
    scale = _REGIME_EDGE_SCALE.get(regime.upper(), 0.80)
    return conviction * scale * max_edge_bps


@dataclass(slots=True)
class GateDecision:
    """Verdict for one candidate order, with a full expectancy breakdown."""

    approved: bool
    reason: str
    expected_gross: float
    expected_fees: float
    expected_slippage_cost: float
    expected_net: float
    risk_score: float
    risk_threshold: float
    edge_source: str  # "model" | "estimated"

    def as_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "reason": self.reason,
            "expected_gross": round(self.expected_gross, 8),
            "expected_fees": round(self.expected_fees, 8),
            "expected_slippage_cost": round(self.expected_slippage_cost, 8),
            "expected_net": round(self.expected_net, 8),
            "risk_score": round(self.risk_score, 6),
            "risk_threshold": round(self.risk_threshold, 6),
            "edge_source": self.edge_source,
        }


@dataclass(slots=True)
class PreTradeGate:
    """Mandatory positive-expectancy filter in front of the execution bridge."""

    friction: EconithFrictionModel = None  # type: ignore[assignment]
    # Absolute floor on required net profit (quote ccy) so a ~0 edge that merely
    # clears fees by a rounding error is still rejected.
    min_net_profit: float = 0.0
    default_risk_threshold: float = 0.85

    def __post_init__(self) -> None:
        if self.friction is None:
            self.friction = EconithFrictionModel()

    def evaluate(
        self,
        *,
        price: float,
        quantity: float,
        side: str,
        expected_edge_bps: float,
        edge_source: str = "model",
        adv: float = 0.0,
        risk_score: float = 0.0,
        risk_threshold: float | None = None,
    ) -> GateDecision:
        """Return the approve/abort verdict for one candidate order.

        ``expected_edge_bps`` is the *gross* expected move captured by the trade
        in basis points (always non-negative — direction is already resolved by
        picking ``side``). ``adv`` scales square-root market impact.
        """
        threshold = (
            self.default_risk_threshold if risk_threshold is None else float(risk_threshold)
        )
        notional = abs(price) * abs(quantity)

        if notional <= 0.0:
            return self._abort(
                "zero_notional", 0.0, 0.0, 0.0, risk_score, threshold, edge_source
            )

        quote = self.friction.friction_quote(
            {"price": price, "quantity": quantity, "side": side}, {"adv": adv}
        )
        fees = quote.commission
        # Impact/half-spread cost = total friction beyond the commission line.
        slippage_cost = max(0.0, quote.total_cost - fees)

        expected_gross = notional * (max(0.0, expected_edge_bps) / 1e4)
        expected_net = expected_gross - fees - slippage_cost

        # --- risk budget check (checked first so it wins the abort reason) ---
        if risk_score > threshold:
            return self._abort(
                f"risk_exceeded({risk_score:.3f}>{threshold:.3f})",
                expected_gross, fees, slippage_cost, risk_score, threshold, edge_source,
            )

        # --- positive-expectancy check ---------------------------------------
        if expected_net <= self.min_net_profit:
            return GateDecision(
                approved=False,
                reason=f"non_positive_net({expected_net:.6f}<={self.min_net_profit:.6f})",
                expected_gross=expected_gross,
                expected_fees=fees,
                expected_slippage_cost=slippage_cost,
                expected_net=expected_net,
                risk_score=risk_score,
                risk_threshold=threshold,
                edge_source=edge_source,
            )

        return GateDecision(
            approved=True,
            reason="approved",
            expected_gross=expected_gross,
            expected_fees=fees,
            expected_slippage_cost=slippage_cost,
            expected_net=expected_net,
            risk_score=risk_score,
            risk_threshold=threshold,
            edge_source=edge_source,
        )

    @staticmethod
    def _abort(
        reason: str,
        gross: float,
        fees: float,
        slip: float,
        risk: float,
        threshold: float,
        edge_source: str,
    ) -> GateDecision:
        return GateDecision(
            approved=False,
            reason=reason,
            expected_gross=gross,
            expected_fees=fees,
            expected_slippage_cost=slip,
            expected_net=gross - fees - slip,
            risk_score=risk,
            risk_threshold=threshold,
            edge_source=edge_source,
        )
