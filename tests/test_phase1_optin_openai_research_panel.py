"""Offline opt-in OpenAI research panel: actual 5-call SDK shape, no network."""
from __future__ import annotations
from dataclasses import dataclass
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ci.phase1_optin_openai_research_panel import (
    main,run_panel,save_advisory,_ROLES,
)
from ci.phase1_spot_multiasset_ohlcv import collect,INTERVALS,ROWS

BASE=1_700_000_000_000


def fake_market(path,params=None):
    if path=="/api/v3/time":
        return json.dumps({"serverTime":BASE}).encode()
    if path=="/api/v3/exchangeInfo":
        symbol=params["symbol"]
        return json.dumps({"symbols":[{
            "symbol":symbol,"status":"TRADING",
            "baseAsset":symbol[:-4],"quoteAsset":"USDT",
        }]}).encode()
    if path=="/api/v3/klines":
        step=INTERVALS[params["interval"]]
        end=params["endTime"]
        begin=end-step+1-(ROWS-1)*step
        return json.dumps([
            [begin+i*step,"100","110","90","105","2",
             begin+(i+1)*step-1,"210",3,"1","105","0"]
            for i in range(ROWS)
        ]).encode()
    raise RuntimeError("external non-market endpoint is forbidden")


@pytest.fixture
def source(tmp_path):
    root=(tmp_path/"source").resolve()
    assert collect(root,requester=fake_market)["status"]=="PASSED_FINITE_REST_QUALITY_ONLY"
    return root


class FakeResponses:
    def __init__(self,*,risk_veto=True,break_role=None):
        self.risk_veto=risk_veto
        self.break_role=break_role
        self.requests=[]
    def create(self,**kwargs):
        self.requests.append(kwargs)
        i=len(self.requests)-1
        role=_ROLES[i]
        stance={
            "technical":"NEUTRAL","bull":"BULL","bear":"BEAR",
            "risk":"VETO" if self.risk_veto else "ALLOW_RESEARCH",
            "portfolio":"Hold",
        }[role]
        item={"stance":stance,"rationale":"Insufficient data for trading; research only."}
        if role==self.break_role:
            item["quantity"]=100_000
        return SimpleNamespace(
            status="completed",output_text=json.dumps(item),
            usage=SimpleNamespace(input_tokens=80,output_tokens=24),
        )


class Client:
    def __init__(self,**kwargs):
        self.responses=FakeResponses(**kwargs)


def safe_clock():
    instant=[BASE+100]
    def tick():
        instant[0]+=10
        return instant[0]
    return tick


def launch(src,client=None,clock=None,**kw):
    return run_panel(
        source_root=src,symbol="BTCUSDT",interval="1m",
        decision_ms=BASE+1000,model="gpt-5.6",
        client=client or Client(),clock_ms=clock or safe_clock(),**kw
    )


def test_five_orderless_no_store_structured_roles_pass_with_risk_veto(source,tmp_path):
    client=Client()
    report=launch(source,client=client)
    assert report["verdict"]=="PASSED_BOUNDED_LLM_ADVISORY_ONLY"
    assert report["model_calls"]==5
    assert len(client.responses.requests)==5
    assert report["total_output_tokens"]==120
    assert report["total_input_tokens"]==400
    assert report["model_cost_usd_unverified"] is True
    assert report["orders_submitted"]==0
    assert report["advisory"]["risk_veto"] is True
    assert report["advisory"]["verdict"]=="WITHHELD_RISK_VETO"
    assert [x["role"] for x in report["reports"]]==list(_ROLES)
    for req in client.responses.requests:
        assert req["store"] is False
        assert req["tools"]==[]
        assert req["tool_choice"]=="none"
        assert req["max_output_tokens"]==240
        assert req["text"]["format"]["strict"] is True
        assert req["text"]["format"]["schema"]["additionalProperties"] is False
        assert "quantity" not in req["input"]
        assert "private" not in req["input"]
        assert req["model"]=="gpt-5.6"
    folder=(tmp_path/"result").resolve()
    save_advisory(folder,report,source_root=source)
    assert (folder/"llm_advisory.json").is_file()
    assert not (folder/"api_key.txt").exists()
    assert json.loads((folder/"llm_advisory.json").read_text())["broker_access"] is False
    with pytest.raises(ValueError,match="safe completed"):
        save_advisory(folder,report,source_root=source)


def test_non_veto_research_hold_still_cannot_place_order(source):
    report=launch(source,client=Client(risk_veto=False))
    assert report["advisory"]["verdict"]=="RESEARCH_HOLD_NO_ACTION"
    assert report["advisory"]["portfolio_research_rating"]=="Hold"
    assert report["advisory"]["can_place_orders"] is False


def test_provider_schema_extra_order_field_aborts_all_quorum(source):
    client=Client(break_role="bear")
    with pytest.raises(ValueError,match="strict approved"):
        launch(source,client=client)
    assert len(client.responses.requests)==3


def test_model_response_refusal_or_unknown_usage_must_refuse(source):
    class InvalidClient(Client):
        def __init__(self,mode):
            super().__init__()
            self.mode=mode
            old=self.responses.create
            def bad(**kwargs):
                obj=old(**kwargs)
                if self.mode=="refused":obj.status="incomplete"
                if self.mode=="tokens":obj.usage.output_tokens=500
                return obj
            self.responses.create=bad
    for mode in ("refused","tokens"):
        with pytest.raises(ValueError):
            launch(source,client=InvalidClient(mode))


def test_untrusted_model_name_or_deadline_denied_before_provider_call(source):
    for name in ("","..; rm -rf /","bad provider/token",object()):
        client=Client()
        with pytest.raises(ValueError):
            run_panel(
                source_root=source,symbol="BTCUSDT",interval="1m",
                decision_ms=BASE+1000,model=name,client=client,
                clock_ms=safe_clock(),
            )
        assert not client.responses.requests
    client=Client()
    with pytest.raises(ValueError,match="deadline"):
        run_panel(
            source_root=source,symbol="BTCUSDT",interval="1m",
            decision_ms=BASE+200_000,model="gpt-5.6",client=client,
            clock_ms=safe_clock(),
        )
    assert client.responses.requests==[]


def test_llm_deadline_crossing_quarantines_without_advisory(source):
    client=Client()
    count=[0]
    def near_timeout():
        count[0]+=1
        return BASE+200 if count[0]<4 else BASE+1001
    with pytest.raises(ValueError,match="deadline"):
        launch(source,client=client,clock=near_timeout)
    assert len(client.responses.requests)<5


def test_no_openai_api_key_or_explicit_billed_consent_blocks_cli(
    tmp_path,monkeypatch,
):
    monkeypatch.delenv("OPENAI_API_KEY",raising=False)
    folder=(tmp_path/"no_network").resolve()
    with pytest.raises(SystemExit) as exc:
        main(["--source-root",str((tmp_path/"missing").resolve()),
              "--output-dir",str(folder),"--symbol","BTCUSDT",
              "--interval","1m","--model","gpt-5.6"])
    assert exc.value.code==2
    assert not folder.exists()


def test_tampered_saved_report_refused_against_private_source(source,tmp_path):
    report=launch(source)
    report["evidence"]["last_closed_price"]="99999"
    with pytest.raises(ValueError):
        save_advisory((tmp_path/"wrong").resolve(),report,source_root=source)
    assert not (tmp_path/"wrong").exists()
