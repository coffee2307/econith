"""ECONITH :: econith.world.sovereign.observability — AgentObservabilityBuffer.

Agents must NOT read the live world tensor directly. If they do, they trade on
**perfect, instantaneous, noise-free** information — which is both unrealistic
and a subtle look-ahead leak (an agent reacting to a shock on the exact tick it
is committed). This buffer sits between the committed :class:`WorldTensorState`
and every consumer, injecting three sources of realistic epistemic friction:

  1. **Time delay** — the agent sees the world as it was ``delay`` ticks ago.
  2. **Gaussian observation noise** — multiplicative measurement error, so two
     agents observing the same state disagree (heterogeneous beliefs).
  3. **Update-frequency throttling** — an agent only refreshes its view every
     ``throttle`` ticks; between refreshes it acts on a *stale* cached snapshot.

Together these produce **asymmetric information**: fast/low-noise agents get an
edge, slow/noisy agents lag — exactly the dispersion that drives non-trivial,
non-repeating market dynamics.

The buffer stores committed hub (and optional proxy) tensors in a ring buffer.
Observations are produced lazily and cached per agent, so the per-tick cost is
just a bounded-deque append (O(1)); noise is only drawn on an actual refresh.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

from econith.world.sovereign.topology import FEATURE_DIM, N_HUBS

__all__ = ["ObservationProfile", "AgentObservation", "AgentObservabilityBuffer"]


@dataclass(slots=True)
class ObservationProfile:
    """Per-agent epistemic configuration."""

    delay: int = 1            # ticks of lag between world and observation
    noise_std: float = 0.02   # multiplicative gaussian measurement error (σ)
    throttle: int = 1         # refresh cadence in ticks (1 == every tick)
    seed: int | None = None


@dataclass(slots=True)
class AgentObservation:
    """A single agent's (delayed, noised, throttled) view of the world."""

    tick: int                 # the tick this observation was refreshed on
    source_tick: int          # the world tick actually observed (tick − delay)
    hubs: np.ndarray          # (N_HUBS, FEATURE_DIM) perceived hub tensor
    proxies: np.ndarray | None = None


@dataclass(slots=True)
class _AgentSlot:
    profile: ObservationProfile
    rng: np.random.Generator
    last_refresh: int = -1
    cache: AgentObservation | None = None


class AgentObservabilityBuffer:
    """Ring buffer decoupling committed world state from agent perception."""

    def __init__(self, maxlen: int = 64) -> None:
        # maxlen bounds the deepest supported delay; deque drops old ticks.
        self._maxlen = max(2, int(maxlen))
        self._hubs: deque[tuple[int, np.ndarray]] = deque(maxlen=self._maxlen)
        self._proxies: deque[tuple[int, np.ndarray]] = deque(maxlen=self._maxlen)
        self._agents: dict[str, _AgentSlot] = {}
        self._latest_tick: int = -1

    # -- registration ---------------------------------------------------------
    def register_agent(self, agent_id: str, profile: ObservationProfile | None = None) -> None:
        p = profile or ObservationProfile()
        self._agents[agent_id] = _AgentSlot(profile=p, rng=np.random.default_rng(p.seed))

    def profile(self, agent_id: str) -> ObservationProfile | None:
        slot = self._agents.get(agent_id)
        return slot.profile if slot else None

    # -- ingestion (hot path, O(1)) ------------------------------------------
    def push(self, tick: int, hubs: np.ndarray, proxies: np.ndarray | None = None) -> None:
        """Record the committed world tensor for ``tick``.

        Stores an owned copy so later mutation of the live tensor never
        retro-actively changes what agents will observe.
        """
        if hubs.shape != (N_HUBS, FEATURE_DIM):
            raise ValueError(f"hubs shape {hubs.shape}")
        self._hubs.append((int(tick), hubs.copy()))
        if proxies is not None:
            self._proxies.append((int(tick), proxies.copy()))
        self._latest_tick = int(tick)

    # -- observation (lazy, cached) ------------------------------------------
    def observe(self, agent_id: str, tick: int) -> AgentObservation | None:
        """Return ``agent_id``'s current perception at ``tick``.

        Honours the agent's delay, throttle and noise. Between throttled
        refreshes the previously cached (increasingly stale) observation is
        returned unchanged. Returns ``None`` until enough history exists.
        """
        slot = self._agents.get(agent_id)
        if slot is None:
            # Unregistered agents get default friction rather than perfect info.
            self.register_agent(agent_id)
            slot = self._agents[agent_id]

        p = slot.profile
        due = slot.cache is None or (tick - slot.last_refresh) >= max(1, p.throttle)
        if not due:
            return slot.cache

        source_tick = tick - max(0, p.delay)
        base = self._lookup(self._hubs, source_tick)
        if base is None:
            return slot.cache  # not enough history yet

        perceived = self._apply_noise(base, slot.rng, p.noise_std)
        prox_base = self._lookup(self._proxies, source_tick)
        perceived_prox = (
            self._apply_noise(prox_base, slot.rng, p.noise_std)
            if prox_base is not None else None
        )

        slot.cache = AgentObservation(
            tick=int(tick),
            source_tick=int(source_tick),
            hubs=perceived,
            proxies=perceived_prox,
        )
        slot.last_refresh = int(tick)
        return slot.cache

    # -- helpers --------------------------------------------------------------
    @staticmethod
    def _lookup(ring: "deque[tuple[int, np.ndarray]]", tick: int) -> np.ndarray | None:
        """Newest recorded tensor with recorded_tick <= tick (nearest past)."""
        best: np.ndarray | None = None
        best_tick = -1
        for t, arr in ring:
            if t <= tick and t > best_tick:
                best_tick, best = t, arr
        return best

    @staticmethod
    def _apply_noise(arr: np.ndarray, rng: np.random.Generator, noise_std: float) -> np.ndarray:
        """Multiplicative gaussian measurement error (scale-invariant per feature)."""
        if noise_std <= 0.0:
            return arr.copy()
        factor = 1.0 + noise_std * rng.standard_normal(arr.shape)
        return np.ascontiguousarray(arr * factor, dtype=np.float64)

    @property
    def latest_tick(self) -> int:
        return self._latest_tick
