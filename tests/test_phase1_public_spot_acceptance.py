"""Public Spot acceptance CLI must never open network without explicit opt-in."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ci.phase1_public_spot_acceptance import (
    ObservedClock, args_parser, main, measure_public_clock, validate_args,
)


class FakeReply:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakePublicClockClient:
    requested = []

    def __init__(self, *, payload, **opts):
        self.payload = payload
        self.options = opts
        self.__class__.requested.append(("init", opts))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url):
        self.__class__.requested.append(("get", url))
        return FakeReply(self.payload)


def args(tmp_path, *extra):
    return args_parser().parse_args([
        "--output-dir", str(tmp_path.resolve()), *extra,
    ])


def test_no_consent_fails_before_creating_or_writing_anything(tmp_path, capsys):
    location = tmp_path / "never_create"
    assert main(["--output-dir", str(location)]) == 2
    assert "PermissionError" in capsys.readouterr().err
    assert not location.exists()


def test_invalid_clock_or_capture_bounds_denied_before_network(tmp_path):
    for bad in (
        ["--max-frames", "2"],
        ["--max-frames", "1000000"],
        ["--max-attempts", "0"],
        ["--max-attempts", "11"],
        ["--max-clock-offset-ms", "0"],
        ["--max-clock-rtt-ms", "0"],
        ["--timeout-s", "1"],
    ):
        with pytest.raises(ValueError):
            validate_args(args(tmp_path, "--allow-public-market-data", *bad))


@pytest.mark.asyncio
async def test_genuine_market_data_time_host_only_using_mock_client():
    now = iter([1780000000000, 1780000000060])
    mono = iter([1000, 1060])
    FakePublicClockClient.requested = []
    report = await measure_public_clock(
        max_abs_offset_ms=3000, max_rtt_ms=2000,
        wall_ms=lambda: next(now), monotonic_ms=lambda: next(mono),
        client_factory=lambda **opts: FakePublicClockClient(
            payload={"serverTime": 1780000000040}, **opts,
        ),
    )
    assert report == ObservedClock(
        server_time_ms=1780000000040, measured_offset_ms=10,
        round_trip_ms=60, estimated_uncertainty_ms=30,
    )
    assert FakePublicClockClient.requested[0][1]["verify"] is True
    assert FakePublicClockClient.requested[0][1]["trust_env"] is False
    assert FakePublicClockClient.requested[0][1]["follow_redirects"] is False
    assert FakePublicClockClient.requested[1] == (
        "get", "https://data-api.binance.vision/api/v3/time",
    )


@pytest.mark.asyncio
async def test_large_clock_offset_refused_before_websocket():
    now = iter([1780000000000,1780000000060])
    mono = iter([1000,1060])
    with pytest.raises(RuntimeError, match="clock offset"):
        await measure_public_clock(
            max_abs_offset_ms=100, max_rtt_ms=2000,
            wall_ms=lambda: next(now), monotonic_ms=lambda: next(mono),
            client_factory=lambda **opts: FakePublicClockClient(
                payload={"serverTime":1780000005000}, **opts,
            ),
        )


@pytest.mark.asyncio
async def test_unreliable_rtt_refused_before_websocket():
    now = iter([1780000000000,1780000004000])
    mono = iter([1000,5000])
    with pytest.raises(RuntimeError, match="unreliable"):
        await measure_public_clock(
            max_abs_offset_ms=10000, max_rtt_ms=1000,
            wall_ms=lambda: next(now), monotonic_ms=lambda: next(mono),
            client_factory=lambda **opts: FakePublicClockClient(
                payload={"serverTime":1780000002000}, **opts,
            ),
        )


@pytest.mark.asyncio
async def test_noninteger_server_clock_rejected():
    now = iter([1780000000000,1780000000005])
    mono = iter([1000,1005])
    with pytest.raises(RuntimeError, match="invalid public exchange clock"):
        await measure_public_clock(
            max_abs_offset_ms=3000, max_rtt_ms=2000,
            wall_ms=lambda: next(now), monotonic_ms=lambda: next(mono),
            client_factory=lambda **opts: FakePublicClockClient(
                payload={"serverTime":True}, **opts,
            ),
        )


def test_explicit_research_only_cli_scope_and_absolute_path(tmp_path):
    opts = args(tmp_path, "--allow-public-market-data")
    assert opts.symbol == "BTCUSDT"
    assert opts.max_attempts <= 3
    validate_args(opts)
    opts.output_dir = Path("relative/dir")
    with pytest.raises(ValueError, match="absolute"):
        validate_args(opts)
