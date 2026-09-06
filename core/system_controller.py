"""ECONITH :: core.system_controller

The **Main System Control** state machine (Task 1 backend).

This is the single, thread-safe coordinator the Main Control Dashboard drives.
It sits *above* the sovereign :class:`~core.mode.ModeManager` (which owns the
binary REALITY/SIMULATION coupling gate) and adds the richer operator surface
the product requires:

    OperatingMode              -> five explicit operator regimes
    world_simulation_enabled   -> the CRITICAL compute guardrail master switch
    world_to_quant_bridge      -> inject World state matrices into Quant

Design rules
------------
* **Sovereignty is never weakened.** Every operating mode resolves down to an
  underlying :class:`~core.mode.QuantMode`; the existing air-gaps read that
  singleton, so this layer can add behaviour but can never *bypass* isolation.
* **Compute guardrail is authoritative.** When ``world_simulation_enabled`` is
  OFF the World pipeline must suspend (the tick handler early-returns) AND the
  World->Quant bridge is force-disabled, regardless of the operating mode. This
  frees local CPU/GPU/RAM so the box runs on standard market pipelines only.
* **Listeners, not polling.** Subsystems subscribe to transitions so wiring
  (suspend/resume loops) stays event-driven and testable.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from core.mode import QuantMode, get_mode_manager

logger = logging.getLogger("econith.core.system_controller")

__all__ = [
    "OperatingMode",
    "SystemState",
    "SystemController",
    "get_system_controller",
]


class OperatingMode(str, Enum):
    """The five explicit operator regimes exposed on the control dashboard."""

    REALITY = "REALITY"                          # live data + real-time execution
    SIMULATION = "SIMULATION"                    # paper trading, historical/synthetic
    AUTONOMOUS_HYPOTHESIS = "AUTONOMOUS_HYPOTHESIS"  # AI self-generates shock tests
    USER_HYPOTHESIS = "USER_HYPOTHESIS"          # operator tweaks macro variables
    FULLY_AUTONOMOUS = "FULLY_AUTONOMOUS"        # self-operating data->train->deploy loop

    @classmethod
    def parse(cls, value: "str | OperatingMode | None", default: "OperatingMode") -> "OperatingMode":
        if isinstance(value, cls):
            return value
        if value is None:
            return default
        try:
            return cls(str(value).strip().upper())
        except ValueError:
            logger.warning("unknown OperatingMode %r; using %s", value, default.value)
            return default


# Each operating mode resolves to exactly one sovereign QuantMode. This mapping
# is the ONLY place operator regimes touch the isolation gate — keeping it small
# makes the security review trivial.
#
#   REALITY / FULLY_AUTONOMOUS  -> REALITY   (live capital path is possible)
#   everything else             -> SIMULATION (sandbox; live sockets refused)
_MODE_TO_QUANT: dict[OperatingMode, QuantMode] = {
    OperatingMode.REALITY: QuantMode.REALITY,
    OperatingMode.SIMULATION: QuantMode.SIMULATION,
    OperatingMode.AUTONOMOUS_HYPOTHESIS: QuantMode.SIMULATION,
    OperatingMode.USER_HYPOTHESIS: QuantMode.SIMULATION,
    OperatingMode.FULLY_AUTONOMOUS: QuantMode.REALITY,
}

# Modes that let the AI autonomously author economic shock hypotheses.
_AUTONOMOUS_HYPOTHESIS_MODES = frozenset(
    {OperatingMode.AUTONOMOUS_HYPOTHESIS, OperatingMode.FULLY_AUTONOMOUS}
)
# Modes that keep the self-operating retrain->backtest->deploy loop armed.
_AUTONOMOUS_LOOP_MODES = frozenset({OperatingMode.FULLY_AUTONOMOUS})

# Retrain→deploy autopilot is not wired yet — surface honesty to the UI.
AUTONOMOUS_LOOP_IMPLEMENTED = False
# HypothesisRunner is wired (generate → scenario → measure). Keep True in sync
# with ai.simulator_engine.hypothesis_runner being started from main.py.
AUTONOMOUS_HYPOTHESIS_IMPLEMENTED = True


def _env_world_default() -> bool:
    """WORLD_SIMULATION_DEFAULT: weak machines should prefer false."""
    import os

    raw = (os.getenv("WORLD_SIMULATION_DEFAULT") or "false").strip().lower()
    return raw in ("1", "true", "yes", "on")


@dataclass(slots=True)
class SystemState:
    """Serialisable snapshot for the dashboard read-model."""

    operating_mode: str
    quant_mode: str
    world_simulation_enabled: bool
    world_to_quant_bridge: bool
    autonomous_hypothesis: bool
    autonomous_loop: bool
    autonomous_loop_implemented: bool
    autonomous_hypothesis_implemented: bool
    coupling_effective: bool          # World actually feeds Quant right now
    compute_profile: str              # "FULL" | "MARKET_ONLY"

    def as_dict(self) -> dict[str, object]:
        return {
            "operating_mode": self.operating_mode,
            "quant_mode": self.quant_mode,
            "world_simulation_enabled": self.world_simulation_enabled,
            "world_to_quant_bridge": self.world_to_quant_bridge,
            "autonomous_hypothesis": self.autonomous_hypothesis,
            "autonomous_loop": self.autonomous_loop,
            "autonomous_loop_implemented": self.autonomous_loop_implemented,
            "autonomous_hypothesis_implemented": self.autonomous_hypothesis_implemented,
            "coupling_effective": self.coupling_effective,
            "compute_profile": self.compute_profile,
        }


# Listeners receive the full post-transition snapshot.
StateListener = Callable[[SystemState], None]


@dataclass
class SystemController:
    """Process-global operator control plane over the sovereign mode manager."""

    _mode: OperatingMode = OperatingMode.REALITY
    # Default comes from WORLD_SIMULATION_DEFAULT (weak machines: prefer false).
    _world_enabled: bool = field(default_factory=lambda: _env_world_default())
    # Default ON so the sovereign SIMULATION coupling behaviour is preserved for
    # any caller that drives ``core.mode`` directly (tests / invariant scripts);
    # the dashboard can suspend it explicitly via the bridge toggle.
    _bridge_requested: bool = True
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _listeners: list[StateListener] = field(default_factory=list)

    # -- reads ----------------------------------------------------------------
    @property
    def operating_mode(self) -> OperatingMode:
        with self._lock:
            return self._mode

    @property
    def world_simulation_enabled(self) -> bool:
        with self._lock:
            return self._world_enabled

    def is_autonomous_hypothesis(self) -> bool:
        with self._lock:
            return self._mode in _AUTONOMOUS_HYPOTHESIS_MODES

    def is_autonomous_loop(self) -> bool:
        with self._lock:
            return self._mode in _AUTONOMOUS_LOOP_MODES

    def world_pipeline_active(self) -> bool:
        """True only when the agent simulation should consume compute this tick.

        This is the guardrail every World stepper MUST honour: OFF => suspend.
        """
        with self._lock:
            return self._world_enabled

    def bridge_and_compute_open(self) -> bool:
        """True when the operator permits World->Quant coupling (compute + bridge).

        Deliberately does NOT consult the operating mode: the sovereign
        ``core.mode.coupling_enabled()`` remains the authoritative isolation gate
        that producers AND this flag together decide on. Defaults are permissive
        (both ON) so callers driving ``core.mode`` directly keep working.
        """
        with self._lock:
            return self._world_enabled and self._bridge_requested

    def coupling_effective(self) -> bool:
        """True only when World state may bias the Quant brain right now.

        Requires ALL of: the sovereign gate open (SIMULATION), the operator
        bridge toggle ON, and the compute master switch ON.
        """
        with self._lock:
            return (
                self._world_enabled
                and self._bridge_requested
                and _MODE_TO_QUANT[self._mode] is QuantMode.SIMULATION
            )

    # -- writes ---------------------------------------------------------------
    def set_mode(self, mode: "OperatingMode | str") -> SystemState:
        """Commit an operating-mode transition and sync the sovereign gate."""
        new = OperatingMode.parse(mode, self.operating_mode)
        with self._lock:
            self._mode = new
            quant = _MODE_TO_QUANT[new]
        # Sync the sovereign singleton OUTSIDE the lock (it has its own lock).
        get_mode_manager().set(quant)
        logger.info("operating mode -> %s (quant=%s)", new.value, quant.value)
        return self._commit()

    def set_world_simulation(self, enabled: bool) -> SystemState:
        """The CRITICAL compute guardrail. OFF suspends the agent pipeline."""
        with self._lock:
            self._world_enabled = bool(enabled)
        logger.info(
            "world simulation %s (compute guardrail)",
            "ENABLED" if enabled else "SUSPENDED — freeing CPU/GPU/RAM",
        )
        return self._commit()

    def set_world_to_quant_bridge(self, enabled: bool) -> SystemState:
        """Request injecting World state matrices into the Quant reward/state."""
        with self._lock:
            self._bridge_requested = bool(enabled)
        return self._commit()

    def on_change(self, listener: StateListener) -> None:
        with self._lock:
            self._listeners.append(listener)

    # -- serialisation --------------------------------------------------------
    def snapshot(self) -> SystemState:
        with self._lock:
            quant = _MODE_TO_QUANT[self._mode]
            return SystemState(
                operating_mode=self._mode.value,
                quant_mode=quant.value,
                world_simulation_enabled=self._world_enabled,
                world_to_quant_bridge=self._bridge_requested,
                autonomous_hypothesis=self._mode in _AUTONOMOUS_HYPOTHESIS_MODES,
                autonomous_loop=self._mode in _AUTONOMOUS_LOOP_MODES,
                autonomous_loop_implemented=AUTONOMOUS_LOOP_IMPLEMENTED,
                autonomous_hypothesis_implemented=AUTONOMOUS_HYPOTHESIS_IMPLEMENTED,
                coupling_effective=(
                    self._world_enabled
                    and self._bridge_requested
                    and quant is QuantMode.SIMULATION
                ),
                compute_profile="FULL" if self._world_enabled else "MARKET_ONLY",
            )

    # -- internals ------------------------------------------------------------
    def _commit(self) -> SystemState:
        state = self.snapshot()
        for cb in list(self._listeners):
            try:
                cb(state)
            except Exception:  # noqa: BLE001 - a bad listener must never break control
                logger.exception("system-state listener failed")
        return state


# --- module-level singleton --------------------------------------------------
_controller = SystemController()


def get_system_controller() -> SystemController:
    return _controller
