"""Guard against GitHub Actions 'failure with zero jobs' from invalid YAML.

PyYAML uses YAML 1.1 so the top-level GitHub Actions 'on' key may parse as True.
This test verifies syntax and structure but cannot prove Actions is enabled.
"""
from __future__ import annotations

from pathlib import Path

import yaml


def test_ci_workflow_is_parseable_with_both_critical_jobs() -> None:
    source = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
    workflow = yaml.safe_load(source.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    assert workflow["name"] == "ci"
    triggers = workflow.get("on", workflow.get(True))
    assert "push" in triggers
    assert "pull_request" in triggers
    jobs = workflow["jobs"]
    assert "pytest" in jobs and "dashboard" in jobs
    python_steps = jobs["pytest"]["steps"]
    assert any("ci.repo_hygiene_check" in str(step.get("run", ""))
               for step in python_steps)
    assert any("pytest tests/" in str(step.get("run", ""))
               for step in python_steps)
