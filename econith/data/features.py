"""ECONITH :: econith.data.features

Native rolling feature extraction — internalizes the Qlib operator idea (rolling
mean / std / z-score / return) as pure Python, dependency-free, so the training
loaders can format tensors without importing the vendor package.
"""
from __future__ import annotations

from collections.abc import Sequence

__all__ = ["rolling_mean", "rolling_std", "zscore", "pct_return", "EconithFeatureSet"]


def rolling_mean(series: Sequence[float], window: int) -> list[float]:
    n = len(series)
    out = [0.0] * n
    acc = 0.0
    for i in range(n):
        acc += series[i]
        if i >= window:
            acc -= series[i - window]
        denom = min(i + 1, window)
        out[i] = acc / denom if denom else 0.0
    return out


def rolling_std(series: Sequence[float], window: int) -> list[float]:
    n = len(series)
    means = rolling_mean(series, window)
    out = [0.0] * n
    for i in range(n):
        lo = max(0, i - window + 1)
        seg = series[lo : i + 1]
        m = means[i]
        var = sum((x - m) ** 2 for x in seg) / len(seg) if seg else 0.0
        out[i] = var ** 0.5
    return out


def zscore(series: Sequence[float], window: int) -> list[float]:
    means = rolling_mean(series, window)
    stds = rolling_std(series, window)
    return [
        (series[i] - means[i]) / stds[i] if stds[i] > 1e-12 else 0.0
        for i in range(len(series))
    ]


def pct_return(series: Sequence[float]) -> list[float]:
    n = len(series)
    out = [0.0] * n
    for i in range(1, n):
        prev = series[i - 1]
        out[i] = (series[i] / prev - 1.0) if prev else 0.0
    return out


class EconithFeatureSet:
    """Build a small, deterministic rolling feature block from a price series."""

    def __init__(self, window: int = 20) -> None:
        self._w = window

    def build(self, prices: Sequence[float]) -> dict[str, list[float]]:
        return {
            "mean": rolling_mean(prices, self._w),
            "std": rolling_std(prices, self._w),
            "zscore": zscore(prices, self._w),
            "ret": pct_return(prices),
        }
