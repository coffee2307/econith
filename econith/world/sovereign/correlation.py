"""ECONITH :: Proxy CorrelationEngine — matrix derivative layer.

ProxyState = CorrelationMatrix @ HubState

Proxies have NO independent logic kernels. One sparse multiply updates all
100 proxies from the settled hub tensor in O(nnz × FEATURE_DIM).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from econith.world.sovereign.topology import (
    FEATURE_DIM,
    HUB_CODES,
    N_HUBS,
    N_PROXIES,
    PROXY_CODES,
    PROXY_WEIGHTS,
    hub_index,
)

__all__ = ["CorrelationEngine", "SparseCorr", "build_correlation_matrix"]


@dataclass(slots=True)
class SparseCorr:
    """Minimal CSR-like structure without a SciPy dependency."""

    indptr: np.ndarray   # (N_PROXIES + 1,)
    indices: np.ndarray  # (nnz,) hub indices
    data: np.ndarray     # (nnz,) weights
    shape: tuple[int, int]

    def multiply(self, hubs: np.ndarray) -> np.ndarray:
        """(N_PROXIES, F) = sparse @ (N_HUBS, F) without densifying W."""
        n_p, _ = self.shape
        f = hubs.shape[1]
        out = np.zeros((n_p, f), dtype=np.float64)
        for r in range(n_p):
            a, b = int(self.indptr[r]), int(self.indptr[r + 1])
            if a == b:
                continue
            cols = self.indices[a:b]
            w = self.data[a:b]
            # w @ hubs[cols] → (F,)
            out[r] = w @ hubs[cols]
        return out

    @property
    def nnz(self) -> int:
        return int(self.data.size)


def build_correlation_matrix() -> SparseCorr:
    hub_set = set(HUB_CODES)
    indptr = [0]
    indices: list[int] = []
    data: list[float] = []

    for code in PROXY_CODES:
        weights = PROXY_WEIGHTS.get(code, {"USA": 1.0})
        filtered = {h: w for h, w in weights.items() if h in hub_set}
        if not filtered:
            filtered = {"USA": 1.0}
        total = sum(filtered.values()) or 1.0
        for hub, w in filtered.items():
            indices.append(hub_index(hub))
            data.append(w / total)
        indptr.append(len(indices))

    mat = SparseCorr(
        indptr=np.asarray(indptr, dtype=np.int32),
        indices=np.asarray(indices, dtype=np.int32),
        data=np.asarray(data, dtype=np.float64),
        shape=(N_PROXIES, N_HUBS),
    )
    # Row-stochastic validation
    for r in range(N_PROXIES):
        a, b = int(mat.indptr[r]), int(mat.indptr[r + 1])
        s = float(mat.data[a:b].sum()) if a != b else 0.0
        if abs(s - 1.0) > 1e-9:
            raise RuntimeError(f"proxy row {r} ({PROXY_CODES[r]}) sum={s}")
    return mat


class CorrelationEngine:
    """Derive all proxy rows from hub tensor in one sparse multiply."""

    def __init__(self, matrix: SparseCorr | None = None) -> None:
        self._W = matrix if matrix is not None else build_correlation_matrix()
        self._frozen = np.zeros(N_PROXIES, dtype=bool)

    @property
    def matrix(self) -> SparseCorr:
        return self._W

    def freeze(self, proxy_idx: int, frozen: bool = True) -> None:
        self._frozen[proxy_idx] = frozen

    def propagate(self, hubs: np.ndarray, out: np.ndarray | None = None) -> np.ndarray:
        if hubs.shape != (N_HUBS, FEATURE_DIM):
            raise ValueError(f"hubs shape {hubs.shape}")
        derived = self._W.multiply(hubs)
        if out is None:
            return np.ascontiguousarray(derived, dtype=np.float64)
        if self._frozen.any():
            mask = ~self._frozen
            out[mask] = derived[mask]
        else:
            np.copyto(out, derived)
        return out
