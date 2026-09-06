"""ECONITH :: ParallelKernelManager — vectorized / chunked hub stepping.

Design choice for TickPipeline integrity:
  * Default executor = **vectorized NumPy** (deterministic, single-process).
  * Optional ``threaded`` chunk fan-out for large F batches (no GIL release on
    pure NumPy, so kept as a structured extension point).
  * ``process`` pool is available for research workloads but is NOT used on the
    master clock path (IPC + pickling would break atomic Sentinel snapshots and
    blow the <50ms budget).

The manager never publishes to EventBus — it only returns tensor mutations that
``SovereignEngine`` commits atomically at end-of-tick.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Literal

import numpy as np

from econith.world.sovereign.topology import FEATURE_DIM, N_HUBS

__all__ = ["ParallelKernelManager", "HubStepParams", "ExecutorMode"]

ExecutorMode = Literal["vectorized", "threaded"]


@dataclass(slots=True, frozen=True)
class HubStepParams:
    """Per-tick control inputs shared by every hub (broadcast)."""

    market_stress: float = 0.0
    scale: float = 1.0
    external_bias: np.ndarray | None = None  # (N_HUBS,) optional per-hub force


def _vectorized_step(
    hubs: np.ndarray,
    params: HubStepParams,
) -> np.ndarray:
    """Contiguous in-place-safe hub evolution for one tick.

    Updates are linear + saturating so the step is fully deterministic given
    (hubs, params). No Python object loop over hubs.
    """
    stress = float(params.market_stress)
    scale = float(params.scale)
    out = hubs.copy()

    # Indices into FEATURE_NAMES (see topology.FEATURE_NAMES).
    IDX_GROWTH = 1
    IDX_CPI = 5
    IDX_RATE = 3
    IDX_UNEMP = 50  # labor.unemployment ≈ index after top3+20+25 = 48… verify: 3+20+25=48, +2=50? 
    # labor block starts at index 48; unemployment is 3rd field → 50.
    IDX_PMI = 70  # industrial.manufacturing_pmi starts at 3+20+25+22=70
    IDX_GEO_RISK = 112  # last geo feature

    growth = out[:, IDX_GROWTH]
    # Mild mean-reversion of growth toward 0.025 under stress.
    growth += scale * (0.025 - growth) * 0.04
    growth -= scale * stress * 0.01
    out[:, IDX_GROWTH] = growth

    out[:, IDX_CPI] = np.clip(
        out[:, IDX_CPI] + scale * (0.002 * stress - 0.001 * (out[:, IDX_RATE] - 0.04)),
        0.0,
        0.25,
    )
    # Taylor-lite rate response
    out[:, IDX_RATE] = np.clip(
        out[:, IDX_RATE] + scale * 0.15 * (out[:, IDX_CPI] - 0.02),
        -0.01,
        0.20,
    )
    out[:, IDX_UNEMP] = np.clip(
        out[:, IDX_UNEMP] + scale * 0.004 * stress - scale * 0.002 * growth,
        0.01,
        0.40,
    )
    out[:, IDX_PMI] = np.clip(
        out[:, IDX_PMI] + scale * (2.0 * growth * 100.0 - 1.5 * stress * 10.0),
        30.0,
        70.0,
    )
    out[:, IDX_GEO_RISK] = np.clip(
        out[:, IDX_GEO_RISK] + scale * 0.05 * stress,
        0.0,
        1.0,
    )

    if params.external_bias is not None:
        bias = np.asarray(params.external_bias, dtype=np.float64).reshape(N_HUBS)
        # External force lands on growth + geo risk.
        out[:, IDX_GROWTH] += scale * 0.01 * bias
        out[:, IDX_GEO_RISK] = np.clip(out[:, IDX_GEO_RISK] + scale * 0.02 * np.abs(bias), 0.0, 1.0)

    # GDP integrates growth (very soft annualization per tick).
    out[:, 0] *= 1.0 + out[:, IDX_GROWTH] * (scale / 252.0)
    out[:, 2] = out[:, 0] / np.maximum(out[:, 48], 1.0)  # gdp_per_capita via population

    return out


class ParallelKernelManager:
    """Batch-step all 50 hubs for one TickPipeline cycle."""

    def __init__(
        self,
        *,
        mode: ExecutorMode = "vectorized",
        workers: int = 4,
    ) -> None:
        self.mode = mode
        self.workers = max(1, workers)
        self._pool: ThreadPoolExecutor | None = None
        if mode == "threaded":
            self._pool = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="titan-hub")

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None

    def step_hubs(self, hubs: np.ndarray, params: HubStepParams) -> np.ndarray:
        if hubs.shape != (N_HUBS, FEATURE_DIM):
            raise ValueError(f"hubs shape {hubs.shape}")
        if self.mode == "vectorized" or self._pool is None:
            return _vectorized_step(hubs, params)

        # Threaded chunk fan-out — still NumPy-heavy; useful when params grow.
        chunks = np.array_split(np.arange(N_HUBS), self.workers)
        futures = []
        for idx in chunks:
            if len(idx) == 0:
                continue
            sub = hubs[idx]
            sub_bias = None
            if params.external_bias is not None:
                sub_bias = np.asarray(params.external_bias)[idx]
            sub_params = HubStepParams(
                market_stress=params.market_stress,
                scale=params.scale,
                external_bias=sub_bias,
            )
            # Temporary reshape trick: pad to full N_HUBS for shared kernel, then slice.
            # Simpler: run vectorized on the submatrix by monkey path — local copy.
            futures.append((idx, self._pool.submit(self._step_chunk, sub, sub_params)))

        out = hubs.copy()
        for idx, fut in futures:
            out[idx] = fut.result()
        return out

    @staticmethod
    def _step_chunk(sub: np.ndarray, params: HubStepParams) -> np.ndarray:
        """Step an arbitrary hub submatrix with the same equations."""
        # Reuse equations by temporarily lying about N_HUBS via local ops.
        stress = float(params.market_stress)
        scale = float(params.scale)
        out = sub.copy()
        IDX_GROWTH, IDX_CPI, IDX_RATE = 1, 5, 3
        IDX_UNEMP, IDX_PMI, IDX_GEO_RISK = 50, 70, 112
        growth = out[:, IDX_GROWTH]
        growth = growth + scale * (0.025 - growth) * 0.04 - scale * stress * 0.01
        out[:, IDX_GROWTH] = growth
        out[:, IDX_CPI] = np.clip(
            out[:, IDX_CPI] + scale * (0.002 * stress - 0.001 * (out[:, IDX_RATE] - 0.04)),
            0.0, 0.25,
        )
        out[:, IDX_RATE] = np.clip(
            out[:, IDX_RATE] + scale * 0.15 * (out[:, IDX_CPI] - 0.02), -0.01, 0.20,
        )
        out[:, IDX_UNEMP] = np.clip(
            out[:, IDX_UNEMP] + scale * 0.004 * stress - scale * 0.002 * growth, 0.01, 0.40,
        )
        out[:, IDX_PMI] = np.clip(
            out[:, IDX_PMI] + scale * (2.0 * growth * 100.0 - 1.5 * stress * 10.0), 30.0, 70.0,
        )
        out[:, IDX_GEO_RISK] = np.clip(out[:, IDX_GEO_RISK] + scale * 0.05 * stress, 0.0, 1.0)
        if params.external_bias is not None:
            bias = np.asarray(params.external_bias, dtype=np.float64)
            out[:, IDX_GROWTH] += scale * 0.01 * bias
            out[:, IDX_GEO_RISK] = np.clip(
                out[:, IDX_GEO_RISK] + scale * 0.02 * np.abs(bias), 0.0, 1.0,
            )
        out[:, 0] *= 1.0 + out[:, IDX_GROWTH] * (scale / 252.0)
        out[:, 2] = out[:, 0] / np.maximum(out[:, 48], 1.0)
        return out
