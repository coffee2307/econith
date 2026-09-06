"""ECONITH :: WorldTensorState — contiguous NumPy world tensor.

Layout (C-contiguous, float64):
  hubs   : (N_HUBS, FEATURE_DIM)     full-fidelity core
  proxies: (N_PROXIES, FEATURE_DIM)  derived via CorrelationEngine

Zero object-list churn inside the hot tick path.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np

from econith.world.sovereign.topology import (
    FEATURE_DIM,
    FEATURE_NAMES,
    HUB_CODES,
    N_HUBS,
    N_PROXIES,
    PROXY_CODES,
    hub_index,
    proxy_index,
)

__all__ = ["WorldTensorState", "default_hub_seed"]


# Stable baseline vector used for synthetic seeding (deterministic).
_BASE = np.array(
    [
        1.0e12, 0.03, 20_000.0,  # gdp, growth, per_capita
        0.04, 0.02, 0.03, 0.028, 0.025, 1.0e12, 4.0e12, 6.0e12,
        0.05, 1.4, 5.0e11, 0.10, 0.045, 0.0, 0.06, 0.01, 0.04, 0.045, 1.0, 1.0,
        0.21, 0.30, 0.10, 0.15, 0.90, 0.04, 0.38, -0.02, -0.02, 100.0, 100.0,
        2.0e11, 1.5e11, 5.0e11, 0.85, 0.03, 0.01, 0.01, 0.04, 0.25, 0.12,
        0.75, 0.01, 0.08, 0.05,
        1.0e8, 0.62, 0.05, 0.07, 0.03, 100.0, 0.55, 0.012, 0.008, 0.001,
        38.0, 0.70, 0.38, 0.95, 0.45, 0.75, 100.0, 0.15, 0.12, 65.0, 7.25, 0.002,
        51.0, 52.0, 100.0, 0.78, 100.0, 100.0, 0.80, 5.0e9, 2.0e12, 1.0e10,
        1.0e6, 0.10, 100.0, 0.80, 0.30, 0.20, 0.75, 0.025, 100.0, 0.75,
        0.40, 0.25, 0.02, 0.10, 0.015,
        0.025, 0.50, 0.70, 0.55, 0.65, 0.60, 0.60, 0.20, 0.50, 0.55,
        0.50, 0.10, 0.70, 0.0, 0.60, 0.50, 0.20, 0.25,
    ],
    dtype=np.float64,
)
assert _BASE.shape == (FEATURE_DIM,)


# Curated GDP / growth overrides for the original majors (indices match HUB_CODES).
_HUB_OVERRIDES: dict[str, tuple[float, float, float]] = {
    "USA": (27.4e12, 0.025, 82_000),
    "CHN": (17.8e12, 0.048, 12_600),
    "DEU": (4.5e12, 0.006, 53_500),
    "JPN": (4.2e12, 0.011, 33_800),
    "IND": (3.7e12, 0.068, 2_600),
    "GBR": (3.3e12, 0.012, 48_000),
    "FRA": (3.0e12, 0.010, 44_000),
    "BRA": (2.2e12, 0.025, 10_000),
    "VNM": (0.43e12, 0.062, 4_300),
    "SAU": (1.1e12, 0.020, 30_000),
    "CAN": (2.1e12, 0.015, 54_000),
    "KOR": (1.7e12, 0.022, 33_000),
    "AUS": (1.6e12, 0.018, 60_000),
    "RUS": (2.0e12, 0.012, 14_000),
    "IDN": (1.4e12, 0.050, 5_000),
}


def default_hub_seed() -> np.ndarray:
    """Deterministic (N_HUBS, FEATURE_DIM) seed matrix."""
    mat = np.tile(_BASE, (N_HUBS, 1))
    rng = np.random.default_rng(20260708)  # fixed seed → reproducible Titan world
    # Mild per-hub jitter so hubs are distinguishable without hand-tuning 50 profiles.
    noise = 1.0 + rng.normal(0.0, 0.04, size=mat.shape)
    mat *= noise
    for code, (gdp, growth, pc) in _HUB_OVERRIDES.items():
        i = hub_index(code)
        mat[i, 0] = gdp
        mat[i, 1] = growth
        mat[i, 2] = pc
    return np.ascontiguousarray(mat, dtype=np.float64)


@dataclass(slots=True)
class WorldTensorState:
    """Single source of truth for the scaled sovereign world."""

    hubs: np.ndarray          # (N_HUBS, FEATURE_DIM)
    proxies: np.ndarray       # (N_PROXIES, FEATURE_DIM)
    tick: int = 0

    def __post_init__(self) -> None:
        if self.hubs.shape != (N_HUBS, FEATURE_DIM):
            raise ValueError(f"hubs shape {self.hubs.shape} != {(N_HUBS, FEATURE_DIM)}")
        if self.proxies.shape != (N_PROXIES, FEATURE_DIM):
            raise ValueError(
                f"proxies shape {self.proxies.shape} != {(N_PROXIES, FEATURE_DIM)}"
            )
        # Enforce contiguous float64 for zero-copy NumPy views.
        if not self.hubs.flags["C_CONTIGUOUS"] or self.hubs.dtype != np.float64:
            self.hubs = np.ascontiguousarray(self.hubs, dtype=np.float64)
        if not self.proxies.flags["C_CONTIGUOUS"] or self.proxies.dtype != np.float64:
            self.proxies = np.ascontiguousarray(self.proxies, dtype=np.float64)

    @classmethod
    def blank(cls) -> "WorldTensorState":
        return cls(
            hubs=default_hub_seed(),
            proxies=np.zeros((N_PROXIES, FEATURE_DIM), dtype=np.float64),
            tick=0,
        )

    # ----- accessors --------------------------------------------------------
    def hub_row(self, code: str) -> np.ndarray:
        return self.hubs[hub_index(code)]

    def proxy_row(self, code: str) -> np.ndarray:
        return self.proxies[proxy_index(code)]

    def feature_slice(self, name: str) -> np.ndarray:
        """All hub values for one named feature (length N_HUBS)."""
        try:
            idx = FEATURE_NAMES.index(name)
        except ValueError as exc:
            raise KeyError(name) from exc
        return self.hubs[:, idx]

    def snapshot(self) -> dict:
        """Atomic Sentinel-facing read-model (single tick consistent)."""
        return {
            "tick": self.tick,
            "n_hubs": N_HUBS,
            "n_proxies": N_PROXIES,
            "feature_dim": FEATURE_DIM,
            "hub_codes": list(HUB_CODES),
            "proxy_codes": list(PROXY_CODES),
            "hubs": self.hubs.copy(),
            "proxies": self.proxies.copy(),
        }

    def ingest_object_vectors(self, code_to_vec: dict[str, Iterable[float]]) -> int:
        """Overwrite hub rows from legacy CountryState.to_vector() dicts."""
        n = 0
        for code, vec in code_to_vec.items():
            if code not in HUB_CODES:
                continue
            arr = np.asarray(list(vec), dtype=np.float64)
            if arr.shape != (FEATURE_DIM,):
                raise ValueError(f"{code} vector shape {arr.shape}")
            self.hubs[hub_index(code)] = arr
            n += 1
        return n
