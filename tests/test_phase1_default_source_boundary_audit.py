"""Frozen safety audit: isolated verified branches never activate legacy."""
from __future__ import annotations

from pathlib import Path

import pytest

from ci.phase1_default_source_boundary_audit import (
    StartupBoundaryAuditError,audit_startup_boundaries,
)

_ROOT=Path(__file__).resolve().parents[1]


def samples():
    return (
        (_ROOT/"main.py").read_text(encoding="utf-8"),
        (_ROOT/"infrastructure/spot_verified_cutover_gate.py").read_text(
            encoding="utf-8",
        ),
    )


def test_current_bounded_verified_paths_have_no_legacy_order_routes():
    main,gate=samples()
    result=audit_startup_boundaries(main,gate)
    assert result["research_legacy_execution_singleton_calls"]==0
    assert result["legacy_unverified_source_remains_default"] is True
    assert result["default_verified_market_data_cutover_certified"] is False
    assert result["manual_security_review_still_required"] is True


def test_engine_call_inside_verified_forward_preview_fails_architecture_gate():
    main,gate=samples()
    needle='        preview = await start_verified_forward_preview(\n'
    assert main.count(needle)==1
    unsafe=main.replace(needle,'        legacy_engine = get_engine()\n'+needle)
    with pytest.raises(StartupBoundaryAuditError,match="execution singleton"):
        audit_startup_boundaries(unsafe,gate)


def test_missing_return_can_never_fall_through_to_legacy_main():
    main,gate=samples()
    needle='        return\n    if ingress_policy == "verified_only":'
    assert main.count(needle)==1
    unsafe=main.replace(needle,'        pass\n    if ingress_policy == "verified_only":')
    with pytest.raises(StartupBoundaryAuditError,match="fall through"):
        audit_startup_boundaries(unsafe,gate)


def test_default_source_policy_switch_requires_manual_reaudit():
    main,gate=samples()
    original='os.environ.get("ECONITH_SPOT_INGRESS_POLICY", "legacy")'
    assert gate.count(original)==1
    changed=gate.replace(original,original.replace('"legacy"','"verified_only"'))
    with pytest.raises(StartupBoundaryAuditError,match="default legacy"):
        audit_startup_boundaries(main,changed)


def test_cannot_silently_remove_legacy_source_debt_from_inventory():
    main,gate=samples()
    stripped=main.replace("    engine = get_engine()","    engine = None")
    assert stripped!=main
    with pytest.raises(StartupBoundaryAuditError,match="manual source review"):
        audit_startup_boundaries(stripped,gate)
