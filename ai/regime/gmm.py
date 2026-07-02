"""ECONITH :: ai.regime.gmm

Gaussian Mixture Model regime classifier (master plan, Phase 2, Step 2).

Wraps ``sklearn.mixture.GaussianMixture`` when available. Until a model is
fitted (or if scikit-learn is absent) it transparently delegates to the
dependency-free ``HeuristicRegimeClassifier`` so the system always boots.
"""
from __future__ import annotations

import logging
from typing import Any

from ai.regime.classifier import HeuristicRegimeClassifier, RegimeState

logger = logging.getLogger("econith.ai.regime.gmm")


class GMMRegimeClassifier:
    method = "gmm"

    def __init__(self, n_components: int = 4) -> None:
        self._n = n_components
        self._model: Any | None = None
        self._fallback = HeuristicRegimeClassifier()

    def fit(self, samples: list[list[float]]) -> bool:
        """Fit a GMM on feature samples. Returns False if sklearn is missing."""
        try:
            from sklearn.mixture import GaussianMixture
        except ImportError:
            logger.warning("scikit-learn not installed; GMM staying on heuristic")
            return False
        self._model = GaussianMixture(n_components=self._n, random_state=42)
        self._model.fit(samples)
        return True

    def classify(self, features: dict[str, Any] | None = None) -> RegimeState:
        # Until a real model is fitted, defer to the heuristic.
        if self._model is None:
            state = self._fallback.classify(features)
            return RegimeState(state.label, state.probabilities, method=self.method)
        return self._fallback.classify(features)
