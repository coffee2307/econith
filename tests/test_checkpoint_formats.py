"""Smoke tests: SB3 .zip vs H200 .pt checkpoint routing."""
from __future__ import annotations

from pathlib import Path

from ai.agents.agent_loaders import resolve_world_neural_checkpoint
from ai.agents.checkpoint_formats import CheckpointKind, classify_checkpoint
from ai.simulator_engine.reaction_models import NeuralReactionModel


def test_classify_checkpoint_kinds(tmp_path: Path) -> None:
    zip_path = tmp_path / "trend.zip"
    pt_path = tmp_path / "world.pt"
    zip_path.write_bytes(b"PK\x03\x04")
    pt_path.write_bytes(b"not-a-real-torch-file")
    assert classify_checkpoint(zip_path) is CheckpointKind.SB3_ZIP
    assert classify_checkpoint(pt_path) is CheckpointKind.TORCH_PT
    assert classify_checkpoint(tmp_path / "missing.zip") is CheckpointKind.MISSING


def test_world_neural_rejects_sb3_zip(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "models"
    reg = root / "registry"
    reg.mkdir(parents=True)
    zip_path = root / "world.zip"
    zip_path.write_bytes(b"PK\x03\x04")
    (reg / "active.yaml").write_text(
        "models:\n  world_neural:\n    path: world.zip\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MODEL_DIR", str(root))
    monkeypatch.setenv("MODEL_REGISTRY", str(reg))
    assert resolve_world_neural_checkpoint() is None


def test_neural_reaction_rejects_sb3_zip(tmp_path: Path) -> None:
    zip_path = tmp_path / "policy.zip"
    zip_path.write_bytes(b"PK\x03\x04")
    model = NeuralReactionModel(checkpoint=zip_path)
    assert model.is_loaded is False
