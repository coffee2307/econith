"""Decision-quality scorecard tests: two separately audited source vintages.

No LLM, public network, exchanges, cash, orders or simulated fill ledger.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from ci.phase1_pit_multiagent_advisory import verified_asof_evidence
from ci.phase1_pit_decision_scorecard import (
    evaluate,write_sealed_evidence,independently_reaudit_scorecard,
)
from ci.phase1_spot_multiasset_ohlcv import collect,INTERVALS,ROWS

BASE=1_700_000_000_000
FOLLOW=BASE+660_000
DECISION=BASE+1_000
OBSERVED=FOLLOW+2_000


def price(opened,step):
    # Stable across both source archives: a price is keyed by its opening
    # timestamp, never by the candle's position in one REST HTTP response.
    return 100+(opened//step)%11


def synthetic_requester(server_time):
    def request(path,params=None):
        if path=="/api/v3/time":
            return json.dumps({"serverTime":server_time}).encode()
        if path=="/api/v3/exchangeInfo":
            sym=params["symbol"]
            return json.dumps({"symbols":[{"symbol":sym,"status":"TRADING",
                      "baseAsset":sym[:-4],"quoteAsset":"USDT"}]}).encode()
        if path=="/api/v3/klines":
            step=INTERVALS[params["interval"]]
            cutoff=params["endTime"]
            start=cutoff-step+1-(ROWS-1)*step
            rows=[]
            for i in range(ROWS):
                opened=start+i*step
                p=str(price(opened,step))
                rows.append([opened,p,str(price(opened,step)+2),
                             str(price(opened,step)-2),p,"2",opened+step-1,
                             "210",3,"1","105","0"])
            return json.dumps(rows).encode()
        raise AssertionError("unapproved internet/broker endpoint")
    return request


@pytest.fixture
def sources(tmp_path):
    a=(tmp_path/"decision_archive").resolve()
    b=(tmp_path/"later_archive").resolve()
    assert collect(a,requester=synthetic_requester(BASE))["status"]=="PASSED_FINITE_REST_QUALITY_ONLY"
    assert collect(b,requester=synthetic_requester(FOLLOW))["status"]=="PASSED_FINITE_REST_QUALITY_ONLY"
    return a,b


def five_reports(e,rating="Buy",veto=False):
    return [
        {"role":"technical","stance":"BULL","rationale":"Observed and audited candle",
         "available_at_ms":BASE,"evidence_sha256":e["evidence_sha256"]},
        {"role":"bull","stance":"BULL","rationale":"Bullish case with past data",
         "available_at_ms":BASE,"evidence_sha256":e["evidence_sha256"]},
        {"role":"bear","stance":"BEAR","rationale":"Bearish case, also past data",
         "available_at_ms":BASE,"evidence_sha256":e["evidence_sha256"]},
        {"role":"risk","stance":"VETO" if veto else "ALLOW_RESEARCH",
         "rationale":"No live risk order authorization",
         "available_at_ms":BASE,"evidence_sha256":e["evidence_sha256"]},
        {"role":"portfolio","stance":rating,"rationale":"Research rating only",
         "available_at_ms":BASE,"evidence_sha256":e["evidence_sha256"]},
    ]


def score(sources,rating="Buy",veto=False,**options):
    old,new=sources
    symbol=options.pop("symbol","BTCUSDT")
    interval=options.pop("interval","1m")
    evidence=verified_asof_evidence(old,symbol=symbol,interval=interval,
                                      decision_ms=DECISION)
    return evaluate(decision_root=old,followup_root=new,symbol=symbol,
                    interval=interval,decision_ms=DECISION,
                    reports=five_reports(evidence,rating,veto),
                    observed_by_ms=OBSERVED,**options)


@pytest.mark.parametrize("symbol",["BTCUSDT","ETHUSDT","BNBUSDT"])
@pytest.mark.parametrize("interval",["1m","5m"])
def test_direction_scored_on_first_subsequently_closed_bar(sources,symbol,interval):
    got=score(sources,symbol=symbol,interval=interval)
    assert got["verdict"]=="PASSED_FINITE_DIRECTION_COMPARISON_ONLY"
    assert got["symbol"]==symbol and got["interval"]==interval
    assert got["decision_candle_end_ms"] < DECISION < got["first_forward_candle_end_ms"]
    assert got["first_forward_candle_end_ms"]-got["decision_candle_end_ms"]==INTERVALS[interval]
    assert got["decision_source_observed_after_ms"]==BASE
    assert got["followup_source_observed_after_ms"]==FOLLOW
    assert got["identical_overlapping_candles_audited"]>=80
    assert got["advisory_direction"]==1
    assert got["advisory_direction_correct"] in (True,False,None)
    assert got["prior_closed_bar_direction_correct"] in (True,False,None)
    assert got["out_of_sample_profitability_proven"] is False
    assert got["net_profit_after_costs_computed"] is False
    assert got["orders_submitted"]==0


@pytest.mark.parametrize("rating,sign",[
    ("Buy",1),("Overweight",1),("Sell",-1),("Underweight",-1),("Hold",0),
])
def test_typed_direction_not_a_cash_position(sources,rating,sign):
    got=score(sources,rating=rating)
    assert got["advisory_direction"]==sign
    assert got["advisory_direction_correct"] is None if sign==0 else got["advisory_direction_correct"] in (True,False,None)
    assert "quantity" not in got and "trade_notional" not in got and "profit" not in got
    assert got["live_orders_enabled"] is False


def test_risk_veto_is_not_an_executed_short_or_long(sources):
    got=score(sources,rating="Sell",veto=True)
    assert got["advisory_verdict"]=="WITHHELD_RISK_VETO"
    assert got["advisory_direction"]==0
    assert got["advisory_rating"]=="WITHHELD"
    assert got["advisory_direction_correct"] is None


def test_output_is_sealed_and_auditor_recomputes_entire_two_source_chain(sources,tmp_path):
    a,b=sources
    e=verified_asof_evidence(a,symbol="BTCUSDT",interval="1m",decision_ms=DECISION)
    reports=five_reports(e)
    dst=(tmp_path/"out").resolve()
    result=write_sealed_evidence(
        dst,decision_root=a,followup_root=b,symbol="BTCUSDT",interval="1m",
        decision_ms=DECISION,reports=reports,observed_by_ms=OBSERVED,
    )
    inspected=independently_reaudit_scorecard(dst,a,b)
    assert inspected["verdict"]=="PASSED_READONLY_ORIGINALS_AND_DECISION_RECOMPUTATION_ONLY"
    assert inspected["score_sha256"]==result["score_sha256"]
    with pytest.raises(ValueError):
        write_sealed_evidence(dst,decision_root=a,followup_root=b,
            symbol="BTCUSDT",interval="1m",decision_ms=DECISION,
            reports=reports,observed_by_ms=OBSERVED)
    report=dst/"finite_scorecard.json"
    payload=json.loads(report.read_text())
    payload["score"]["raw_asset_return_decimal"]="999"
    report.write_text(json.dumps(payload))
    with pytest.raises(ValueError,match="diverges"):
        independently_reaudit_scorecard(dst,a,b)


def test_evaluation_not_allowed_before_new_archive_observed(sources):
    a,b=sources
    e=verified_asof_evidence(a,symbol="BTCUSDT",interval="1m",decision_ms=DECISION)
    with pytest.raises(ValueError,match="LOOKAHEAD"):
        evaluate(decision_root=a,followup_root=b,symbol="BTCUSDT",
                 interval="1m",decision_ms=DECISION,reports=five_reports(e),
                 observed_by_ms=FOLLOW-1)


def test_reusing_same_archive_not_a_forward_test(sources):
    a,_=sources
    e=verified_asof_evidence(a,symbol="BTCUSDT",interval="1m",decision_ms=DECISION)
    with pytest.raises(ValueError):
        evaluate(decision_root=a,followup_root=a,symbol="BTCUSDT",interval="1m",
                 decision_ms=DECISION,reports=five_reports(e),observed_by_ms=OBSERVED)


def test_reject_future_closed_at_same_cutoff_as_decision_archive(sources,tmp_path):
    a,_=sources
    second=(tmp_path/"same_period").resolve()
    assert collect(second,requester=synthetic_requester(BASE+2_000))["status"]=="PASSED_FINITE_REST_QUALITY_ONLY"
    e=verified_asof_evidence(a,symbol="BTCUSDT",interval="1m",decision_ms=DECISION)
    with pytest.raises(ValueError,match="no independently closed forward outcome"):
        evaluate(decision_root=a,followup_root=second,symbol="BTCUSDT",
                 interval="1m",decision_ms=DECISION,reports=five_reports(e),
                 observed_by_ms=BASE+3_000)


def test_different_historical_candle_on_followup_archive_refused_even_if_hash_updated(sources):
    old,new=sources
    path=new/"klines_btcusdt_1m.json"
    rows=json.loads(path.read_bytes())
    old_close=(BASE//60_000)*60_000-1
    target=next(x for x in rows if x[6]==old_close)
    target[4]=str(int(target[4])+1)
    # Still inside OHLC geometry, so per-archive market-quality audit passes.
    path.write_text(json.dumps(rows).encode())
    report=new/"aggregate_report.json"
    data=json.loads(report.read_text())
    data["originals"][path.name]={"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
                                   "bytes":path.stat().st_size}
    report.write_text(json.dumps(data))
    with pytest.raises(ValueError,match="overlapping original candles"):
        score((old,new))


def test_tampered_later_original_rejected(sources):
    old,new=sources
    path=new/"klines_btcusdt_1m.json"
    path.write_bytes(path.read_bytes().replace(b'"210"',b'"211"',1))
    with pytest.raises(ValueError):
        score((old,new))


def test_missing_agent_or_postdated_agent_cannot_gain_future_score(sources):
    a,b=sources
    e=verified_asof_evidence(a,symbol="BTCUSDT",interval="1m",decision_ms=DECISION)
    for corrupted in ("missing_role","late_role"):
        reports=five_reports(e)
        if corrupted=="missing_role":reports.pop(2)
        else:reports[0]["available_at_ms"]=DECISION+10
        with pytest.raises(ValueError):
            evaluate(decision_root=a,followup_root=b,symbol="BTCUSDT",
                     interval="1m",decision_ms=DECISION,
                     reports=reports,observed_by_ms=OBSERVED)
