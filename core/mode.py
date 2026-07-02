"""ECONITH :: core.mode

Dual-mode operational architecture for the sovereign ECONITH Quant brain.

``QuantMode`` governs the single structural coupling that decides institutional
integrity: whether ECONITH World's *synthetic* ``world.micro_impact`` shock
vector is permitted to bias the live Quant trading intelligence.

    REALITY     Production / live-paper. Quant is a pristine, sovereign system
                driven ONLY by the real-world data plane (Binance WS + alt-data).
                ``world.micro_impact`` is HARD-BLOCKED from the Predictor and the
                regime layer. Synthetic anomaly injection is disabled. This is
                the safe default so the core brain can never be corrupted by
                simulation metrics during real operations.

    SIMULATION  Sandbox / auxiliary reinforcement learning. Quant and World
                couple dynamically: macro edits flow through ``cross_impact`` to
                generate ``world.micro_impact``, and anomaly injection + the
                simulated Sentinel veto path are enabled.

The mode is a process-global, thread-safe singleton so every subsystem
(Predictor, WorldKernel, the Sentinel-injection REST endpoints, the dashboard
telemetry) reads exactly one source of truth.
"""
from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from enum import Enum

logger = logging.getLogger("econith.core.mode")


class QuantMode(str, Enum):
    """The two sovereign operating regimes of ECONITH Quant."""

    REALITY = "REALITY"
    SIMULATION = "SIMULATION"

    @classmethod
    def parse(
        cls, value: "str | QuantMode | None", default: "QuantMode"
    ) -> "QuantMode":
        """Coerce arbitrary input into a QuantMode, falling back to ``default``."""
        if isinstance(value, cls):
            return value
        if value is None:
            return default
        try:
            return cls(str(value).strip().upper())
        except ValueError:
            logger.warning("unknown QuantMode %r; using %s", value, default.value)
            return default


# Listeners receive ``(previous, new)`` on every committed transition.
ModeListener = Callable[[QuantMode, QuantMode], None]

# Safe institutional default: REALITY keeps the trading brain pristine unless an
# operator explicitly opts into the SIMULATION sandbox.
DEFAULT_MODE: QuantMode = QuantMode.parse(os.getenv("QUANT_MODE"), QuantMode.REALITY)


class ModeManager:
    """Process-global holder of the active :class:`QuantMode`.

    Thread-safe via a re-entrant lock so it is equally correct when read from
    the asyncio event loop and mutated from a FastAPI request handler thread.
    """

    def __init__(self, initial: QuantMode = DEFAULT_MODE) -> None:
        self._mode = initial
        self._lock = threading.RLock()
        self._listeners: list[ModeListener] = []

    # -- reads ----------------------------------------------------------------
    @property
    def mode(self) -> QuantMode:
        with self._lock:
            return self._mode

    def get(self) -> QuantMode:
        return self.mode

    def is_reality(self) -> bool:
        return self.mode is QuantMode.REALITY

    def is_simulation(self) -> bool:
        return self.mode is QuantMode.SIMULATION

    def coupling_enabled(self) -> bool:
        """True only when World -> Quant micro_impact coupling is permitted."""
        return self.mode is QuantMode.SIMULATION

    def anomaly_injection_enabled(self) -> bool:
        """True only when synthetic anomaly injection is permitted."""
        return self.mode is QuantMode.SIMULATION

    # -- writes ---------------------------------------------------------------
    def set(self, mode: "QuantMode | str") -> QuantMode:
        """Commit a mode transition and notify listeners. Idempotent."""
        new = QuantMode.parse(mode, self.mode)
        with self._lock:
            prev = self._mode
            if new is prev:
                return prev
            self._mode = new
            listeners = list(self._listeners)
        logger.info("QuantMode transition %s -> %s", prev.value, new.value)
        for cb in listeners:
            try:
                cb(prev, new)
            except Exception:  # noqa: BLE001 - a bad listener must never break switching
                logger.exception("QuantMode listener failed")
        return new

    def toggle(self) -> QuantMode:
        """Flip REALITY <-> SIMULATION."""
        return self.set(
            QuantMode.SIMULATION if self.is_reality() else QuantMode.REALITY
        )

    def on_change(self, listener: ModeListener) -> None:
        with self._lock:
            self._listeners.append(listener)

    # -- serialisation --------------------------------------------------------
    def snapshot(self) -> dict:
        """Serialisable view for the dashboard telemetry read-model."""
        m = self.mode
        return {
            "mode": m.value,
            "coupling_enabled": m is QuantMode.SIMULATION,
            "anomaly_injection_enabled": m is QuantMode.SIMULATION,
        }


# --- module-level singleton + convenience accessors --------------------------
_manager = ModeManager()


def get_mode_manager() -> ModeManager:
    return _manager


def current_mode() -> QuantMode:
    return _manager.mode


def set_mode(mode: "QuantMode | str") -> QuantMode:
    return _manager.set(mode)


def coupling_enabled() -> bool:
    return _manager.coupling_enabled()


def anomaly_injection_enabled() -> bool:
    return _manager.anomaly_injection_enabled()
