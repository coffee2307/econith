"""Aggregate-only FIFO forensic report keeps source failure disposition."""
from __future__ import annotations

import pytest

from ci.phase1_subsecond_forensics import inspect
from infrastructure.spot_spool_pressure_diagnostics import BoundedSpoolPressure


def test_forensics_finds_precise_preoverflow_window(tmp_path):
    t=[0]
    p=BoundedSpoolPressure(tmp_path/"spool_pressure.json",clock_ns=lambda:t[0])
    p.fifo_event("enqueue",3,128)
    t[0]=250_000_000
    p.fifo_event("enqueue",120,128)
    t[0]=500_000_000
    p.fifo_event("overflow",128,128)
    p.write(source_complete=False)
    result=inspect(tmp_path/"spool_pressure.json")
    assert result["source_complete"] is False
    assert result["first_overflow_window_start_ms"]==500
    assert result["totals"]["overflow"]==1
    assert result["max_fifo_depth"]==128
    assert result["pre_overflow_context"][-1]["overflow"]==1
    assert result["history_continuity_certified"] is False
    assert result["hmac_cryptography_reverified"] is False


def test_forensics_valid_source_stays_diagnostic_only(tmp_path):
    p=BoundedSpoolPressure(tmp_path/"spool_pressure.json")
    p.fifo_event("enqueue",2,128)
    p.fifo_event("dequeue",1,128)
    p.fifo_event("writer_complete",1,128,1.5)
    p.write(source_complete=True)
    result=inspect(tmp_path/"spool_pressure.json")
    assert result["source_complete"] is True
    assert result["totals"]["overflow"]==0
    assert result["first_overflow_window_start_ms"] is None
    assert result["orders_submitted"]==0


def test_forensics_refuses_malformed_or_oversized_input(tmp_path):
    file=tmp_path/"spool_pressure.json"
    file.write_text('{"schema":"not-a-diagnostic"}',encoding="utf-8")
    with pytest.raises(ValueError):
        inspect(file)
    file.write_text("x"*512001,encoding="utf-8")
    with pytest.raises(ValueError):
        inspect(file)
