"""Backtest gate + neural reaction + paper soak smoke tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_deploy_gate_evaluate_pass_fail() -> None:
    from training.deploy_gate import evaluate_gate

    ok, _ = evaluate_gate(
        {"rows": 200, "annualized_sharpe": 0.2, "max_drawdown": 0.1},
        min_sharpe=-0.5,
        max_drawdown=0.45,
        min_rows=50,
    )
    assert ok is True
    bad, reason = evaluate_gate(
        {"rows": 10, "annualized_sharpe": 0.2, "max_drawdown": 0.1},
        min_rows=50,
    )
    assert bad is False
    assert "min_rows" in reason


def test_deploy_gate_skip(tmp_path: Path) -> None:
    from training.deploy_gate import gate_or_raise

    report = gate_or_raise(
        holdout=None,
        metrics_report=None,
        min_sharpe=-0.5,
        max_drawdown=0.45,
        min_rows=50,
        skip=True,
    )
    assert report["skipped"] is True
    assert report["passed"] is True


def test_deploy_gate_from_metrics_file(tmp_path: Path) -> None:
    from training.deploy_gate import gate_or_raise

    path = tmp_path / "metrics.json"
    path.write_text(
        json.dumps(
            {"rows": 120, "annualized_sharpe": 0.05, "max_drawdown": 0.12}
        ),
        encoding="utf-8",
    )
    report = gate_or_raise(
        holdout=None,
        metrics_report=path,
        min_sharpe=-0.5,
        max_drawdown=0.45,
        min_rows=50,
        skip=False,
    )
    assert report["passed"] is True


def test_neural_react_without_checkpoint_is_empty() -> None:
    from ai.simulator_engine.macro_vectors import default_world
    from ai.simulator_engine.reaction_models import NeuralReactionModel

    world = default_world()
    model = NeuralReactionModel()
    assert model.is_loaded is False
    assert model.react("USA", world) == []


def test_neural_react_with_tiny_checkpoint(tmp_path: Path) -> None:
    torch = pytest.importorskip("torch")
    import torch.nn as nn

    from ai.simulator_engine.macro_vectors import default_world
    from ai.simulator_engine.reaction_models import NeuralReactionModel

    world = default_world()
    in_dim = world.countries["USA"].feature_count_template()

    class ReactionNet(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(in_dim, 128),
                nn.ReLU(),
                nn.Linear(128, 64),
                nn.ReLU(),
                nn.Linear(64, 3),
                nn.Tanh(),
            )

        def forward(self, x):
            return self.net(x)

    net = ReactionNet()
    ckpt = tmp_path / "neural_reaction.pt"
    torch.save(
        {
            "state_dict": net.state_dict(),
            "in_dim": in_dim,
            "out_dim": 3,
            "x_mean": [0.0] * in_dim,
            "x_std": [1.0] * in_dim,
            "output_names": ["expected_volatility", "directional_bias", "risk_premium"],
        },
        ckpt,
    )
    model = NeuralReactionModel(checkpoint=ckpt, scale=1.0)
    assert model.is_loaded is True
    # Untrained random net may still emit small adjustments — just require list type.
    out = model.react("USA", world)
    assert isinstance(out, list)


def test_paper_soak_check_runs() -> None:
    from scripts.paper_soak_check import run_checks

    checks = run_checks(require_demo=False)
    assert checks
    assert any("WORLD_SIMULATION_DEFAULT" in c[0] for c in checks)


def test_prepare_feature_store_empty(tmp_path: Path) -> None:
    from training.prepare_feature_store import prepare

    raw = tmp_path / "raw"
    out = tmp_path / "features"
    raw.mkdir()
    summary = prepare(raw, out)
    assert summary["promoted"] == 0
