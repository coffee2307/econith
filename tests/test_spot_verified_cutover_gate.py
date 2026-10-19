"""Fail-closed verified-only runtime cutover policy: opt-in, no live IO."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from infrastructure.spot_verified_cutover_gate import (
    VerifiedIngressNotReady,
    enforce_verified_only_spot_ingress,
)


def test_legacy_default_does_not_change_existing_boot(monkeypatch):
    monkeypatch.delenv("ECONITH_SPOT_INGRESS_POLICY", raising=False)
    assert enforce_verified_only_spot_ingress() == "legacy"
    assert enforce_verified_only_spot_ingress("legacy") == "legacy"


def test_verified_only_flag_refuses_unverified_default_streamer(monkeypatch):
    monkeypatch.setenv("ECONITH_SPOT_INGRESS_POLICY", "verified_only")
    with pytest.raises(VerifiedIngressNotReady, match="startup stopped"):
        enforce_verified_only_spot_ingress()


@pytest.mark.parametrize("policy", [
    "VERIFIED_ONLY", "verified-only", "mock", "unknown", "", "1", "enabled",
    " legacy ", "verified_only ", False, True, 123,
])
def test_invalid_policy_does_not_silently_fall_back(policy):
    with pytest.raises(VerifiedIngressNotReady, match="invalid"):
        enforce_verified_only_spot_ingress(policy)


def test_main_enforces_gate_before_database_engine_and_streamer_start():
    """AST regression: no default runtime should bypass preflight by accident."""
    source = (Path(__file__).resolve().parents[1] / "main.py").read_text(
        encoding="utf-8",
    )
    tree = ast.parse(source)
    lifespan = next(
        n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
        and n.name == "lifespan"
    )
    calls = [
        (node.lineno, (
            node.func.id if isinstance(node.func, ast.Name)
            else node.func.attr if isinstance(node.func, ast.Attribute)
            else "<dynamic>"
        ))
        for node in ast.walk(lifespan) if isinstance(node, ast.Call)
    ]
    for gate in ("enforce_verified_only_spot_ingress",):
        assert sum(n == gate for _, n in calls) == 1
        line = next(i for i, n in calls if n == gate)
        for downstream in ("init_database", "startup", "start", "connect"):
            for other, name in calls:
                if name == downstream:
                    assert line < other, (
                        f"verified-only guard must precede {name} at line {other}"
                    )
    assert "from infrastructure.spot_verified_cutover_gate import " in source


def test_no_default_runtime_claims_verified_spot_without_cutover():
    from infrastructure.spot_verified_cutover_gate import (
        enforce_verified_only_spot_ingress,
    )
    with pytest.raises(VerifiedIngressNotReady):
        enforce_verified_only_spot_ingress("verified_only")
