"""ECONITH :: econith.base

Native kernel base contract for refactored in-house engines.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.mode import QuantMode, current_mode

__all__ = ["BaseKernel"]


@dataclass(slots=True)
class BaseKernel:
    """Small contract every native ECONITH kernel follows.

    Kernels are deterministic, step-driven units invoked by TickPipeline or
    bridge handlers; they never spawn their own blocking scheduler loops.
    """

    name: str
    simulation_only: bool = False

    def ensure_mode(self) -> None:
        if self.simulation_only and current_mode() is QuantMode.REALITY:
            raise RuntimeError(f"{self.name} is SIMULATION-only")

