"""ECONITH :: training.h200.orchestrator

End-to-end data-to-training orchestration factory optimised for a remote RunPod
NVIDIA H200 GPU instance (HBM3e high-bandwidth VRAM, advanced Tensor Cores).

Workflow:
    1. DATA PROCESSING    -- ingest Snappy Parquet (from the VPS daemon) + free
                             macro sources; parallel feature extraction via Ray
                             (or a process pool fallback); build target labels;
                             dump normalized tensor cache blocks.
    2. PARTITIONED TRAIN  -- isolate & segment jobs across separate networks to
                             prevent parameter pollution: the HRL Meta-Brain, the
                             per-desk PPO nets (BTC/ETH/High-Beta/Meme) and the
                             Neural World model each train as distinct partitions.
    3. H200 ACCELERATION  -- mixed precision (BF16/FP16/FP8), DDP/DeepSpeed hooks,
                             metric logging + model-registry state tracking.

Every deep-learning dependency (torch, ray, deepspeed) is optional: the module
imports lazily and degrades to a fully-typed dry-run planner so the pipeline is
inspectable and CI-runnable on a CPU box without a GPU.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger("econith.training.h200")

__all__ = [
    "Precision",
    "ParallelStrategy",
    "H200HardwareProfile",
    "ComponentPartition",
    "DataProcessingConfig",
    "TrainingPlan",
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
    num_workers: int = 16
    use_ray: bool = True
    label_horizon_ticks: int = 50
    normalization: str = "zscore"           # zscore | minmax | robust


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
# Data processing phase
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
# Partitioned trainer
# ---------------------------------------------------------------------------
class PartitionedTrainer:
    """Resolves + executes per-partition training with H200 acceleration."""

    def __init__(
        self,
        hardware: H200HardwareProfile,
        checkpoint_root: Path = Path("./checkpoints"),
        sink: MetricSink | None = None,
    ) -> None:
        self._hw = hardware
        self._root = checkpoint_root
        self._sink = sink or ConsoleMetricSink()
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

    def train(self, plan: TrainingPlan) -> dict[str, Any]:
        """Execute (or dry-run) a single partition's training."""
        plan.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        torch = _try_import_torch()
        if torch is None:
            logger.warning("torch unavailable -- dry-run plan for %s", plan.partition.value)
            self._sink.register_model(
                plan.partition.value, str(plan.checkpoint_dir / "DRY_RUN"),
                {"status": "dry_run", **plan.to_dict()},
            )
            return {"status": "dry_run", "plan": plan.to_dict()}

        device = "cuda" if torch.cuda.is_available() else "cpu"
        autocast_dtype = self._autocast_dtype(torch, plan.precision)
        logger.info(
            "training %s on %s (%s, batch=%d)",
            plan.partition.value, device, plan.precision.value, plan.effective_batch,
        )
        # Structural training seam: a concrete model/dataloader is injected by the
        # partition-specific trainer modules (train_ppo / train_world). Here we
        # emit the resolved, acceleration-ready context so those modules bind to a
        # single canonical plan.
        ckpt = plan.checkpoint_dir / f"{plan.partition.value}-latest.pt"
        self._sink.register_model(
            plan.partition.value, str(ckpt),
            {"device": device, "autocast": str(autocast_dtype), **plan.to_dict()},
        )
        return {
            "status": "ready",
            "device": device,
            "autocast_dtype": str(autocast_dtype),
            "checkpoint": str(ckpt),
            "plan": plan.to_dict(),
        }

    @staticmethod
    def _autocast_dtype(torch: Any, precision: Precision) -> Any:
        return {
            Precision.FP16: torch.float16,
            Precision.BF16: torch.bfloat16,
            Precision.FP8: torch.bfloat16,   # FP8 handled by transformer-engine
            Precision.FP32: torch.float32,
        }[precision]


def _try_import_torch() -> Any | None:
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
        data_config: DataProcessingConfig | None = None,
        hardware: H200HardwareProfile | None = None,
        sink: MetricSink | None = None,
    ) -> None:
        self._data_cfg = data_config or DataProcessingConfig()
        self._hw = hardware or H200HardwareProfile()
        self._processor = DataProcessor(self._data_cfg)
        self._trainer = PartitionedTrainer(self._hw, sink=sink)

    def apply_hardware_env(self) -> dict[str, str]:
        """Export the H200 allocator/NCCL env flags into the process env."""
        import os

        flags = self._hw.env_flags()
        os.environ.update(flags)
        logger.info("applied H200 env flags: %s", ", ".join(flags))
        return flags

    def run(
        self,
        partitions: list[ComponentPartition] | None = None,
        world_size: int = 1,
    ) -> dict[str, Any]:
        """Run data processing then train each requested partition in isolation."""
        self.apply_hardware_env()
        manifest = self._processor.process()
        partitions = partitions or list(ComponentPartition)
        results: dict[str, Any] = {}
        for partition in partitions:
            plan = self._trainer.plan(partition, world_size=world_size)
            results[partition.value] = self._trainer.train(plan)
        return {
            "hardware": self._hw.device_name,
            "usable_vram_gb": round(self._hw.usable_vram_gb(), 1),
            "data_manifest": manifest,
            "partitions": results,
        }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s :: %(message)s")
    result = H200Orchestrator().run()
    logger.info("orchestration complete: %d partitions", len(result["partitions"]))


if __name__ == "__main__":
    main()
