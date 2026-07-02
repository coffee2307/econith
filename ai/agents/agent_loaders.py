"""ECONITH :: ai.agents.agent_loaders  (The Trading Desks)

Seat the graduated apprentices at real desks on the exchange floor.

Economic analogy
----------------
Training produced three sealed "brains" (the PPO ``.zip`` checkpoints). This
module builds the **trading desks** where those brains sit down, receive the live
ticker tape, and voice an opinion every tick. A desk does three things:

  1. Reads the live feature row in the EXACT column order used during training
     (so the model sees inputs shaped identically to its classroom -- any drift
     here would be like handing a trader a report in a foreign unit system).
  2. Normalises those numbers with the SAME scaler saved at training time.
  3. Asks the policy network for its stance and converts it into a smooth,
     bounded opinion (direction in [-1,1], confidence in [0,1]) that plugs into
     the existing ensemble/fusion machinery unchanged.

Everything heavy (``stable-baselines3`` / ``torch``) is imported lazily, and if a
checkpoint or the ML stack is missing the loader simply returns nothing -- the
Predictor then falls back to the deterministic heuristic desks, so the live
system NEVER crashes for lack of a trained brain.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import numpy as np

from ai.agents.base import AgentSignal, BaseAgent

logger = logging.getLogger("econith.ai.agent_loaders")

# ---------------------------------------------------------------------------
#  CANONICAL FEATURE CONTRACT
#  This is the single source of truth for the PPO observation layout. BOTH the
#  training env (training/train_ppo.py) and these live desks import this list, so
#  the model can never be trained on one column order and served another.
# ---------------------------------------------------------------------------
PPO_FEATURE_COLS: list[str] = [
    "obi", "volume_delta", "buy_volume", "sell_volume", "trade_count",
    "funding_rate", "time_to_funding_s", "open_interest", "oi_change_pct",
    "liquidation_notional",
]

# active.yaml model-name -> environment-variable override (README contract).
_ENV_OVERRIDES = {
    "trend": "TREND_CHECKPOINT",
    "mean_reversion": "MEAN_REV_CHECKPOINT",
    "scalper": "SCALPER_CHECKPOINT",
    "hmm": "REGIME_CHECKPOINT",
    "world_neural": "WORLD_CHECKPOINT",
}

_PPO_AGENTS = ("trend", "mean_reversion", "scalper")


# ===========================================================================
#  Active-model resolution
# ===========================================================================
def resolve_active_models(
    model_dir: str | Path | None = None,
    registry: str | Path | None = None,
) -> dict[str, Path]:
    """Figure out which checkpoint file is 'live' for each model.

    Priority (highest first):
      1. Explicit environment variable per model (e.g. ``TREND_CHECKPOINT``).
      2. The ``active.yaml`` board written by the deploy customs gate.

    Returns a mapping ``name -> absolute path`` for every model we can locate.
    Missing models are simply absent from the dict (caller handles fallback).
    """
    root = Path(os.getenv("MODEL_DIR", str(model_dir or "./models")))
    reg = Path(os.getenv("MODEL_REGISTRY", str(registry or (root / "registry"))))
    resolved: dict[str, Path] = {}

    active = reg / "active.yaml"
    if active.exists():
        try:
            import yaml  # lazy: keeps the base backend free of a hard yaml dep

            data = yaml.safe_load(active.read_text()) or {}
            for name, entry in (data.get("models") or {}).items():
                rel = entry.get("path") if isinstance(entry, dict) else None
                if rel:
                    resolved[name] = (root / rel).resolve()
        except Exception as exc:  # noqa: BLE001 - a broken board must not crash boot
            logger.warning("could not read active.yaml (%s); relying on env vars", exc)

    # Environment overrides win -- lets you pin a specific file without a deploy.
    for name, var in _ENV_OVERRIDES.items():
        val = os.getenv(var)
        if val:
            resolved[name] = Path(val).resolve()

    return resolved


# ===========================================================================
#  A single trained trading desk
# ===========================================================================
class TrainedPPOAgent(BaseAgent):
    """A live desk wrapping one ``stable-baselines3`` PPO checkpoint.

    Implements the same :class:`BaseAgent` contract as the heuristic stubs, so it
    is a drop-in replacement -- the ensemble, fusion and Sentinel veto are none
    the wiser. Inference is CPU-only and lightweight (a 2-layer MLP forward pass),
    so it never bottlenecks the deterministic tick engine.
    """

    def __init__(self, name: str, checkpoint: str | Path) -> None:
        self.name = name
        self.feature_keys = tuple(PPO_FEATURE_COLS)
        self._path = Path(checkpoint)
        self._model: Any = None
        self._loaded = False
        # Normalisation stats saved next to the checkpoint at training time.
        self._cols = list(PPO_FEATURE_COLS)
        self._mean = np.zeros(len(PPO_FEATURE_COLS), dtype="float64")
        self._std = np.ones(len(PPO_FEATURE_COLS), dtype="float64")
        # The desk remembers its current stance (the env fed position as an input).
        self._pos = 0.0
        self._load_normalizer()

    # -- loading --------------------------------------------------------------
    def _load_normalizer(self) -> None:
        """Load the z-score stats the model was trained with (crucial for parity)."""
        norm_path = self._path.parent / f"{self._path.stem}.norm.json"
        if not norm_path.exists():
            logger.warning(
                "[%s] no normalizer sidecar at %s -- using identity scaling "
                "(retrain to regenerate it)", self.name, norm_path,
            )
            return
        try:
            data = json.loads(norm_path.read_text())
            self._cols = list(data.get("cols", PPO_FEATURE_COLS))
            self._mean = np.asarray(data["mean"], dtype="float64")
            self._std = np.asarray(data["std"], dtype="float64")
            self._std[self._std < 1e-8] = 1.0
        except (ValueError, KeyError, OSError) as exc:
            logger.warning("[%s] bad normalizer (%s); using identity", self.name, exc)

    def _ensure_model(self) -> Any:
        """Lazily load the PPO policy the first time the desk is asked to act."""
        if self._loaded:
            return self._model
        self._loaded = True
        if not self._path.exists():
            logger.warning("[%s] checkpoint missing: %s", self.name, self._path)
            return None
        try:
            from stable_baselines3 import PPO

            # device="cpu": serving a tiny MLP needs no GPU, and it keeps the
            # H200 free for training and avoids CUDA init cost in the API process.
            self._model = PPO.load(str(self._path), device="cpu")
            logger.info("[%s] loaded PPO desk <- %s", self.name, self._path)
        except ImportError:
            logger.warning("[%s] stable-baselines3 not installed -- desk offline", self.name)
            self._model = None
        except Exception as exc:  # noqa: BLE001 - never let a bad file crash boot
            logger.warning("[%s] failed to load PPO (%s)", self.name, exc)
            self._model = None
        return self._model

    @property
    def is_live(self) -> bool:
        return self._ensure_model() is not None

    # -- observation ----------------------------------------------------------
    def _observe(self, features: dict[str, Any]) -> np.ndarray:
        """Build the exact observation vector the model was trained on."""
        raw = np.array([self._f(features, c) for c in self._cols], dtype="float64")
        z = (raw - self._mean) / self._std
        z = np.clip(z, -10.0, 10.0)
        # Append the desk's current stance, exactly like the training env did.
        return np.append(z, self._pos).astype(np.float32)

    # -- inference ------------------------------------------------------------
    def act(self, features: dict[str, Any]) -> AgentSignal:
        model = self._ensure_model()
        if model is None:
            return AgentSignal(self.name, 0.0, 0.0, "desk offline (no model)")

        obs = self._observe(features)
        direction, confidence, action = self._policy_opinion(model, obs)

        # Update remembered stance for the next tick (deadband around flat).
        self._pos = 1.0 if direction > 0.05 else (-1.0 if direction < -0.05 else 0.0)

        stance = {0: "SHORT", 1: "FLAT", 2: "LONG"}.get(int(action), "FLAT")
        return AgentSignal(
            agent=self.name,
            direction=self.clamp(direction),
            confidence=max(0.0, min(1.0, confidence)),
            rationale=f"PPO[{self.name}] policy -> {stance}",
            contributions={"p_long_minus_short": round(direction, 4)},
        )

    def _policy_opinion(self, model: Any, obs: np.ndarray) -> tuple[float, float, int]:
        """Turn the discrete policy into a smooth (direction, confidence, action).

        We read the policy's action *probabilities* -- P(short), P(flat), P(long)
        -- so the opinion is graded rather than a hard flip: a barely-confident
        long and a screaming long are told apart. ``direction = P(long) - P(short)``
        naturally lives in [-1, 1]; ``confidence`` is the strongest probability.
        Falls back to a plain deterministic prediction if the distribution API
        is unavailable for any reason.
        """
        try:
            import torch

            obs_t, _ = model.policy.obs_to_tensor(obs)
            with torch.no_grad():
                dist = model.policy.get_distribution(obs_t)
                probs = dist.distribution.probs.detach().cpu().numpy().reshape(-1)
            p_short, p_flat, p_long = (float(probs[0]), float(probs[1]), float(probs[2]))
            direction = p_long - p_short
            confidence = max(p_short, p_flat, p_long)
            action = int(np.argmax(probs))
            return direction, confidence, action
        except Exception:  # noqa: BLE001 - robust fallback to hard prediction
            action_arr, _ = model.predict(obs, deterministic=True)
            action = int(np.asarray(action_arr).reshape(-1)[0])
            direction = float(action - 1)   # {0,1,2} -> {-1,0,+1}
            return direction, abs(direction), action


# ===========================================================================
#  Bulk loader
# ===========================================================================
def load_active_agents(
    model_dir: str | Path | None = None,
    registry: str | Path | None = None,
) -> list[BaseAgent]:
    """Load whichever PPO desks are live, in canonical [trend, mean_rev, scalper] order.

    Returns only the desks whose checkpoints actually exist and load. The caller
    (the Predictor) fills any gaps with heuristic stubs so the ensemble always has
    a full three-agent roster.
    """
    resolved = resolve_active_models(model_dir, registry)
    desks: list[BaseAgent] = []
    for name in _PPO_AGENTS:
        path = resolved.get(name)
        if not path:
            continue
        desk = TrainedPPOAgent(name, path)
        if desk.is_live:
            desks.append(desk)
    if desks:
        logger.info("live trading desks: %s", ", ".join(d.name for d in desks))
    return desks
