"""ECONITH :: ai.regime.regime  (The Weather Forecaster's Desk)

Read the live tape and call out what kind of market weather we're in.

Economic analogy
----------------
``fit_regime`` trained a forecaster on history; this desk puts that forecaster to
work in real time. Every tick it looks at how the market is *behaving* -- the size
and direction of moves, how stormy (volatile) it is, order-book pressure -- and
announces a probability across four kinds of weather:

    TRENDING        a steady directional march (ride it)
    MEAN_REVERTING  choppy, range-bound (fade the extremes)
    VOLATILE        stormy, high-variance (trade small / fast)
    CALM            quiet drift (little edge)

The trained model thinks in anonymous "hidden states" (state 0, 1, 2, 3). This
desk translates those into the four named regimes above by inspecting what each
state *looks like* (its average volatility and drift), so the rest of the system
keeps speaking the same language it always has (`RegimeState` + `REGIMES`).

If the model file or ML libraries are missing, ``load_active_regime`` returns
``None`` and the Predictor keeps using its built-in heuristic forecaster -- the
floor is never left without a weather call.
"""
from __future__ import annotations

import logging
from collections import deque
from pathlib import Path
from typing import Any

from ai.regime.classifier import REGIMES, RegimeState

logger = logging.getLogger("econith.ai.regime.live")

# The instrument order the model was trained on (must match training/fit_regime).
REGIME_FEATURE_ORDER = ("return", "volatility", "obi", "volume_delta")


class TrainedRegimeClassifier:
    """Live regime forecaster backed by a trained HMM (or GMM) bundle."""

    def __init__(self, bundle: dict, window: int = 64) -> None:
        self._model = bundle["model"]
        self._scaler = bundle["scaler"]
        self._backend = bundle.get("backend", "hmm")
        self._n_states = int(bundle.get("n_states", 4))
        self.method = self._backend

        # Rolling state so we can compute return + volatility from the price tape.
        self._prev_price: float | None = None
        self._returns: deque[float] = deque(maxlen=window)
        self._scaled_buffer: deque[list[float]] = deque(maxlen=window)

        # Precompute the hidden-state -> named-regime translation once.
        self._state_labels = self._label_states()

    # -- state translation ----------------------------------------------------
    def _label_states(self) -> list[str]:
        """Name each hidden state by inspecting its average drift + volatility.

        We pull each state's mean instrument reading back into real units, then
        score it with the same intuition the heuristic classifier uses:
          * lots of volatility        -> VOLATILE
          * quiet + little drift       -> CALM
          * strong persistent drift    -> TRENDING
          * moderate vol, low drift     -> MEAN_REVERTING
        """
        import numpy as np

        means = getattr(self._model, "means_", None)
        if means is None:
            # Some models expose no means; default every state to CALM (safe).
            return ["CALM"] * self._n_states
        means = np.asarray(means, dtype="float64")
        try:
            original = self._scaler.inverse_transform(means)
        except Exception:  # noqa: BLE001
            original = means

        ret = np.abs(original[:, 0])          # |return| == drift strength
        vol = original[:, 1]                  # rolling volatility

        def _mm(a):
            lo, hi = float(np.min(a)), float(np.max(a))
            return (a - lo) / (hi - lo) if hi - lo > 1e-12 else np.zeros_like(a)

        driftn, voln = _mm(ret), _mm(vol)
        labels: list[str] = []
        for i in range(len(means)):
            scores = {
                "TRENDING": 3.0 * driftn[i] - 1.5 * voln[i],
                "MEAN_REVERTING": 2.0 * voln[i] - 2.0 * driftn[i],
                "VOLATILE": 5.0 * voln[i],
                "CALM": 2.0 - 6.0 * voln[i] - 4.0 * driftn[i],
            }
            labels.append(max(scores, key=scores.get))
        logger.info("regime state map (%s): %s", self._backend, labels)
        return labels

    # -- live feature assembly ------------------------------------------------
    def _instruments(self, features: dict[str, Any]) -> list[float] | None:
        """Turn the live feature row into the 4 instruments the model expects."""
        price = features.get("price")
        if price is None:
            price = features.get("mid")
        try:
            price = float(price) if price is not None else None
        except (TypeError, ValueError):
            price = None

        ret = 0.0
        if price is not None and self._prev_price:
            ret = price / self._prev_price - 1.0
        if price is not None:
            self._prev_price = price
        self._returns.append(ret)

        # Rolling volatility == std of recent returns (our 'storminess' gauge).
        import numpy as np

        vol = float(np.std(self._returns)) if len(self._returns) > 1 else 0.0
        obi = _num(features.get("obi"))
        vd = _num(features.get("volume_delta"))
        return [ret, vol, obi, vd]

    # -- classification -------------------------------------------------------
    def classify(self, features: dict[str, Any] | None = None) -> RegimeState:
        if features is None:
            return RegimeState("CALM", {r: 0.25 for r in REGIMES}, method=self.method)

        instruments = self._instruments(features)
        import numpy as np

        try:
            scaled = self._scaler.transform([instruments])[0]
        except Exception:  # noqa: BLE001
            scaled = np.asarray(instruments, dtype="float64")
        self._scaled_buffer.append(list(scaled))

        posterior = self._posterior(np.asarray(self._scaled_buffer, dtype="float64"))
        if posterior is None:
            return RegimeState("CALM", {r: 0.25 for r in REGIMES}, method=self.method)

        # Fold hidden-state probabilities into the four named regimes.
        probs = {r: 0.0 for r in REGIMES}
        for state_idx, p in enumerate(posterior):
            label = self._state_labels[state_idx] if state_idx < len(self._state_labels) else "CALM"
            probs[label] += float(p)
        total = sum(probs.values()) or 1.0
        probs = {r: v / total for r, v in probs.items()}
        label = max(probs, key=probs.get)
        return RegimeState(label=label, probabilities=probs, method=self.method)

    def _posterior(self, seq: "Any"):
        """Get the per-state probability for the latest observation.

        For an HMM we hand it the recent sequence so the answer respects the
        market's momentum (weather has memory); for a GMM we score the single
        latest point. Returns a probability vector over hidden states, or None.
        """
        import numpy as np

        if seq.ndim != 2 or seq.shape[0] == 0:
            return None
        try:
            if self._backend == "hmm":
                gamma = self._model.predict_proba(seq)   # (T, n_states)
                return np.asarray(gamma[-1], dtype="float64")
            # GMM: posterior of the latest sample only.
            gamma = self._model.predict_proba(seq[-1:].reshape(1, -1))
            return np.asarray(gamma[0], dtype="float64")
        except Exception as exc:  # noqa: BLE001 - degrade gracefully to heuristic caller
            logger.debug("regime posterior failed (%s)", exc)
            return None


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


# ===========================================================================
#  Loader
# ===========================================================================
def load_active_regime(
    model_dir: str | Path | None = None,
    registry: str | Path | None = None,
) -> TrainedRegimeClassifier | None:
    """Load the live regime forecaster from the active registry, or ``None``.

    ``None`` is a valid, safe result: it tells the Predictor to keep using its
    dependency-free heuristic forecaster.
    """
    # Reuse the same resolution logic the trading desks use (env + active.yaml).
    from ai.agents.agent_loaders import resolve_active_models

    resolved = resolve_active_models(model_dir, registry)
    path = resolved.get("hmm")
    if not path or not Path(path).exists():
        return None

    try:
        import joblib

        bundle = joblib.load(path)
    except ImportError:
        logger.warning("joblib not installed -- regime model stays heuristic")
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to load regime bundle (%s)", exc)
        return None

    if not isinstance(bundle, dict) or "model" not in bundle or "scaler" not in bundle:
        logger.warning("regime bundle at %s has unexpected shape -- ignoring", path)
        return None

    logger.info("live regime forecaster loaded <- %s", path)
    return TrainedRegimeClassifier(bundle)
