"""Hostile tests for unattended-safe, FINITE research paper supervision."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from econith.quant.forward_quote_dryrun import (
    PaperRefused, initialize, status,
)
from econith.quant.paper_forward_monitor import report
from scripts.paper_forward_cycle import cycle
from scripts.paper_supervised_endurance import (
    FAIL, PASS, _claim_owner, _release_owner,
    _resource_guard, audit_events, run, main,
)
from tests.test_forward_quote_dryrun import _source, _book, _quote


@pytest.fixture
def paper(tmp_path):
    path = (tmp_path / "paper.sqlite").resolve()
    initialize(path)
    return path


class ControlledClock:
    def __init__(self, start):
        self.ms = start
        self.elapsed = 0.
    def now(self):
        self.ms += 200
        return self.ms
    def monotonic(self):
        return self.elapsed
    def sleep(self, seconds):
        self.ms += int(seconds * 1000)
        self.elapsed += seconds


def make_runtime():
    snap, at = _source()
    clock = ControlledClock(at + 1000)
    q = _quote(snap)
    def fake_cycle(ledger):
        return cycle(
            ledger,
            capture=lambda symbols, *, limit, interval: snap,
            quote_get=lambda symbol: q,
            rules_get=lambda symbol: _book(),
            now_ms=clock.now,
        )
    return clock, fake_cycle


def test_one_real_engine_simulated_paper_cycle_supervised_and_reaudited(paper, tmp_path):
    clock, execute = make_runtime()
    folder = (tmp_path / "evidence").resolve()
    r = run(
        paper, folder, cycles=1,
        cycle_run=execute, clock_ms=clock.now,
        monotonic=clock.monotonic, sleep=clock.sleep,
    )
    assert r["verdict"] == PASS
    assert r["completed_cycles"] == 1
    assert r["real_orders"] == 0
    assert r["continuous_24h_proven"] is False
    assert r["independent_evidence"]["replayed_steps"] == 1
    assert audit_events(folder / "events.jsonl") == r["independent_evidence"]
    assert json.loads((folder / "summary.json").read_text()) == r
    assert status(paper)["simulated_fills"] == 1
    assert report(paper)["verified_quote_marks"] == 2
    assert not (paper.parent / (paper.name + ".supervisor-owner")).exists()


def test_3_cycles_same_closed_bar_do_not_duplicate_virtual_orders(paper, tmp_path):
    clock, execute = make_runtime()
    root = (tmp_path / "three").resolve()
    r = run(
        paper, root, cycles=3, poll_seconds=60,
        cycle_run=execute, clock_ms=clock.now,
        monotonic=clock.monotonic, sleep=clock.sleep,
    )
    assert r["verdict"] == PASS
    assert r["completed_cycles"] == 3
    assert audit_events(root / "events.jsonl")["replayed_steps"] == 3
    assert status(paper)["signal_count"] == 1
    assert status(paper)["simulated_fills"] == 1
    assert report(paper)["verified_quote_marks"] == 4


def test_incoming_time_is_capped_and_never_claims_continuous_source(paper, tmp_path):
    clock, execute = make_runtime()
    root = (tmp_path / "stale").resolve()
    r = run(
        paper, root, cycles=3, poll_seconds=3600,
        cycle_run=execute, clock_ms=clock.now,
        monotonic=clock.monotonic, sleep=clock.sleep,
        max_finite_hours=3,
    )
    assert r["verdict"] == FAIL
    assert r["completed_cycles"] < 3
    assert audit_events(root / "events.jsonl")["terminator"] == "HALT"
    assert r["continuous_24h_proven"] is False


def test_forged_existing_owner_fence_denies_second_supervisor(paper, tmp_path):
    lock, key = _claim_owner(paper)
    try:
        clock, execute = make_runtime()
        with pytest.raises(PaperRefused, match="owner"):
            run(paper, (tmp_path / "refused").resolve(), cycles=1,
                cycle_run=execute, clock_ms=clock.now,
                monotonic=clock.monotonic)
        assert not (tmp_path / "refused").exists()
    finally:
        _release_owner(lock, key)


def test_preexisting_evidence_is_never_overwritten(paper, tmp_path):
    root = (tmp_path / "written").resolve()
    root.mkdir()
    (root / "private.txt").write_text("KEEP")
    clock, execute = make_runtime()
    with pytest.raises(PaperRefused):
        run(paper, root, cycle_run=execute, clock_ms=clock.now,
            monotonic=clock.monotonic)
    assert (root / "private.txt").read_text() == "KEEP"
    assert not (paper.parent / (paper.name + ".supervisor-owner")).exists()


def test_refuse_network_failure_keep_incomplete_and_no_fake_success(paper, tmp_path):
    clock, _ = make_runtime()
    root = (tmp_path / "networkfailed").resolve()
    def broken(*args):
        raise OSError("public data source unreachable")
    r = run(
        paper, root, cycle_run=broken,
        clock_ms=clock.now, monotonic=clock.monotonic,
    )
    assert r["verdict"] == FAIL
    assert r["completed_cycles"] == 0
    assert r["failure_reason"].startswith("OSError")
    assert not r["prospective_profitable_strategy_proven"]
    assert audit_events(root / "events.jsonl")["terminator"] == "HALT"
    assert status(paper)["signal_count"] == 0


def test_tampered_hash_or_duplicate_order_rejected(paper, tmp_path):
    clock, execute = make_runtime()
    root = (tmp_path / "tamper").resolve()
    run(paper, root, cycle_run=execute,
        clock_ms=clock.now, monotonic=clock.monotonic)
    path = root / "events.jsonl"
    lines = path.read_text().splitlines()
    line = json.loads(lines[1])
    line["attributes"]["real_orders"] = 2
    lines[1] = json.dumps(line)
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(PaperRefused, match="hash"):
        audit_events(path)


def test_missing_terminal_record_is_not_reported_as_pass(paper, tmp_path):
    clock, execute = make_runtime()
    root = (tmp_path / "terminal").resolve()
    run(paper, root, cycle_run=execute,
        clock_ms=clock.now, monotonic=clock.monotonic)
    path = root / "events.jsonl"
    path.write_bytes(b"\n".join(path.read_bytes().splitlines()[:-1]) + b"\n")
    with pytest.raises(PaperRefused, match="terminal"):
        audit_events(path)


def test_refuse_invalid_cpu_time_disk_owner_and_excessive_duration(paper, tmp_path):
    clock, execute = make_runtime()
    for args in (
        {"cycles": 1441},
        {"cycles": 3, "poll_seconds": 3600, "max_finite_hours": 1},
        {"poll_seconds": 0},
        {"min_free_mb": 0},
        {"max_ledger_mb": 99999},
    ):
        with pytest.raises(PaperRefused, match="budget"):
            run(paper, (tmp_path / "invalid").resolve(), cycle_run=execute,
                clock_ms=clock.now, monotonic=clock.monotonic, **args)
        assert not (paper.parent / (paper.name + ".supervisor-owner")).exists()


def test_disk_limit_guard_fails_closed(paper):
    with pytest.raises(PaperRefused, match="disk"):
        _resource_guard(paper, min_free_mb=10**18, max_ledger_mb=256)


def test_clock_rollback_fails_closed_even_without_market_source_error(paper, tmp_path):
    clock, execute = make_runtime()
    first_ms = clock.ms
    seen = 0
    def backwards_clock():
        nonlocal seen
        seen += 1
        return first_ms + (2000 if seen == 1 else -5000)
    r = run(paper, (tmp_path / "rollback").resolve(),
            cycle_run=execute, clock_ms=backwards_clock,
            monotonic=clock.monotonic)
    assert r["verdict"] == FAIL
    assert r["completed_cycles"] == 0
    assert "CLOCK_ROLLBACK" in r["failure_reason"]


def test_command_require_explicit_three_optins(tmp_path):
    root = (tmp_path / "fake.sqlite").resolve()
    out = (tmp_path / "fake_runs").resolve()
    for argv in (
        ["--ledger", str(root), "--evidence-dir", str(out)],
        ["--ledger", str(root), "--evidence-dir", str(out), "--simulation-only"],
        ["--ledger", str(root), "--evidence-dir", str(out), "--allow-public-market-data"],
    ):
        with pytest.raises(SystemExit) as raised:
            main(argv)
        assert raised.value.code == 2
    assert not out.exists()
