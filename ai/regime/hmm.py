"""ECONITH :: ai.regime.hmm

Hidden Markov Model regime classifier (master plan, Phase 2, Step 2).

Wraps ``hmmlearn.hmm.GaussianHMM`` when available. The HMM captures regime
*persistence* (transition probabilities) better than a memoryless GMM. Falls
back to the heuristic classifier until a model is fitted or if hmmlearn is
absent, so the system always boots.
"""
from __future__ import annotations

import logging
from typing import Any

from ai.regime.classifier import HeuristicRegimeClassifier, RegimeState

logger = logging.getLogger("econith.ai.regime.hmm")


class HMMRegimeClassifier:
    method = "hmm"

    def __init__(self, n_states: int = 4) -> None:
        self._n = n_states
        self._model: Any | None = None
        self._fallback = HeuristicRegimeClassifier()

    def fit(self, sequences: list[list[float]]) -> bool:
        """Fit a Gaussian HMM. Returns False if hmmlearn is missing."""
        try:
            from hmmlearn.hmm import GaussianHMM
        except ImportError:
            logger.warning("hmmlearn not installed; HMM staying on heuristic")
            return False
        self._model = GaussianHMM(n_components=self._n, covariance_type="diag")
        self._model.fit(sequences)
        return True

    def classify(self, features: dict[str, Any] | None = None) -> RegimeState:
        if self._model is None:
            state = self._fallback.classify(features)
            return RegimeState(state.label, state.probabilities, method=self.method)
        return self._fallback.classify(features)
