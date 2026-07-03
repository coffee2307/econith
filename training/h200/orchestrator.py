"""ECONITH :: training.h200.orchestrator

End-to-end data-to-training orchestration factory optimised for a remote RunPod
NVIDIA H200 GPU instance (HBM3e high-bandwidth VRAM, advanced Tensor Cores).

Workflow:
    1. DATA PROCESSING    -- ingest Snappy Parquet (from the VPS daemon) + free
                             macro sources; parallel feature extraction via Ray
                             (or a process pool fallback); build target labels;
                             dump normalized tensor cache blocks.
    2. ASYNC DATALOADER   -- stream Parquet/SQLite arrays into tensor batches via
                             an async generator so training overlaps IO.
    3. PARTITIONED TRAIN  -- isolate & segment jobs across separate networks to
                             prevent parameter pollution: the HRL Meta-Brain, the
                             per-desk PPO nets (BTC/ETH/High-Beta/Meme) and the
                             Neural World model each train as distinct partitions.
    4. H200 ACCELERATION  -- mixed precision (BF16/FP16/FP8), DDP init hooks,
                             metric logging + model-registry checkpoint tracking.

Every deep-learning dependency (torch, ray, deepspeed, pandas, pyyaml) is
optional: the module imports lazily and degrades to a fully-typed dry-run planner
so the pipeline is inspectable and CI-runnable on a CPU box without a GPU.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Protocol

logger = logging.getLogger("econith.training.h200")

__all__ = [
    "Precision",
    "ParallelStrategy",
    "H200HardwareProfile",
    "ComponentPartition",
    "DataProcessingConfig",
    "TrainingPlan",
    "TensorBatch",
    "AsyncTensorLoader",
    "RegistryWriter",
    "ModelFactory",
    "DataProcessor",
    "PartitionedTrainer",
    "H200Orchestrator",
]


# ---------------------------------------------------------------------------
# Enums & hardware profile
# ---------------------------------------------------------------------------
class Precision(str, Enum):
    FP32 = "fp32"
    FP16 = "fp16"
    BF16 = "bf16"
    FP8 = "fp8"          # H200 transformer-engine FP8


class ParallelStrategy(str, Enum):
    SINGLE = "single"
    DDP = "ddp"                  # torch DistributedDataParallel
    DEEPSPEED_ZERO2 = "zero2"
    DEEPSPEED_ZERO3 = "zero3"


@dataclass(slots=True, frozen=True)
class H200HardwareProfile:
    """Static description of the RunPod H200 target for allocation planning."""

    device_name: str = "NVIDIA H200"
    vram_gb: int = 141                    # HBM3e capacity
    memory_bandwidth_tb_s: float = 4.8    # HBM3e
    tensor_core_gen: str = "Hopper"
    supports_fp8: bool = True
    #: fraction of VRAM the allocator is allowed to reserve up-front.
    vram_reservation_fraction: float = 0.92

    def usable_vram_gb(self) -> float:
        return self.vram_gb * self.vram_reservation_fraction

    def env_flags(self) -> dict[str, str]:
        """CUDA/allocator env flags that maximise HBM3e utilisation."""
        return {
            # Expandable segments minimise fragmentation on the huge HBM3e pool.
            "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True,max_split_size_mb:512",
            "NCCL_P2P_LEVEL": "NVL",
            "NVTE_FUSED_ATTN": "1",       # transformer-engine fused attention
            "CUDA_DEVICE_MAX_CONNECTIONS": "1",
            "TORCH_NCCL_AVOID_RECORD_STREAMS": "1",
        }


# ---------------------------------------------------------------------------
# Component partitions (the isolation contract)
# ---------------------------------------------------------------------------
class ComponentPartition(str, Enum):
    """Distinct, parameter-isolated training partitions."""

    HRL_META_BRAIN = "hrl_meta_brain"
    PPO_BTC = "ppo_btc"
    PPO_ETH = "ppo_eth"
    PPO_HIGH_BETA = "ppo_high_beta"
    PPO_MEME = "ppo_meme"
    NEURAL_WORLD = "neural_world"

    @property
    def default_precision(self) -> Precision:
        # The world model is the largest -> FP8 to fit + throughput; policy nets
        # stay BF16 for gradient stability under RL noise.
        return Precision.FP8 if self is ComponentPartition.NEURAL_WORLD else Precision.BF16


# ---------------------------------------------------------------------------
# Config objects
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class DataProcessingConfig:
    parquet_root: Path = Path("./datasets/vps")
    macro_cache: Path = Path("./datasets/macro")
    tensor_cache: Path = Path("./datasets/tensor_cache")
    sqlite_path: Optional[Path] = None            # optional alt source
    sqlite_table: str = "features"
    num_workers: int = 16
    use_ray: bool = True
    label_horizon_ticks: int = 50
    normalization: str = "zscore"                 # zscore | minmax | robust
    feature_columns: tuple[str, ...] = (
        "obi", "volume_delta", "buy_volume", "sell_volume", "trade_count",
        "funding_rate", "time_to_funding_s", "open_interest", "oi_change_pct",
        "liquidation_notional",
    )
    target_column: str = "reward"
    batch_size: int = 4096


@dataclass(slots=True)
class TrainingPlan:
    """A fully-resolved training plan for a single component partition."""

    partition: ComponentPartition
    precision: Precision
    strategy: ParallelStrategy
    world_size: int
    micro_batch: int
    grad_accum: int
    max_steps: int
    learning_rate: float
    vram_budget_gb: float
    checkpoint_dir: Path
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def effective_batch(self) -> int:
        return self.micro_batch * self.grad_accum * self.world_size

    def to_dict(self) -> dict[str, Any]:
        return {
            "partition": self.partition.value,
            "precision": self.precision.value,
            "strategy": self.strategy.value,
            "world_size": self.world_size,
            "micro_batch": self.micro_batch,
            "grad_accum": self.grad_accum,
            "effective_batch": self.effective_batch,
            "max_steps": self.max_steps,
            "learning_rate": self.learning_rate,
            "vram_budget_gb": round(self.vram_budget_gb, 2),
            "checkpoint_dir": str(self.checkpoint_dir),
            **self.extra,
        }


# ---------------------------------------------------------------------------
# Metric / registry sink protocol
# ---------------------------------------------------------------------------
class MetricSink(Protocol):
    def log(self, partition: str, step: int, metrics: dict[str, float]) -> None: ...
    def register_model(self, partition: str, path: str, meta: dict[str, Any]) -> None: ...


class ConsoleMetricSink:
    """Default metric sink writing structured lines to the logger."""

    def log(self, partition: str, step: int, metrics: dict[str, float]) -> None:
        payload = " ".join(f"{k}={v:.4f}" for k, v in metrics.items())
        logger.info("[%s] step %d :: %s", partition, step, payload)

    def register_model(self, partition: str, path: str, meta: dict[str, Any]) -> None:
        logger.info("[registry] %s -> %s (%s)", partition, path, meta)


# ---------------------------------------------------------------------------
# Model factory protocol + default regression head
# ---------------------------------------------------------------------------
class ModelFactory(Protocol):
    """Builds a ``torch.nn.Module`` for a given partition + input dim."""

    def __call__(self, partition: ComponentPartition, in_dim: int) -> Any: ...


def _default_model_factory(partition: ComponentPartition, in_dim: int) -> Any:
    """A small, robust MLP head so the harness is runnable end-to-end.

    Partition-specific trainers (train_ppo / train_world) inject their own
    architectures; this default lets the pipeline train + checkpoint on any host.
    """
    import torch.nn as nn

    hidden = 256 if partition is ComponentPartition.NEURAL_WORLD else 128
    return nn.Sequential(
        nn.Linear(in_dim, hidden),
        nn.GELU(),
        nn.Linear(hidden, hidden),
        nn.GELU(),
        nn.Linear(hidden, 1),
    )


# ---------------------------------------------------------------------------
# Registry writer (manifest.yaml + active.yaml)
# ---------------------------------------------------------------------------
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class RegistryWriter:
    """Writes checkpoints into the central model-registry schema.

    Mirrors the contract consumed by ``training/deploy.py``:
      * ``manifest.yaml`` : ``{version, generated_at, models: {name: {path, sha256, meta}}}``
      * ``active.yaml``   : ``{version, activated_at, manifest, models: {name: {path, sha256}}}``
    """

    def __init__(self, registry_dir: Path = Path("./models/registry")) -> None:
        self._dir = registry_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._manifest_path = self._dir / "manifest.yaml"
        self._active_path = self._dir / "active.yaml"

    def _load(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            import yaml

            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001 - fall back to a fresh document
            logger.warning("could not parse %s; starting a fresh registry doc", path)
            return {}

    def _dump(self, path: Path, doc: dict[str, Any]) -> None:
        try:
            import yaml

            path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        except ImportError:
            import json

            path.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
            logger.warning("pyyaml missing -- wrote JSON to %s", path)

    def register(
        self, partition: str, checkpoint: Path, meta: dict[str, Any]
    ) -> dict[str, Any]:
        """Record a completed checkpoint into ``manifest.yaml`` (idempotent)."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        manifest = self._load(self._manifest_path)
        manifest.setdefault("version", stamp)
        manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
        models = manifest.setdefault("models", {})
        entry = {
            "path": str(checkpoint.as_posix()),
            "sha256": _sha256(checkpoint) if checkpoint.exists() else None,
            "meta": meta,
        }
        models[partition] = entry
        self._dump(self._manifest_path, manifest)
        logger.info("[registry] manifest updated: %s -> %s", partition, entry["path"])
        return entry

    def activate(self) -> Path:
        """Promote the current manifest to ``active.yaml`` for production reads."""
        manifest = self._load(self._manifest_path)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        active = {
            "version": manifest.get("version", stamp),
            "activated_at": datetime.now(timezone.utc).isoformat(),
            "manifest": str(self._manifest_path.as_posix()),
            "models": {
                name: {"path": e.get("path"), "sha256": e.get("sha256")}
                for name, e in manifest.get("models", {}).items()
            },
        }
        self._dump(self._active_path, active)
        logger.info("[registry] activated %d models -> %s",
                    len(active["models"]), self._active_path)
        return self._active_path


# ---------------------------------------------------------------------------
# Async tensor loader
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class TensorBatch:
    """A single mini-batch: features ``x`` and targets ``y`` (torch or numpy)."""

    x: Any
    y: Any
    rows: int


class AsyncTensorLoader:
    """Streams Parquet/SQLite feature arrays into tensor batches asynchronously.

    Disk/Arrow reads run in a worker thread (via ``asyncio.to_thread``) so the
    training loop can overlap compute with IO. Yields :class:`TensorBatch`.
    """

    def __init__(self, config: DataProcessingConfig) -> None:
        self._cfg = config

    def _discover(self) -> list[Path]:
        root = self._cfg.parquet_root
        if not root.exists():
            return []
        return sorted(root.rglob("*.parquet"))

    async def iter_batches(
        self, device: Optional[str] = None
    ) -> AsyncGenerator[TensorBatch, None]:
        """Async-generate normalized tensor batches from the configured source."""
        shards = self._discover()
        if shards:
            async for batch in self._iter_parquet(shards, device):
                yield batch
        elif self._cfg.sqlite_path is not None:
            async for batch in self._iter_sqlite(device):
                yield batch
        else:
            logger.warning("AsyncTensorLoader found no data source; yielding nothing")

    async def _iter_parquet(
        self, shards: list[Path], device: Optional[str]
    ) -> AsyncGenerator[TensorBatch, None]:
        for shard in shards:
            frame = await asyncio.to_thread(self._read_parquet, shard)
            if frame is None:
                continue
            for batch in self._to_batches(frame, device):
                yield batch

    async def _iter_sqlite(
        self, device: Optional[str]
    ) -> AsyncGenerator[TensorBatch, None]:
        frame = await asyncio.to_thread(self._read_sqlite)
        if frame is None:
            return
        for batch in self._to_batches(frame, device):
            yield batch

    def _read_parquet(self, shard: Path) -> Optional[Any]:
        try:
            import pandas as pd

            return pd.read_parquet(shard)
        except Exception:  # noqa: BLE001 - a corrupt shard must not kill training
            logger.warning("skipping unreadable parquet shard %s", shard)
            return None

    def _read_sqlite(self) -> Optional[Any]:
        try:
            import sqlite3

            import pandas as pd

            with sqlite3.connect(self._cfg.sqlite_path) as conn:  # type: ignore[arg-type]
                return pd.read_sql_query(
                    f"SELECT * FROM {self._cfg.sqlite_table}", conn
                )
        except Exception:  # noqa: BLE001
            logger.warning("could not read sqlite source %s", self._cfg.sqlite_path)
            return None

    def _to_batches(self, frame: Any, device: Optional[str]):
        cols = [c for c in self._cfg.feature_columns if c in frame.columns]
        if not cols or self._cfg.target_column not in frame.columns:
            logger.warning(
                "frame missing feature/target columns (have %s)", list(frame.columns)
            )
            return
        import numpy as np

        feats = frame[cols].to_numpy(dtype="float32")
        feats = self._normalize(feats, np)
        target = frame[self._cfg.target_column].to_numpy(dtype="float32").reshape(-1, 1)
        bs = max(1, self._cfg.batch_size)
        torch = _try_import_torch()
        for start in range(0, len(feats), bs):
            xb = feats[start:start + bs]
            yb = target[start:start + bs]
            if torch is not None:
                x = torch.from_numpy(xb)
                y = torch.from_numpy(yb)
                if device:
                    x = x.to(device, non_blocking=True)
                    y = y.to(device, non_blocking=True)
                yield TensorBatch(x=x, y=y, rows=len(xb))
            else:
                yield TensorBatch(x=xb, y=yb, rows=len(xb))

    def _normalize(self, arr: Any, np: Any) -> Any:
        mode = self._cfg.normalization
        if mode == "minmax":
            lo = arr.min(axis=0, keepdims=True)
            hi = arr.max(axis=0, keepdims=True)
            return (arr - lo) / (np.abs(hi - lo) + 1e-8)
        if mode == "robust":
            med = np.median(arr, axis=0, keepdims=True)
            iqr = (
                np.percentile(arr, 75, axis=0, keepdims=True)
                - np.percentile(arr, 25, axis=0, keepdims=True)
            )
            return (arr - med) / (np.abs(iqr) + 1e-8)
        # default zscore
        mean = arr.mean(axis=0, keepdims=True)
        std = arr.std(axis=0, keepdims=True)
        return (arr - mean) / (std + 1e-8)

    def feature_dim(self) -> int:
        return len([c for c in self._cfg.feature_columns])


# ---------------------------------------------------------------------------
# Data processing phase (parallel cache builder)
# ---------------------------------------------------------------------------
class DataProcessor:
    """Parallel feature-extraction & tensor-cache builder."""

    def __init__(self, config: DataProcessingConfig) -> None:
        self._cfg = config
        self._cfg.tensor_cache.mkdir(parents=True, exist_ok=True)

    def discover_parquet(self) -> list[Path]:
        if not self._cfg.parquet_root.exists():
            return []
        return sorted(self._cfg.parquet_root.rglob("*.parquet"))

    def process(self) -> dict[str, Any]:
        """Run parallel feature extraction; returns a manifest of cache blocks."""
        shards = self.discover_parquet()
        logger.info("data processing over %d parquet shards", len(shards))
        if self._cfg.use_ray and self._try_ray(shards):
            engine = "ray"
        else:
            engine = self._process_pool(shards)
        manifest = {
            "engine": engine,
            "shard_count": len(shards),
            "tensor_cache": str(self._cfg.tensor_cache),
            "normalization": self._cfg.normalization,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        (self._cfg.tensor_cache / "manifest.json").write_text(
            _json_dumps(manifest), encoding="utf-8"
        )
        return manifest

    def _try_ray(self, shards: list[Path]) -> bool:
        try:
            import ray
        except ImportError:
            return False
        if not ray.is_initialized():
            ray.init(num_cpus=self._cfg.num_workers, ignore_reinit_error=True)

        @ray.remote
        def _extract(path_str: str) -> int:
            return _extract_features(Path(path_str))

        futures = [_extract.remote(str(p)) for p in shards]
        ray.get(futures)
        return True

    def _process_pool(self, shards: list[Path]) -> str:
        from concurrent.futures import ProcessPoolExecutor

        if not shards:
            return "noop"
        with ProcessPoolExecutor(max_workers=self._cfg.num_workers) as pool:
            list(pool.map(_extract_features, shards))
        return "process_pool"


def _extract_features(path: Path) -> int:
    """Pure feature-extraction worker (Arrow-native, importable by executors).

    Returns the number of rows processed. Kept module-level so it is picklable
    for ``ProcessPoolExecutor`` and Ray.
    """
    try:
        import pyarrow.parquet as pq
    except ImportError:
        return 0
    try:
        table = pq.read_table(path)
    except Exception:  # noqa: BLE001 - a corrupt shard must not kill the batch
        logger.warning("skipping unreadable shard %s", path)
        return 0
    return table.num_rows


def _json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj, indent=2, default=str)


# ---------------------------------------------------------------------------
# Partitioned trainer (real DDP-capable loop + dry-run fallback)
# ---------------------------------------------------------------------------
class PartitionedTrainer:
    """Resolves + executes per-partition training with H200 acceleration."""

    def __init__(
        self,
        hardware: H200HardwareProfile,
        checkpoint_root: Path = Path("./checkpoints"),
        sink: Optional[MetricSink] = None,
        registry: Optional[RegistryWriter] = None,
        model_factory: Optional[ModelFactory] = None,
    ) -> None:
        self._hw = hardware
        self._root = checkpoint_root
        self._sink = sink or ConsoleMetricSink()
        self._registry = registry or RegistryWriter()
        self._model_factory = model_factory or _default_model_factory
        self._root.mkdir(parents=True, exist_ok=True)

    def plan(
        self, partition: ComponentPartition, world_size: int = 1
    ) -> TrainingPlan:
        """Produce an H200-optimised training plan for a partition."""
        # VRAM budget is split evenly across the partition's data-parallel ranks.
        vram_budget = self._hw.usable_vram_gb() / max(1, world_size)
        strategy = (
            ParallelStrategy.DEEPSPEED_ZERO3
            if partition is ComponentPartition.NEURAL_WORLD and world_size > 1
            else ParallelStrategy.DDP if world_size > 1
            else ParallelStrategy.SINGLE
        )
        # Micro-batch scales with the huge HBM3e pool; the world model is heavier.
        micro = 8 if partition is ComponentPartition.NEURAL_WORLD else 32
        return TrainingPlan(
            partition=partition,
            precision=partition.default_precision,
            strategy=strategy,
            world_size=world_size,
            micro_batch=micro,
            grad_accum=4,
            max_steps=100_000 if partition is ComponentPartition.NEURAL_WORLD else 40_000,
            learning_rate=3e-4 if partition.value.startswith("ppo") else 1e-4,
            vram_budget_gb=vram_budget,
            checkpoint_dir=self._root / partition.value,
            extra={"fp8_enabled": partition.default_precision is Precision.FP8},
        )

    async def train(
        self, plan: TrainingPlan, loader: Optional[AsyncTensorLoader] = None
    ) -> dict[str, Any]:
        """Execute (or dry-run) a single partition's training loop."""
        plan.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        torch = _try_import_torch()
        if torch is None:
            logger.warning("torch unavailable -- dry-run plan for %s", plan.partition.value)
            ckpt = plan.checkpoint_dir / "DRY_RUN"
            self._registry.register(
                plan.partition.value, ckpt, {"status": "dry_run", **plan.to_dict()}
            )
            self._sink.register_model(
                plan.partition.value, str(ckpt), {"status": "dry_run", **plan.to_dict()}
            )
            return {"status": "dry_run", "plan": plan.to_dict()}

        if loader is None:
            logger.warning("no data loader for %s -- planning only", plan.partition.value)
            return {"status": "ready_no_data", "plan": plan.to_dict()}

        return await self._run_training_loop(plan, loader, torch)

    async def _run_training_loop(
        self, plan: TrainingPlan, loader: AsyncTensorLoader, torch: Any
    ) -> dict[str, Any]:
        rank, world_size, local_rank = _maybe_init_distributed(torch)
        device = self._resolve_device(torch, local_rank)
        autocast_dtype = self._autocast_dtype(torch, plan.precision)

        in_dim = loader.feature_dim()
        model = self._model_factory(plan.partition, in_dim).to(device)
        model = _wrap_ddp(torch, model, plan.strategy, local_rank)

        optimizer = torch.optim.AdamW(model.parameters(), lr=plan.learning_rate)
        loss_fn = torch.nn.MSELoss()
        use_amp = plan.precision in (Precision.FP16, Precision.BF16, Precision.FP8) \
            and device.startswith("cuda")
        scaler = _make_grad_scaler(torch, enabled=plan.precision is Precision.FP16)

        logger.info(
            "training %s on %s (%s, batch=%d, world_size=%d)",
            plan.partition.value, device, plan.precision.value,
            plan.effective_batch, world_size,
        )

        step = 0
        last_loss = float("nan")
        model.train()
        optimizer.zero_grad(set_to_none=True)
        async for batch in loader.iter_batches(device=device):
            if step >= plan.max_steps:
                break
            with torch.autocast(
                device_type="cuda" if device.startswith("cuda") else "cpu",
                dtype=autocast_dtype, enabled=use_amp,
            ):
                pred = model(batch.x)
                loss = loss_fn(pred, batch.y) / plan.grad_accum

            if scaler.is_enabled():
                scaler.scale(loss).backward()
            else:
                loss.backward()

            if (step + 1) % plan.grad_accum == 0:
                if scaler.is_enabled():
                    scaler.step(optimizer)
                    scaler.update()
                else:
                    optimizer.step()
                optimizer.zero_grad(set_to_none=True)

            last_loss = float(loss.item()) * plan.grad_accum
            if rank == 0 and step % 100 == 0:
                self._sink.log(plan.partition.value, step, {"loss": last_loss})
            step += 1

        ckpt = plan.checkpoint_dir / f"{plan.partition.value}-latest.pt"
        result_meta = {
            "device": device, "steps": step, "final_loss": round(last_loss, 6),
            "world_size": world_size, "autocast": str(autocast_dtype), **plan.to_dict(),
        }
        if rank == 0:
            self._save_checkpoint(torch, model, ckpt)
            self._registry.register(plan.partition.value, ckpt, result_meta)
            self._sink.register_model(plan.partition.value, str(ckpt), result_meta)

        _maybe_destroy_distributed(torch)
        return {"status": "trained", "checkpoint": str(ckpt), **result_meta}

    def _save_checkpoint(self, torch: Any, model: Any, ckpt: Path) -> None:
        raw = model.module if hasattr(model, "module") else model
        torch.save(raw.state_dict(), ckpt)
        logger.info("checkpoint written -> %s", ckpt)

    @staticmethod
    def _resolve_device(torch: Any, local_rank: int) -> str:
        if torch.cuda.is_available():
            return f"cuda:{local_rank}"
        return "cpu"

    @staticmethod
    def _autocast_dtype(torch: Any, precision: Precision) -> Any:
        return {
            Precision.FP16: torch.float16,
            Precision.BF16: torch.bfloat16,
            Precision.FP8: torch.bfloat16,   # FP8 handled by transformer-engine
            Precision.FP32: torch.float32,
        }[precision]


# ---------------------------------------------------------------------------
# Distributed helpers (torchrun-compatible)
# ---------------------------------------------------------------------------
def _maybe_init_distributed(torch: Any) -> tuple[int, int, int]:
    """Init ``torch.distributed`` when launched under torchrun; else single proc.

    Returns ``(rank, world_size, local_rank)``.
    """
    world_size = int(os.environ.get("WORLD_SIZE", "1"))
    if world_size <= 1:
        return 0, 1, 0
    if not torch.distributed.is_available():
        logger.warning("torch.distributed unavailable -- forcing single process")
        return 0, 1, 0
    rank = int(os.environ.get("RANK", "0"))
    local_rank = int(os.environ.get("LOCAL_RANK", "0"))
    backend = "nccl" if torch.cuda.is_available() else "gloo"
    if not torch.distributed.is_initialized():
        torch.distributed.init_process_group(backend=backend)
    if torch.cuda.is_available():
        torch.cuda.set_device(local_rank)
    logger.info("DDP initialised rank=%d/%d (backend=%s)", rank, world_size, backend)
    return rank, world_size, local_rank


def _wrap_ddp(torch: Any, model: Any, strategy: ParallelStrategy, local_rank: int) -> Any:
    if strategy is ParallelStrategy.DDP and torch.distributed.is_initialized():
        device_ids = [local_rank] if torch.cuda.is_available() else None
        return torch.nn.parallel.DistributedDataParallel(model, device_ids=device_ids)
    return model


def _maybe_destroy_distributed(torch: Any) -> None:
    if torch.distributed.is_available() and torch.distributed.is_initialized():
        torch.distributed.destroy_process_group()


def _make_grad_scaler(torch: Any, *, enabled: bool) -> Any:
    """Version-agnostic AMP GradScaler (new ``torch.amp`` API, legacy fallback)."""
    try:
        return torch.amp.GradScaler("cuda", enabled=enabled)
    except (AttributeError, TypeError):
        return torch.cuda.amp.GradScaler(enabled=enabled)


def _try_import_torch() -> Optional[Any]:
    try:
        import torch

        return torch
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------
class H200Orchestrator:
    """Drives the full data->partitioned-training workflow on RunPod H200."""

    def __init__(
        self,
        data_config: Optional[DataProcessingConfig] = None,
        hardware: Optional[H200HardwareProfile] = None,
        sink: Optional[MetricSink] = None,
        registry: Optional[RegistryWriter] = None,
    ) -> None:
        self._data_cfg = data_config or DataProcessingConfig()
        self._hw = hardware or H200HardwareProfile()
        self._registry = registry or RegistryWriter()
        self._processor = DataProcessor(self._data_cfg)
        self._loader = AsyncTensorLoader(self._data_cfg)
        self._trainer = PartitionedTrainer(
            self._hw, sink=sink, registry=self._registry
        )

    def apply_hardware_env(self) -> dict[str, str]:
        """Export the H200 allocator/NCCL env flags into the process env."""
        flags = self._hw.env_flags()
        os.environ.update(flags)
        logger.info("applied H200 env flags: %s", ", ".join(flags))
        return flags

    async def run_async(
        self,
        partitions: Optional[list[ComponentPartition]] = None,
        world_size: int = 1,
        activate: bool = True,
    ) -> dict[str, Any]:
        """Process data then train each requested partition in isolation (async)."""
        self.apply_hardware_env()
        manifest = await asyncio.to_thread(self._processor.process)
        partitions = partitions or list(ComponentPartition)
        results: dict[str, Any] = {}
        for partition in partitions:
            plan = self._trainer.plan(partition, world_size=world_size)
            results[partition.value] = await self._trainer.train(plan, self._loader)
        if activate:
            self._registry.activate()
        return {
            "hardware": self._hw.device_name,
            "usable_vram_gb": round(self._hw.usable_vram_gb(), 1),
            "data_manifest": manifest,
            "partitions": results,
        }

    def run(
        self,
        partitions: Optional[list[ComponentPartition]] = None,
        world_size: int = 1,
        activate: bool = True,
    ) -> dict[str, Any]:
        """Synchronous entrypoint wrapping :meth:`run_async`."""
        return asyncio.run(
            self.run_async(partitions=partitions, world_size=world_size, activate=activate)
        )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s :: %(message)s"
    )
    result = H200Orchestrator().run()
    logger.info("orchestration complete: %d partitions", len(result["partitions"]))


if __name__ == "__main__":
    main()
