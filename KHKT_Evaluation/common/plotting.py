# -*- coding: utf-8 -*-
"""Minimal plotting helpers (matplotlib). Fail soft if unavailable."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def save_bar_chart(
    path: Path,
    labels: list[str],
    values: list[float],
    *,
    title: str,
    ylabel: str,
) -> bool:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    ax.bar(labels, values, color="#1F4E79")
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.tick_params(axis="x", rotation=15)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return True


def save_line_chart(
    path: Path,
    series: dict[str, Any],
    *,
    title: str,
    ylabel: str,
    max_points: int = 800,
) -> bool:
    try:
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(8, 4.2))
    for name, arr in series.items():
        y = np.asarray(arr, dtype=float)
        if y.size > max_points:
            idx = np.linspace(0, y.size - 1, max_points).astype(int)
            y = y[idx]
            x = idx
        else:
            x = np.arange(y.size)
        ax.plot(x, y, label=name, linewidth=1.2)
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return True
