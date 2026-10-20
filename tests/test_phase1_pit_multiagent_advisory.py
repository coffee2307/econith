"""TradingAgents-inspired PIT/quorum contract; deterministic no-network tests."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile

import pytest

from ci.phase1_pit_multiagent_advisory import (
    SCHEMA,adjudicate_advisory,verified_asof_evidence,
)
from ci.phase1_spot_multiasset_ohlcv import collect,INTERVALS,ROWS

BASE=1_700_000_000_000


def candle(opened:int,step:int):
    return [opened,"100","110","90","105","2",
            opened+step-1,"210",3,"1","105","0"]


def requester(path:str,params=None):
    if path=="/api/v3/time":
        return json.dumps({"serverTime":BASE}).encode()
    if path=="/api/v3/exchangeInfo":
        sym=params["symbol"]
        return json.dumps({"symbols":[{
            "symbol":sym,"status":"TRADING",
            "baseAsset":sym.replace("USDT",""),"quoteAsset":"USDT",
        }]}).encode()
    if path=="/api/v3/klines":
        step=INTERVALS[params["interval"]]
        cutoff=params["endTime"]
        opened=cutoff-step+1-(ROWS-1)*step
        return json.dumps([candle(opened+n*step,step) for n in range(ROWS)]).encode()
    raise AssertionError(f"unexpected network endpoint {path}")


@pytest.fixture
def qualified(tmp_path):
    root=(tmp_path/"source").resolve()
    result=collect(root,requester=requester)
    assert result["status"]=="PASSED_FINITE_REST_QUALITY_ONLY"
    return root


def approved_roles(e):
    return [
        {"role":"technical","stance":"BULL","rationale":"Observed closed bar only",
         "available_at_ms":BASE,"evidence_sha256":e["evidence_sha256"]},
        {"role":"bull","stance":"BULL","rationale":"Upside case with cited data",
         "available_at_ms":BASE,"evidence_sha256":e["evidence_sha256"]},
        {"role":"bear","stance":"BEAR","rationale":"Downside case with cited data",
         "available_at_ms":BASE,"evidence_sha256":e["evidence_sha256"]},
        {"role":"risk","stance":"ALLOW_RESEARCH","rationale":"Advisory review only; no order",
         "available_at_ms":BASE,"evidence_sha256":e["evidence_sha256"]},
        {"role":"portfolio","stance":"Overweight","rationale":"Research-only preference",
         "available_at_ms":BASE,"evidence_sha256":e["evidence_sha256"]},
    ]


@pytest.mark.parametrize("symbol",["BTCUSDT","ETHUSDT","BNBUSDT"])
@pytest.mark.parametrize("interval",["1m","5m"])
def test_six_symbol_interval_combinations_pit_asof(qualified,symbol,interval):
    evidence=verified_asof_evidence(
        qualified,symbol=symbol,interval=interval,decision_ms=BASE+1000,
    )
    assert evidence["schema"]==SCHEMA
    assert evidence["symbol"]==symbol
    assert evidence["interval"]==interval
    assert evidence["source_observed_after_ms"]==BASE
    assert evidence["last_closed_candle_end_ms"]<BASE
    assert evidence["historical_listing_vintage_certified"] is False
    assert evidence["market_history_continuity_certified"] is False
    assert evidence["orders_submitted"]==0
    result=adjudicate_advisory(evidence,approved_roles(evidence))
    assert result["verdict"]=="RESEARCH_RATING_NON_EXECUTABLE"
    assert result["can_place_orders"] is False
    assert result["live_execution_authorized"] is False
    assert result["realized_returns_tested"] is False


@pytest.mark.parametrize("decision_ms",[BASE-1,BASE-300_000,0,BASE+600_001,True])
def test_later_collected_source_cannot_backtest_earlier_decision(qualified,decision_ms):
    with pytest.raises(ValueError):
        verified_asof_evidence(
            qualified,symbol="BTCUSDT",interval="1m",decision_ms=decision_ms,
        )


def test_missing_or_wrong_market_evidence_and_spot_symbol_refused(qualified):
    with pytest.raises(ValueError):
        verified_asof_evidence(qualified,symbol="SOLUSDT",interval="1m",
                                decision_ms=BASE+500)
    with pytest.raises(ValueError):
        verified_asof_evidence(qualified,symbol="BTCUSDT",interval="15m",
                                decision_ms=BASE+500)
    report=qualified/"aggregate_report.json"
    original=report.read_text()
    data=json.loads(original)
    data["assets"][0]["market_snapshot"]["status_at_capture"]="BREAK"
    report.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        verified_asof_evidence(qualified,symbol="BTCUSDT",interval="1m",
                                decision_ms=BASE+500)


def test_raw_market_tampering_refused_before_agent_voting(qualified):
    path=qualified/"klines_btcusdt_1m.json"
    path.write_bytes(path.read_bytes().replace(b'"105"',b'"104"',1))
    with pytest.raises(ValueError):
        verified_asof_evidence(qualified,symbol="BTCUSDT",interval="1m",
                                decision_ms=BASE+500)


def test_risk_veto_wins_without_execution(qualified):
    e=verified_asof_evidence(qualified,symbol="BTCUSDT",interval="1m",
                            decision_ms=BASE+1000)
    agents=approved_roles(e)
    agents[-2]["stance"]="VETO"
    out=adjudicate_advisory(e,agents)
    assert out["verdict"]=="WITHHELD_RISK_VETO"
    assert out["portfolio_research_rating"]=="WITHHELD"
    assert out["orders_submitted"]==0


def test_hold_is_not_a_trade_and_conflicting_cases_are_retained(qualified):
    e=verified_asof_evidence(qualified,symbol="BTCUSDT",interval="1m",
                            decision_ms=BASE+1000)
    agents=approved_roles(e)
    agents[-1]["stance"]="Hold"
    out=adjudicate_advisory(e,agents)
    assert out["verdict"]=="RESEARCH_HOLD_NO_ACTION"
    assert out["analyst_roles"]==["technical","bull","bear","risk","portfolio"]


@pytest.mark.parametrize("bad",[
    "extra_order","future_agent","late_evidence","missing_bear",
    "duplicate_bear","bad_portfolio","unsourced","empty_rationale",
    "wrong_technical_vote",
])
def test_untrusted_llm_decision_schema_or_temporal_claim_denied(qualified,bad):
    e=verified_asof_evidence(qualified,symbol="BTCUSDT",interval="1m",
                            decision_ms=BASE+1000)
    agents=approved_roles(e)
    if bad=="extra_order":agents[0]["quantity"]=100000
    elif bad=="future_agent":agents[0]["available_at_ms"]=BASE+2000
    elif bad=="late_evidence":agents[0]["available_at_ms"]=BASE-1
    elif bad=="missing_bear":agents.pop(2)
    elif bad=="duplicate_bear":agents[0]["role"]="bear"
    elif bad=="bad_portfolio":agents[-1]["stance"]="MEGA_BUY"
    elif bad=="unsourced":agents[0]["evidence_sha256"]="a"*64
    elif bad=="empty_rationale":agents[0]["rationale"]=""
    elif bad=="wrong_technical_vote":agents[0]["stance"]="BUY_NOW"
    with pytest.raises(ValueError):
        adjudicate_advisory(e,agents)


def test_evidence_body_and_order_flag_cannot_be_forged(qualified):
    e=verified_asof_evidence(qualified,symbol="BTCUSDT",interval="1m",
                            decision_ms=BASE+1000)
    reports=approved_roles(e)
    tampered=copy.deepcopy(e)
    tampered["last_closed_price"]="1000000"
    with pytest.raises(ValueError):
        adjudicate_advisory(tampered,reports)
    tampered=copy.deepcopy(e)
    tampered["orders_submitted"]=1
    with pytest.raises(ValueError):
        adjudicate_advisory(tampered,reports)


def test_no_technical_market_data_or_no_quorum_refuses():
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(ValueError):
            verified_asof_evidence(Path(tmp),symbol="BTCUSDT",
                                  interval="1m",decision_ms=BASE+1000)
