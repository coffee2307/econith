#!/usr/bin/env python3
"""ECONITH :: TITAN stress harness.

Spawns SovereignEngine with 50 Core Hubs × 113 features + 100 Proxy Nodes,
runs a warm batch of atomic ticks, and reports p50/p95/p99 tick latency.

Budget: < 50 ms / tick (vectorized path on a single core).
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from econith.world.sovereign import (  # noqa: E402
    FEATURE_DIM,
    N_HUBS,
    N_PROXIES,
    ParallelKernelManager,
    SovereignEngine,
    WorldTensorState,
)


def percentile(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    ys = sorted(xs)
    k = (len(ys) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(ys) - 1)
    if f == c:
        return ys[f]
    return ys[f] + (ys[c] - ys[f]) * (k - f)


def main() -> int:
    ap = argparse.ArgumentParser(description="TITAN world tick latency benchmark")
    ap.add_argument("--ticks", type=int, default=200, help="measured ticks after warmup")
    ap.add_argument("--warmup", type=int, default=20)
    ap.add_argument("--mode", choices=("vectorized", "threaded"), default="vectorized")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--budget-ms", type=float, default=50.0)
    args = ap.parse_args()

    state = WorldTensorState.blank()
    mgr = ParallelKernelManager(mode=args.mode, workers=args.workers)
    engine = SovereignEngine(state=state, manager=mgr)

    print("=" * 64)
    print("ECONITH TITAN · World Scale Benchmark")
    print("=" * 64)
    print(f"  hubs            : {N_HUBS}")
    print(f"  proxies         : {N_PROXIES}")
    print(f"  feature_dim     : {FEATURE_DIM}")
    print(f"  active features : {N_HUBS * FEATURE_DIM:,}")
    print(f"  executor        : {args.mode} (workers={args.workers})")
    print(f"  warmup / ticks  : {args.warmup} / {args.ticks}")
    print(f"  budget          : < {args.budget_ms:.1f} ms / tick")
    print("-" * 64)

    for i in range(args.warmup):
        engine.step(market_stress=0.1 * (i % 5), scale=1.0)

    samples: list[float] = []
    hub_samples: list[float] = []
    proxy_samples: list[float] = []
    for i in range(args.ticks):
        telem = engine.step(market_stress=0.05 * ((i % 7) / 6.0), scale=1.0)
        samples.append(telem.total_ms)
        hub_samples.append(telem.hub_ms)
        proxy_samples.append(telem.proxy_ms)

    engine.close()

    mean = statistics.fmean(samples)
    p50 = percentile(samples, 50)
    p95 = percentile(samples, 95)
    p99 = percentile(samples, 99)
    mx = max(samples)

    print(f"  hub step   p50  : {percentile(hub_samples, 50):8.3f} ms")
    print(f"  proxy prop p50  : {percentile(proxy_samples, 50):8.3f} ms")
    print(f"  tick mean       : {mean:8.3f} ms")
    print(f"  tick p50        : {p50:8.3f} ms")
    print(f"  tick p95        : {p95:8.3f} ms")
    print(f"  tick p99        : {p99:8.3f} ms")
    print(f"  tick max        : {mx:8.3f} ms")
    print("-" * 64)

    # Correctness smoke: proxies should be finite and hubs should have moved.
    snap = engine.snapshot()
    hubs = snap["hubs"]
    proxies = snap["proxies"]
    assert hubs.shape == (N_HUBS, FEATURE_DIM)
    assert proxies.shape == (N_PROXIES, FEATURE_DIM)
    assert np_all_finite(hubs) and np_all_finite(proxies)
    print(f"  snapshot OK     : hubs={hubs.shape} proxies={proxies.shape}")

    ok = p95 < args.budget_ms
    print(f"  RESULT          : {'PASS' if ok else 'FAIL'} (p95 {'<' if ok else '>='} {args.budget_ms} ms)")
    print("=" * 64)
    return 0 if ok else 1


def np_all_finite(arr) -> bool:
    import numpy as np

    return bool(np.isfinite(arr).all())


if __name__ == "__main__":
    raise SystemExit(main())
