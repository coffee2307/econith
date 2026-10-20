"""P1-09 longitudinal research cohort: deterministic offline originals only."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ci.phase1_pit_multiagent_advisory import verified_asof_evidence
from ci.phase1_pit_decision_scorecard import write_sealed_evidence
from ci.phase1_longitudinal_direction_cohort import (
    SCHEMA, VERDICT, evaluate_manifest, main, write_cohort,
)
from ci.phase1_spot_multiasset_ohlcv import collect, INTERVALS, ROWS

BASE = 1_700_000_000_000


def market(server_time):
    def requester(path, params=None):
        if path == "/api/v3/time":
            return json.dumps({"serverTime": server_time}).encode()
        if path == "/api/v3/exchangeInfo":
            sym = params["symbol"]
            return json.dumps({"symbols": [{
                "symbol": sym, "status": "TRADING",
                "baseAsset": sym[:-4], "quoteAsset": "USDT",
            }]}).encode()
        if path == "/api/v3/klines":
            step = INTERVALS[params["interval"]]
            opened = params["endTime"] - step + 1 - (ROWS - 1) * step
            rows = []
            for i in range(ROWS):
                timestamp = opened + i * step
                price = 100 + (timestamp // step) % 11
                rows.append([
                    timestamp, str(price), str(price + 2), str(price - 2),
                    str(price), "2", timestamp + step - 1,
                    "210", 3, "1", "105", "0",
                ])
            return json.dumps(rows).encode()
        raise AssertionError("no external market or model calls allowed")
    return requester


def create_entry(tmp_path, index, *, symbol="BTCUSDT", interval="1m",
                 rating="Buy", veto=False):
    start = BASE + index * 1_200_000
    follow = start + 660_000
    decision = start + 1_000
    first = (tmp_path / f"source_{index}").resolve()
    later = (tmp_path / f"later_{index}").resolve()
    output = (tmp_path / f"result_{index}").resolve()
    assert collect(first, requester=market(start))["status"] == "PASSED_FINITE_REST_QUALITY_ONLY"
    assert collect(later, requester=market(follow))["status"] == "PASSED_FINITE_REST_QUALITY_ONLY"
    evidence = verified_asof_evidence(
        first, symbol=symbol, interval=interval, decision_ms=decision,
    )
    votes = (
        ("technical", "BULL"), ("bull", "BULL"), ("bear", "BEAR"),
        ("risk", "VETO" if veto else "ALLOW_RESEARCH"),
        ("portfolio", rating),
    )
    reports = [{
        "role": role, "stance": stance, "evidence_sha256": evidence["evidence_sha256"],
        "available_at_ms": start, "rationale": "Audited historical candle only",
    } for role, stance in votes]
    write_sealed_evidence(
        output, decision_root=first, followup_root=later,
        symbol=symbol, interval=interval, decision_ms=decision,
        reports=reports, observed_by_ms=follow + 2_000,
    )
    return {
        "report_dir": str(output),
        "decision_root": str(first),
        "followup_root": str(later),
    }


def make_manifest(tmp_path, entries):
    path = (tmp_path / "manifest.json").resolve()
    path.write_text(json.dumps({"entries": entries}), encoding="utf-8")
    return path


@pytest.fixture
def cohort_sources(tmp_path):
    entries = [
        create_entry(tmp_path, 0, symbol="BTCUSDT", rating="Buy"),
        create_entry(tmp_path, 1, symbol="ETHUSDT", rating="Hold"),
        create_entry(tmp_path, 2, symbol="BNBUSDT", interval="5m",
                     rating="Sell", veto=True),
    ]
    return make_manifest(tmp_path, entries), entries


def test_multi_decision_exact_paired_denominators_and_split(cohort_sources):
    path, entries = cohort_sources
    result = evaluate_manifest(path)
    assert result["schema"] == SCHEMA
    assert result["verdict"] == VERDICT
    assert result["data_selection_complete_verified"] is False
    assert result["statistical_significance_claimed"] is False
    assert result["realized_pnl_computed"] is False
    assert result["orders_submitted"] == 0
    assert result["total_original_reaudits"] == len(entries)
    assert len(result["decisions"]) == 3
    metrics = result["summary"]
    assert metrics["decisions"] == 3
    assert metrics["risk_veto"] == 1
    assert metrics["ai_scorable"] <= 1
    assert metrics["ai_abstained_or_unscorable"] >= 2
    assert metrics["paired_scorable"] <= 1
    assert metrics["paired_ai_correct"] == sum(
        row["ai_correct"] is True
        for row in result["decisions"] if
        row["ai_correct"] is not None and row["baseline_correct"] is not None
    )
    assert metrics["paired_baseline_correct"] == sum(
        row["baseline_correct"] is True
        for row in result["decisions"] if
        row["ai_correct"] is not None and row["baseline_correct"] is not None
    )
    assert metrics["paired_ai_only_right"] + metrics["paired_baseline_only_right"] + metrics["paired_same_result"] == metrics["paired_scorable"]
    assert len(result["by_market"]) == 3
    assert all("report_sha256" in r for r in result["decisions"])


def test_manifest_order_does_not_change_chronological_result(cohort_sources):
    path, entries = cohort_sources
    original = evaluate_manifest(path)
    path.write_text(json.dumps({"entries": list(reversed(entries))}))
    shuffled = evaluate_manifest(path)
    assert shuffled["decisions"] == original["decisions"]
    assert shuffled["summary"] == original["summary"]
    assert shuffled["manifest_sha256"] != original["manifest_sha256"]


def test_duplicate_decision_or_outcome_rejected(cohort_sources):
    path, entries = cohort_sources
    path.write_text(json.dumps({"entries": [entries[0], entries[0]]}))
    with pytest.raises(ValueError, match="duplicate"):
        evaluate_manifest(path)


def test_altered_scorecard_refused_even_if_it_claims_high_accuracy(cohort_sources):
    path, entries = cohort_sources
    altered = Path(entries[0]["report_dir"]) / "finite_scorecard.json"
    payload = json.loads(altered.read_text())
    payload["score"]["advisory_direction_correct"] = True
    payload["score"]["raw_asset_return_decimal"] = "999"
    altered.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        evaluate_manifest(path)


def test_tampered_private_original_refused(cohort_sources):
    path, entries = cohort_sources
    original = Path(entries[0]["decision_root"]) / "klines_btcusdt_1m.json"
    original.write_bytes(original.read_bytes().replace(b'"210"', b'"211"', 1))
    with pytest.raises(ValueError):
        evaluate_manifest(path)


@pytest.mark.parametrize("bad_entries", [
    [], [{}], [{"report_dir": "/tmp/a", "decision_root": "/tmp/b"}],
    [{"report_dir": "../a", "decision_root": "../b", "followup_root": "../c"}],
])
def test_invalid_manifest_schema_fails_closed(tmp_path, bad_entries):
    path = make_manifest(tmp_path, bad_entries)
    with pytest.raises(ValueError):
        evaluate_manifest(path)


def test_no_consent_and_nested_output_cannot_write(cohort_sources, tmp_path):
    path, entries = cohort_sources
    folder = (tmp_path / "new_cohort").resolve()
    with pytest.raises(SystemExit) as exc:
        main(["--manifest", str(path), "--output-dir", str(folder)])
    assert exc.value.code == 2
    assert not folder.exists()
    private_folder = Path(entries[0]["decision_root"]) / "export"
    assert main([
        "--allow-offline-research-cohort", "--acknowledge-no-executed-pnl",
        "--manifest", str(path), "--output-dir", str(private_folder),
    ]) == 2
    assert not private_folder.exists()
    assert main([
        "--allow-offline-research-cohort", "--acknowledge-no-executed-pnl",
        "--manifest", str(path), "--output-dir", str(folder),
    ]) == 2  # manifest is in its own parent; cannot write under it
    assert not folder.exists()


def test_cli_success_outside_input_workspace(cohort_sources, tmp_path):
    path, entries = cohort_sources
    output = (tmp_path.parent / f"{tmp_path.name}_cohort_export").resolve()
    assert main([
        "--allow-offline-research-cohort", "--acknowledge-no-executed-pnl",
        "--manifest", str(path), "--output-dir", str(output),
    ]) == 0
    result = json.loads((output / "research_quality_cohort.json").read_text())
    assert result["summary"]["decisions"] == len(entries)
    assert result["orders_submitted"] == 0


def test_output_requires_fresh_directory(cohort_sources, tmp_path):
    path, _ = cohort_sources
    result = evaluate_manifest(path)
    target = (tmp_path / "receipt").resolve()
    write_cohort(target, result)
    assert json.loads((target / "research_quality_cohort.json").read_text()) == result
    with pytest.raises(ValueError):
        write_cohort(target, result)
    link = (tmp_path / "receipt_alias").resolve()
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError):
        write_cohort(link, result)
