"""Offline preflight for strictly opt-in public Spot co-running HMAC runner."""
from __future__ import annotations

import pytest

from ci.phase1_rotating_corun_hmac import co_run_rotating_hmac
from ci.phase1_public_corun_hmac_pilot import main as public_cli
from ci.phase1_public_corun_hmac_audit import inspect_public
from ci.phase1_public_spot_acceptance import measure_public_clock
from infrastructure.spot_public_pinned_lease import open_pinned_public_spot_lease


@pytest.mark.asyncio
async def test_public_co_run_requires_explicit_public_not_fixture_approval(tmp_path):
    root=tmp_path/"missing"
    with pytest.raises(PermissionError):
        await co_run_rotating_hmac(root,duration_s=120,max_frames_per_segment=450,
            lease_factory=open_pinned_public_spot_lease,clock_preflight=measure_public_clock)
    assert not root.exists()
    with pytest.raises((ValueError,PermissionError)):
        await co_run_rotating_hmac(root,duration_s=120,max_frames_per_segment=450,
            lease_factory=open_pinned_public_spot_lease,clock_preflight=measure_public_clock,
            explicit_offline_fixture_approval=True)
    assert not root.exists()
    with pytest.raises(PermissionError):
        await co_run_rotating_hmac(root,duration_s=120,max_frames_per_segment=450,
            lease_factory=open_pinned_public_spot_lease,clock_preflight=measure_public_clock,
            explicit_offline_fixture_approval=True,explicit_public_research_approval=True)
    assert not root.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("duration,limit",[(30,450),(151,450),(120,100),(120,900)])
async def test_public_bounds_reject_before_transport_and_filesystem(tmp_path,duration,limit):
    root=tmp_path/"rejected"
    with pytest.raises(ValueError):
        await co_run_rotating_hmac(root,duration_s=duration,max_frames_per_segment=limit,
            lease_factory=open_pinned_public_spot_lease,clock_preflight=measure_public_clock,
            explicit_public_research_approval=True)
    assert not root.exists()


@pytest.mark.asyncio
async def test_public_cannot_use_fixture_transport_or_clock(tmp_path):
    async def bogus_clock(**kwargs): return None
    def fake_factory(**kwargs): raise AssertionError("network factory must never run")
    root=tmp_path/"rejected"
    with pytest.raises(ValueError):
        await co_run_rotating_hmac(root,duration_s=120,max_frames_per_segment=450,
            lease_factory=fake_factory,clock_preflight=measure_public_clock,
            explicit_public_research_approval=True)
    with pytest.raises(ValueError):
        await co_run_rotating_hmac(root,duration_s=120,max_frames_per_segment=450,
            lease_factory=open_pinned_public_spot_lease,clock_preflight=bogus_clock,
            explicit_public_research_approval=True)
    assert not root.exists()


def test_public_cli_requires_three_approvals(tmp_path):
    root=(tmp_path/"missing").resolve()
    with pytest.raises(SystemExit):
        public_cli(["--output-dir",str(root)])
    with pytest.raises(SystemExit):
        public_cli(["--output-dir",str(root),"--allow-public-market-data"])
    with pytest.raises(SystemExit):
        public_cli(["--output-dir",str(root),"--allow-public-market-data",
                    "--allow-hmac-research"])
    assert not root.exists()


def test_public_auditor_refuses_missing_source_not_promoted(tmp_path):
    root=tmp_path/"not_made"
    with pytest.raises((ValueError,OSError,KeyError)):
        inspect_public(root)
