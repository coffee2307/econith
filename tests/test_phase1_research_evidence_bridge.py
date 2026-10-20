"""Offline read-only research audit bridge tests; all Binance sources are synthetic."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ci.phase1_optin_openai_research_panel import _ROLES, run_panel, save_advisory
from ci.phase1_pit_multiagent_advisory import verified_asof_evidence
from ci.phase1_pit_decision_scorecard import write_sealed_evidence
from ci.phase1_research_evidence_bridge import (
    SCHEMA, VERDICT, audit, export_receipt, main,
)
from ci.phase1_spot_multiasset_ohlcv import collect, INTERVALS, ROWS

BASE = 1_700_000_000_000
LATER = BASE + 660_000
DECISION = BASE + 1_000
OBSERVED = LATER + 2_000


def market(server_time):
    def request(path, params=None):
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
            start = params["endTime"] - step + 1 - (ROWS - 1) * step
            rows = []
            for i in range(ROWS):
                opened = start + i * step
                price = 100 + (opened // step) % 11
                rows.append([
                    opened, str(price), str(price + 2), str(price - 2),
                    str(price), "2", opened + step - 1,
                    "210", 3, "1", "105", "0",
                ])
            return json.dumps(rows).encode()
        raise AssertionError("network endpoint forbidden")
    return request


@pytest.fixture
def originals(tmp_path):
    a = (tmp_path / "original_decision").resolve()
    b = (tmp_path / "original_followup").resolve()
    assert collect(a, requester=market(BASE))["status"] == "PASSED_FINITE_REST_QUALITY_ONLY"
    assert collect(b, requester=market(LATER))["status"] == "PASSED_FINITE_REST_QUALITY_ONLY"
    return a, b


def make_scorecard(originals, tmp_path):
    a, b = originals
    evidence = verified_asof_evidence(
        a, symbol="BTCUSDT", interval="1m", decision_ms=DECISION,
    )
    reports = [
        {
            "role": role, "stance": stance,
            "rationale": "Observed pre-decision evidence only",
            "available_at_ms": BASE,
            "evidence_sha256": evidence["evidence_sha256"],
        } for role, stance in (
            ("technical", "BULL"), ("bull", "BULL"), ("bear", "BEAR"),
            ("risk", "ALLOW_RESEARCH"), ("portfolio", "Buy"),
        )
    ]
    folder = (tmp_path / "scorecard_result").resolve()
    write_sealed_evidence(
        folder, decision_root=a, followup_root=b,
        symbol="BTCUSDT", interval="1m", decision_ms=DECISION,
        observed_by_ms=OBSERVED, reports=reports,
    )
    return folder


class FakeClient:
    def __init__(self):
        self.responses = self
        self.calls = 0

    def create(self, **kwargs):
        role = _ROLES[self.calls]
        self.calls += 1
        stance = {
            "technical": "NEUTRAL", "bull": "BULL", "bear": "BEAR",
            "risk": "VETO", "portfolio": "Hold",
        }[role]
        return SimpleNamespace(
            status="completed",
            output_text=json.dumps({"stance": stance, "rationale": "Research only."}),
            usage=SimpleNamespace(input_tokens=80, output_tokens=24),
        )


def make_panel(originals, tmp_path):
    a, _ = originals
    time_state = [BASE + 100]

    def clock():
        time_state[0] += 10
        return time_state[0]

    record = run_panel(
        source_root=a, symbol="BTCUSDT", interval="1m",
        decision_ms=DECISION, model="gpt-5.6",
        client=FakeClient(), clock_ms=clock,
    )
    folder = (tmp_path / "panel_result").resolve()
    save_advisory(folder, record, source_root=a)
    return folder


def test_scorecard_receipt_recomputes_originals_and_binds_exact_file(originals, tmp_path):
    folder = make_scorecard(originals, tmp_path)
    receipt = audit(
        kind="scorecard", report_dir=folder,
        decision_root=originals[0], followup_root=originals[1],
    )
    assert receipt["schema"] == SCHEMA
    assert receipt["verdict"] == VERDICT
    assert receipt["orders_submitted"] == 0
    assert receipt["real_trading_results_proven"] is False
    assert receipt["signed_by_trusted_authority"] is False
    assert len(receipt["report_sha256"]) == 64
    assert receipt["kind"] == "scorecard"
    assert "root" not in receipt and "api_key" not in receipt
    out = (tmp_path / "bridge_output").resolve()
    export_receipt(out, receipt)
    assert json.loads((out / "research_audit_receipt.json").read_text()) == receipt
    with pytest.raises(ValueError):
        export_receipt(out, receipt)


def test_forged_scorecard_and_future_original_fail_closed(originals, tmp_path):
    folder = make_scorecard(originals, tmp_path)
    file = folder / "finite_scorecard.json"
    item = json.loads(file.read_text())
    item["score"]["raw_asset_return_decimal"] = "999"
    file.write_text(json.dumps(item))
    with pytest.raises(ValueError):
        audit(kind="scorecard", report_dir=folder,
              decision_root=originals[0], followup_root=originals[1])
    assert not (tmp_path / "refused_output").exists()


def test_panel_rechecks_original_quorum_and_declared_model_events(originals, tmp_path):
    folder = make_panel(originals, tmp_path)
    receipt = audit(kind="panel", report_dir=folder, decision_root=originals[0])
    assert receipt["kind"] == "panel"
    assert receipt["risk_veto"] is True
    assert receipt["evidence_sha256"]
    assert receipt["exchange_origin_verified"] is False
    with pytest.raises(ValueError):
        audit(kind="panel", report_dir=folder, decision_root=originals[0],
              followup_root=originals[1])
    payload = json.loads((folder / "llm_advisory.json").read_text())
    file = folder / "llm_advisory.json"
    bad = copy.deepcopy(payload)
    bad["advisory"]["risk_veto"] = False
    file.write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        audit(kind="panel", report_dir=folder, decision_root=originals[0])
    bad = copy.deepcopy(payload)
    bad["model_events"][0]["tools_enabled"] = True
    file.write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        audit(kind="panel", report_dir=folder, decision_root=originals[0])
    bad = copy.deepcopy(payload)
    bad["model_events"][0]["output_tokens"] = 999
    file.write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        audit(kind="panel", report_dir=folder, decision_root=originals[0])


def test_missing_source_and_no_consent_cannot_export(originals, tmp_path):
    folder = make_panel(originals, tmp_path)
    out = (tmp_path / "no_consent").resolve()
    with pytest.raises(SystemExit) as info:
        main(["--kind", "panel", "--report-dir", str(folder),
              "--decision-root", str(originals[0]),
              "--output-dir", str(out)])
    assert info.value.code == 2
    assert not out.exists()
    assert main(["--allow-offline-original-audit", "--kind", "panel",
                 "--report-dir", str(folder),
                 "--decision-root", str((tmp_path / "missing").resolve()),
                 "--output-dir", str(out)]) == 2
    assert not out.exists()


def test_corrupt_private_original_refused(originals, tmp_path):
    folder = make_panel(originals, tmp_path)
    path = originals[0] / "klines_btcusdt_1m.json"
    path.write_bytes(path.read_bytes().replace(b'"210"', b'"211"', 1))
    with pytest.raises(ValueError):
        audit(kind="panel", report_dir=folder, decision_root=originals[0])


def test_receipt_cannot_overwrite_or_follow_symlink(originals, tmp_path):
    folder = make_panel(originals, tmp_path)
    receipt = audit(kind="panel", report_dir=folder, decision_root=originals[0])
    out = (tmp_path / "receipt").resolve()
    out.symlink_to(folder, target_is_directory=True)
    with pytest.raises(ValueError):
        export_receipt(out, receipt)
    assert (folder / "llm_advisory.json").is_file()
