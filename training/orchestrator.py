"""ECONITH :: training.orchestrator  (PHASE C/D -- The Factory Foreman)

Run every apprentice at once without letting them fight over the machinery.

Economic analogy
----------------
The H200 is one enormous, expensive workshop (141 GB of GPU memory). A good
foreman doesn't make workers queue single-file when they could work side by side,
but also doesn't let five of them grab the same crane at once and jam it. This
orchestrator schedules the training jobs to use the H200 efficiently:

  * **Wave 1 (parallel):** the three PPO apprentices train side by side on the
    GPU (each needs only ~8 GB, so the 141 GB workshop has room to spare), while
    the HMM regime model runs on the CPU in parallel -- it needs no GPU at all.
  * **Wave 2 (after):** the neural world model trains once the GPU apprentices
    have clocked out, so peak GPU memory never spikes.

Each job runs as its own **isolated process** (a separate worker with their own
tools), which is the safest way to share a GPU: when a worker finishes, the OS
reclaims all their VRAM instantly -- no leaks, no interference.

Backends:
  * ``multiprocessing`` (default) -- launches worker subprocesses directly. Zero
    extra dependencies; perfect for a single H200.
  * ``ray`` -- same jobs wrapped as Ray tasks, for multi-GPU / multi-node pods.

After every job finishes, the foreman signs off by writing the shipping manifest
(``models/registry/manifest.yaml``) with a SHA256 fingerprint of each model, so
the customs gate (deploy.py) can later prove nothing was tampered with.

Run it:
    python training/orchestrator.py --data ./datasets/processed \
        --output ./models --backend multiprocessing \
        --jobs trend,mean_reversion,scalper,hmm,world_neural \
        --early-stop-patience 5 --holdout ./datasets/processed/quant_holdout.parquet
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import logging
import subprocess
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
)
logger = logging.getLogger("econith.training.orchestrator")

PYEXE = sys.executable  # the exact interpreter running us (works inside venvs)


# ===========================================================================
#  Job definitions
# ===========================================================================
class Job:
    """One unit of work: a worker command + where it belongs in the schedule.

    ``wave`` orders execution (all of wave 0 finishes before wave 1 starts);
    ``device`` marks whether it competes for GPU memory so the foreman can cap
    how many GPU jobs run at once.
    """

    def __init__(self, name: str, cmd: list[str], output: Path, wave: int, device: str):
        self.name = name
        self.cmd = cmd
        self.output = output
        self.wave = wave
        self.device = device


def build_jobs(args) -> list[Job]:
    data = Path(args.data)
    model_dir = Path(args.output)
    labeled = data / "quant_labeled.parquet"
    holdout = Path(args.holdout) if args.holdout else data / "quant_holdout.parquet"
    requested = [j.strip() for j in args.jobs.split(",") if j.strip()]

    jobs: list[Job] = []
    for name in requested:
        if name in ("trend", "mean_reversion", "scalper"):
            out = model_dir / "agents" / f"{name}_ppo.zip"
            cmd = [
                PYEXE, "training/train_ppo.py",
                "--agent", name,
                "--data", str(labeled),
                "--output", str(out),
                "--holdout", str(holdout),
                "--timesteps", str(args.timesteps),
                "--patience", str(args.early_stop_patience),
            ]
            jobs.append(Job(name, cmd, out, wave=0, device="gpu"))

        elif name in ("hmm", "fit_regime", "regime"):
            out = model_dir / "regime" / "hmm_4state.pkl"
            cmd = [
                PYEXE, "training/fit_regime.py",
                "--data", str(labeled),
                "--output", str(out),
                "--states", str(args.regime_states),
            ]
            jobs.append(Job("hmm", cmd, out, wave=0, device="cpu"))

        elif name in ("world_neural", "world"):
            out = model_dir / "world" / "neural_reaction.pt"
            cmd = [
                PYEXE, "training/train_world.py",
                "--output", str(out),
                "--samples", str(args.world_samples),
                "--epochs", str(args.world_epochs),
            ]
            # Wave 1: runs AFTER the PPO agents free their GPU memory.
            jobs.append(Job("world_neural", cmd, out, wave=1, device="gpu"))
        else:
            logger.warning("unknown job '%s' -- skipping", name)
    return jobs


# ===========================================================================
#  Execution
# ===========================================================================
def _run_job(job: Job) -> tuple[str, int, float]:
    """Run one worker subprocess to completion, streaming its exit status."""
    start = time.monotonic()
    logger.info("[%s] starting (%s) -> %s", job.name, job.device, job.output)
    proc = subprocess.run(job.cmd, cwd=str(_ROOT))
    elapsed = time.monotonic() - start
    status = "OK" if proc.returncode == 0 else f"FAILED({proc.returncode})"
    logger.info("[%s] %s in %.1fs", job.name, status, elapsed)
    return job.name, proc.returncode, elapsed


def run_multiprocessing(jobs: list[Job], max_gpu_concurrent: int) -> dict[str, int]:
    """Schedule jobs wave by wave, capping simultaneous GPU workers.

    Within a wave, GPU jobs are throttled to ``max_gpu_concurrent`` so we never
    over-subscribe the workshop, while CPU jobs (the HMM) run freely alongside.
    """
    results: dict[str, int] = {}
    waves = sorted({j.wave for j in jobs})
    for wave in waves:
        wave_jobs = [j for j in jobs if j.wave == wave]
        gpu_jobs = [j for j in wave_jobs if j.device == "gpu"]
        cpu_jobs = [j for j in wave_jobs if j.device == "cpu"]
        # Total workers = capped GPU slots + all CPU jobs (they don't touch VRAM).
        workers = max(1, min(len(gpu_jobs), max_gpu_concurrent)) + len(cpu_jobs)
        logger.info("wave %d: %d GPU job(s) (cap %d) + %d CPU job(s)",
                    wave, len(gpu_jobs), max_gpu_concurrent, len(cpu_jobs))

        # Order GPU jobs first but throttle them via the pool size; CPU jobs ride along.
        ordered = gpu_jobs[:max_gpu_concurrent] + cpu_jobs + gpu_jobs[max_gpu_concurrent:]
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_run_job, j): j for j in ordered}
            for fut in concurrent.futures.as_completed(futures):
                name, code, _ = fut.result()
                results[name] = code
    return results


def run_ray(jobs: list[Job], max_gpu_concurrent: int) -> dict[str, int]:
    """Same schedule, but each job is a Ray task (multi-GPU / multi-node ready)."""
    try:
        import ray
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(f"ray backend requested but ray is not installed ({exc})")

    if not ray.is_initialized():
        ray.init(ignore_reinit_error=True)

    @ray.remote
    def _remote(cmd: list[str], cwd: str) -> int:
        import subprocess as sp
        return sp.run(cmd, cwd=cwd).returncode

    results: dict[str, int] = {}
    for wave in sorted({j.wave for j in jobs}):
        wave_jobs = [j for j in jobs if j.wave == wave]
        logger.info("ray wave %d: %d job(s)", wave, len(wave_jobs))
        refs = {j.name: _remote.remote(j.cmd, str(_ROOT)) for j in wave_jobs}
        for name, ref in refs.items():
            results[name] = int(ray.get(ref))
    return results


# ===========================================================================
#  Manifest (Phase D sign-off)
# ===========================================================================
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(jobs: list[Job], results: dict[str, int], model_dir: Path) -> Path:
    """Sign off each successfully-built model with a tamper-proof fingerprint."""
    import json
    from datetime import datetime, timezone

    import yaml

    registry = model_dir / "registry"
    registry.mkdir(parents=True, exist_ok=True)

    models: dict[str, dict] = {}
    for job in jobs:
        if results.get(job.name, 1) != 0 or not job.output.exists():
            continue
        rel = job.output.relative_to(model_dir).as_posix()
        entry = {"path": rel, "sha256": _sha256(job.output)}
        # Fold in the worker's sidecar metrics if it left any.
        for sidecar in (
            job.output.parent / f"{job.output.stem}.metrics.json",
            job.output.parent / f"{job.output.stem}.meta.json",
        ):
            if sidecar.exists():
                try:
                    entry["metrics"] = json.loads(sidecar.read_text())
                except (ValueError, OSError):
                    pass
        models[job.name] = entry

    manifest = {
        "version": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_by": "training/orchestrator.py",
        "models": models,
    }
    path = registry / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest, sort_keys=False))
    logger.info("wrote manifest with %d model(s) -> %s", len(models), path)
    return path


# ===========================================================================
#  CLI
# ===========================================================================
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="orchestrator.py", description="ECONITH training foreman")
    p.add_argument("--data", default="./datasets/processed", help="processed data dir")
    p.add_argument("--output", default="./models", help="model output root")
    p.add_argument("--backend", choices=["multiprocessing", "ray"], default="multiprocessing")
    p.add_argument("--jobs", default="trend,mean_reversion,scalper,hmm,world_neural")
    p.add_argument("--holdout", default="", help="holdout parquet (defaults under --data)")
    p.add_argument("--early-stop-patience", type=int, default=5)
    p.add_argument("--max-gpu-concurrent", type=int, default=3)
    p.add_argument("--timesteps", type=int, default=200_000)
    p.add_argument("--regime-states", type=int, default=4)
    p.add_argument("--world-samples", type=int, default=20_000)
    p.add_argument("--world-epochs", type=int, default=40)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    jobs = build_jobs(args)
    if not jobs:
        raise SystemExit("no valid jobs to run")

    logger.info("foreman starting %d job(s) via %s backend", len(jobs), args.backend)
    started = time.monotonic()
    if args.backend == "ray":
        results = run_ray(jobs, args.max_gpu_concurrent)
    else:
        results = run_multiprocessing(jobs, args.max_gpu_concurrent)

    write_manifest(jobs, results, Path(args.output))

    failed = [n for n, c in results.items() if c != 0]
    logger.info("factory run complete in %.1fs | ok=%d failed=%d",
                time.monotonic() - started,
                sum(1 for c in results.values() if c == 0), len(failed))
    if failed:
        logger.error("failed jobs: %s", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
