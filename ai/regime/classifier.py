"""ECONITH :: ai.regime.classifier

Market-regime classification (master plan, Phase 2, Step 2).

Returns a probability distribution over discrete regimes from microstructure
features. Phase 2 ships a dependency-free heuristic classifier; the HMM/GMM
variants (``hmm.py`` / ``gmm.py``) implement the same ``classify`` contract and
fall back to this heuristic when their ML libraries are unavailable.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

# Discrete regimes the switcher allocates against.
REGIMES = ("TRENDING", "MEAN_REVERTING", "VOLATILE", "CALM")


@dataclass(slots=True, frozen=True)
class RegimeState:
    label: str
    probabilities: dict[str, float] = field(default_factory=dict)
    method: str = "heuristic"

    @property
    def confidence(self) -> float:
        return self.probabilities.get(self.label, 0.0)


def _softmax(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    mx = max(scores.values())
    exps = {k: math.exp(v - mx) for k, v in scores.items()}
    total = sum(exps.values()) or 1.0
    return {k: v / total for k, v in exps.items()}


class HeuristicRegimeClassifier:
    """Stateful heuristic over a rolling window of OBI / volume-delta."""

    method = "heuristic"

    def __init__(self, window: int = 60) -> None:
        self._obi: deque[float] = deque(maxlen=window)
        self._vd: deque[float] = deque(maxlen=window)

    def update(self, features: dict[str, Any]) -> None:
        try:
            self._obi.append(float(features.get("obi", 0.0) or 0.0))
            self._vd.append(float(features.get("volume_delta", 0.0) or 0.0))
        except (TypeError, ValueError):
            pass

    def _stats(self) -> tuple[float, float, float]:
        n = len(self._obi)
        if n == 0:
            return 0.0, 0.0, 0.0
        mean_obi = sum(self._obi) / n
        var_obi = sum((x - mean_obi) ** 2 for x in self._obi) / n
        std_obi = math.sqrt(var_obi)
        abs_drift = abs(mean_obi)
        return mean_obi, std_obi, abs_drift

    def classify(self, features: dict[str, Any] | None = None) -> RegimeState:
        if features is not None:
            self.update(features)
        _, std_obi, abs_drift = self._stats()

        # Heuristic scores per regime (higher == more likely).
        scores = {
            "TRENDING": 3.0 * abs_drift - 1.5 * std_obi,
            "MEAN_REVERTING": 2.0 * std_obi - 2.0 * abs_drift,
            "VOLATILE": 5.0 * std_obi,
            "CALM": 2.0 - 6.0 * std_obi - 4.0 * abs_drift,
        }
        probs = _softmax(scores)
        label = max(probs, key=probs.get) if probs else "CALM"
        return RegimeState(label=label, probabilities=probs, method=self.method)
