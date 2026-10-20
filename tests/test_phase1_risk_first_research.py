"""P1-10 risk-first panel never turns one VETO into accepted agent quorum."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ci.phase1_risk_first_research import (
    GATE_SCHEMA, GATE_VERDICT, run_risk_first, main,
)
from ci.phase1_optin_openai_research_panel import save_advisory
from ci.phase1_research_evidence_bridge import audit as audit_full_panel
from ci.phase1_spot_multiasset_ohlcv import collect, INTERVALS, ROWS

BASE = 1_700_000_000_000


def fake_spot(server_time):
    def req(path, params=None):
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
            return json.dumps([
                [opened + i * step, "100", "110", "90", "105", "2",
                 opened + (i + 1) * step - 1, "210", 3, "1", "105", "0"]
                for i in range(ROWS)
            ]).encode()
        raise AssertionError("network is not allowed")
    return req


@pytest.fixture
def source(tmp_path):
    source = (tmp_path / "finite_source").resolve()
    assert collect(source, requester=fake_spot(BASE))["status"] == "PASSED_FINITE_REST_QUALITY_ONLY"
    return source


class FakeResponses:
    def __init__(self, *, veto=False, break_role=None):
        self.veto = veto
        self.break_role = break_role
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        role = ("risk", "technical", "bull", "bear", "portfolio")[len(self.calls) - 1]
        stance = {
            "risk": "VETO" if self.veto else "ALLOW_RESEARCH",
            "technical": "NEUTRAL", "bull": "BULL",
            "bear": "BEAR", "portfolio": "Hold",
        }[role]
        result = {"stance": stance, "rationale": "Bounded research only."}
        if self.break_role == role:
            result["order_quantity"] = 1
        return SimpleNamespace(
            status="completed", output_text=json.dumps(result),
            usage=SimpleNamespace(input_tokens=70, output_tokens=30),
        )


class Client:
    def __init__(self, **kw):
        self.responses = FakeResponses(**kw)


def clock():
    t = [BASE + 100]

    def now():
        t[0] += 10
        return t[0]
    return now


def run(source, client):
    return run_risk_first(
        source_root=source, symbol="BTCUSDT", interval="1m",
        model="gpt-5.6", client=client, decision_ms=BASE + 1_000,
        clock_ms=clock(),
    )


def test_risk_veto_saves_four_calls_but_creates_no_accepted_quorum(source, tmp_path):
    client = Client(veto=True)
    record = run(source, client)
    assert record["schema"] == GATE_SCHEMA
    assert record["verdict"] == GATE_VERDICT
    assert record["model_calls"] == 1
    assert len(client.responses.calls) == 1
    assert record["agent_quorum_satisfied"] is False
    assert record["portfolio_rating"] is None
    assert record["advisory_direction"] is None
    assert record["orders_submitted"] == 0
    assert "reports" not in record and "advisory" not in record
    call = client.responses.calls[0]
    assert call["store"] is False and call["tools"] == []
    assert call["tool_choice"] == "none" and call["max_output_tokens"] == 240
    with pytest.raises((KeyError, ValueError)):
        save_advisory((tmp_path / "should_not_be_panel").resolve(),
                      record, source_root=source)


def test_allow_runs_full_quorum_and_passes_existing_independent_auditor(source, tmp_path):
    client = Client(veto=False)
    record = run(source, client)
    assert record["model_calls"] == len(client.responses.calls) == 5
    assert [r["role"] for r in record["reports"]] == [
        "technical", "bull", "bear", "risk", "portfolio",
    ]
    assert [e["role"] for e in record["model_events"]] == [
        "technical", "bull", "bear", "risk", "portfolio",
    ]
    assert record["advisory"]["risk_veto"] is False
    assert record["advisory"]["portfolio_research_rating"] == "Hold"
    assert record["total_input_tokens"] == 350
    assert record["total_output_tokens"] == 150
    assert record["orders_submitted"] == 0
    for call in client.responses.calls:
        assert call["tools"] == [] and call["store"] is False
        assert call["tool_choice"] == "none"
    assert "peer stances" in client.responses.calls[-1]["input"]
    assert "Bounded research only." not in client.responses.calls[-1]["input"]
    folder = (tmp_path / "saved_panel").resolve()
    save_advisory(folder, record, source_root=source)
    receipt = audit_full_panel(kind="panel", report_dir=folder, decision_root=source)
    assert receipt["verdict"] == "PASSED_LOCAL_PYTHON_ORIGINAL_REAUDIT_ONLY"
    assert receipt["orders_submitted"] == 0


@pytest.mark.parametrize("break_role", ["risk", "technical", "bull", "bear", "portfolio"])
def test_any_provider_schema_violation_fails_before_acceptance(source, break_role):
    client = Client(veto=False, break_role=break_role)
    with pytest.raises(ValueError, match="structured"):
        run(source, client)
    assert len(client.responses.calls) <= 5


def test_risk_gate_is_first_and_never_calls_untrusted_analysts_on_veto(source):
    client = Client(veto=True)
    run(source, client)
    assert client.responses.calls[0]["text"]["format"]["name"] == "econith_risk_risk_first_v1"


def test_stale_or_missing_originals_rejected_without_spending(source):
    client = Client()
    with pytest.raises(ValueError):
        run_risk_first(
            source_root=source, symbol="BTCUSDT", interval="1m",
            model="gpt-5.6", client=client,
            decision_ms=BASE + 601_000, clock_ms=lambda: BASE + 600_900,
        )
    assert not client.responses.calls
    file = source / "klines_btcusdt_1m.json"
    file.write_bytes(file.read_bytes().replace(b'"210"', b'"211"', 1))
    with pytest.raises(ValueError):
        run(source, client)
    assert not client.responses.calls


def test_deadline_and_client_guards_precede_all_provider_calls(source):
    for model, deadline in [
        ("../invalid", BASE + 1_000),
        ("gpt-5.6", BASE + 200_000),
        ("gpt-5.6", BASE + 100),
    ]:
        client = Client()
        with pytest.raises(ValueError):
            run_risk_first(
                source_root=source, symbol="BTCUSDT", interval="1m",
                model=model, client=client,
                decision_ms=deadline, clock_ms=clock(),
            )
        assert client.responses.calls == []


def test_cli_explicit_paid_consent_required_before_sdk_init(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    root = (tmp_path / "nonexistent").resolve()
    out = (tmp_path / "output").resolve()
    args = ["--source-root", str(root), "--output-dir", str(out),
            "--symbol", "BTCUSDT", "--interval", "1m", "--model", "gpt-5.6"]
    with pytest.raises(SystemExit) as err:
        main(args)
    assert err.value.code == 2
    with pytest.raises(SystemExit):
        main(args + ["--allow-billed-llm-research",
                     "--acknowledge-usage-charges",
                     "--enable-risk-first-protocol"])
    assert not out.exists()
