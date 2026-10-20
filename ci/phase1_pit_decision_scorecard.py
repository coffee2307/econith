"""P1-07: independent, finite, no-order POINT-IN-TIME advisory outcome scorecard.

Inspired by the decision-quality grid in TradingAgents v0.7.0, not its fill
simulator (it does not offer one in backtest.py). This scores ONE direction call
against a subsequently observed, independently audited, first eligible Spot
closed candle and against a deterministic pre-decision momentum baseline.

It is not portfolio PnL: no fills, fees, slippage, short borrow, funding, cash,
trade sizing, exchange-origin signature, future completeness or profit claim.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sys

from ci.phase1_pit_multiagent_advisory import (
    SCHEMA as PIT_SCHEMA, _canonical, adjudicate_advisory,
    verified_asof_evidence,
)
from ci.phase1_spot_multiasset_ohlcv import (
    INTERVALS, audit_klines, strict_decimal,
)
from ci.phase1_spot_multiasset_ohlcv_audit import inspect as audit_originals

SCHEMA = "econith-p1-pit-advisory-forward-close-quality-v1"
_SIGN = {"Buy":1,"Overweight":1,"Hold":0,"Underweight":-1,"Sell":-1}


def _is_safe_root(root:Path)->bool:
    return (isinstance(root,Path) and root.is_absolute() and root.is_dir()
            and not root.is_symlink() and root==root.resolve())


def evaluate(
    *, decision_root:Path, followup_root:Path,
    symbol:str, interval:str, decision_ms:int,
    reports:list[dict],
    observed_by_ms:int,
)->dict:
    """Read source data twice under separate as-of provenance, score no trades.

    Refuse to score when the next post-decision candle is missing, when the
    original/revision overlap conflicts, or when collection vintage is invalid.
    """
    if (not _is_safe_root(decision_root) or not _is_safe_root(followup_root)
        or decision_root==followup_root
        or type(decision_ms) is not int
        or type(observed_by_ms) is not int
        or observed_by_ms<=decision_ms
        or type(interval) is not str or interval not in INTERVALS):
        raise ValueError("invalid separated decision/follow-up evidence roots or times")
    ev=verified_asof_evidence(
        decision_root,symbol=symbol,interval=interval,
        decision_ms=decision_ms,
    )
    # Quorum reopens ALL original bytes and rejects self-hashed spoofing.
    view=adjudicate_advisory(ev,reports,source_root=decision_root)
    audit=audit_originals(followup_root)
    if audit.get("verdict")!="PASSED_FINITE_REST_ONLY":
        raise ValueError("unverified future archive")
    other=json.loads((followup_root/"aggregate_report.json").read_text())
    future_first=other.get("source_time_before_ms")
    future_last=other.get("source_time_after_ms")
    if (type(future_first) is not int or type(future_last) is not int
        or not decision_ms < future_first <= future_last <= observed_by_ms):
        raise ValueError("LOOKAHEAD: follow-up snapshot not yet observed or not later")
    # Do not claim exchange listing history: current status at capture cannot
    # supply historical tradability, and neither archive has signed provenance.
    path1=decision_root/f"klines_{symbol.lower()}_{interval}.json"
    path2=followup_root/f"klines_{symbol.lower()}_{interval}.json"
    if path1.is_symlink() or path2.is_symlink():
        raise ValueError("aliased OHLCV source evidence")
    old_raw=path1.read_bytes()
    new_raw=path2.read_bytes()
    step=INTERVALS[interval]
    old_rows=json.loads(old_raw)
    new_rows=json.loads(new_raw)
    old_cutoff=ev["last_closed_candle_end_ms"]
    future_cutoff=(future_first//step)*step-1
    old_measure=audit_klines(old_raw,interval=interval,cutoff_ms=old_cutoff)
    new_measure=audit_klines(new_raw,interval=interval,cutoff_ms=future_cutoff)
    if (old_measure["candles"]<2 or new_measure["candles"]<2
        or new_measure["last_close_ms"]<old_cutoff+step):
        raise ValueError("no independently closed forward outcome available")
    old_last=old_rows[-1]
    if (old_last[6]!=old_cutoff
        or old_last[6]>=decision_ms
        or old_cutoff+step<=decision_ms):
        # The first immediately following candle must not have closed before
        # the original decision. Otherwise the "one-bar" definition changes.
        raise ValueError("decision is not immediately before first future close")
    old_price=strict_decimal(old_last[4],"decision close")
    previous_price=strict_decimal(old_rows[-2][4],"previous close")
    if old_price<=0 or previous_price<=0:
        raise ValueError("non-positive reference price")
    # Require two independent archives to agree about the overlapping
    # decision close; revisions are NOT silently used to rewrite the past.
    old_footprint={
        r[0]:(r[1],r[2],r[3],r[4],r[5],r[6],r[7],r[8],r[9],r[10])
        for r in old_rows
    }
    new_footprint={
        r[0]:(r[1],r[2],r[3],r[4],r[5],r[6],r[7],r[8],r[9],r[10])
        for r in new_rows
    }
    shared=set(old_footprint)&set(new_footprint)
    if (old_last[0] not in shared or
        any(old_footprint[k]!=new_footprint[k] for k in shared)):
        raise ValueError("source archives disagree on overlapping original candles")
    first_forward_end=old_cutoff+step
    post=[r for r in new_rows if r[6]==first_forward_end]
    if len(post)!=1 or first_forward_end>future_first:
        raise ValueError("first post-decision close unobserved or absent")
    outcome_price=strict_decimal(post[0][4],"forward candle close")
    if outcome_price<=0:
        raise ValueError("invalid outcome close")
    delta=(outcome_price-old_price)/old_price
    realized_dir=(1 if delta>0 else -1 if delta<0 else 0)
    rating=view["portfolio_research_rating"]
    vote=0 if view["risk_veto"] else _SIGN[rating]
    baseline=(1 if old_price>previous_price
              else -1 if old_price<previous_price else 0)
    # Direction agreement, NOT an executed trade, a cash return or a Sharpe.
    def correctness(sign:int):
        return None if sign==0 or realized_dir==0 else sign==realized_dir
    score={
        "schema":SCHEMA,
        "verdict":"PASSED_FINITE_DIRECTION_COMPARISON_ONLY",
        "symbol":symbol,"interval":interval,
        "decision_ms":decision_ms,"observed_by_ms":observed_by_ms,
        "decision_source_sha256":ev["source_original_sha256"],
        "decision_evidence_sha256":ev["evidence_sha256"],
        "followup_source_sha256":hashlib.sha256(new_raw).hexdigest(),
        "followup_report_sha256":hashlib.sha256(
            (followup_root/"aggregate_report.json").read_bytes()
        ).hexdigest(),
        "decision_source_observed_after_ms":ev["source_observed_after_ms"],
        "followup_source_observed_after_ms":future_last,
        "decision_candle_end_ms":old_cutoff,
        "first_forward_candle_end_ms":first_forward_end,
        "decision_close":str(old_price),
        "first_forward_close":str(outcome_price),
        "raw_asset_return_decimal":str(delta),
        "realized_direction":realized_dir,
        "advisory_verdict":view["verdict"],
        "advisory_rating":rating,
        "advisory_direction":vote,
        "advisory_direction_correct":correctness(vote),
        "prior_closed_bar_momentum_direction":baseline,
        "prior_closed_bar_direction_correct":correctness(baseline),
        "identical_overlapping_candles_audited":len(shared),
        "complete_exchange_history_certified":False,
        "out_of_sample_profitability_proven":False,
        "net_profit_after_costs_computed":False,
        "short_execution_feasibility_tested":False,
        "broker_access":False,"live_orders_enabled":False,"orders_submitted":0,
    }
    score["score_sha256"]=hashlib.sha256(_canonical(score)).hexdigest()
    return score


def write_sealed_evidence(
    output_dir:Path,*,decision_root:Path,followup_root:Path,
    symbol:str,interval:str,decision_ms:int,reports:list[dict],
    observed_by_ms:int,
)->dict:
    """Fresh output root, immutable report + explicit source-grounded receipt."""
    if (not isinstance(output_dir,Path) or not output_dir.is_absolute()
        or output_dir.exists() or output_dir.is_symlink()
        or output_dir!=output_dir.resolve()
        or output_dir.parent.is_symlink()):
        raise ValueError("new isolated output root required")
    score=evaluate(
        decision_root=decision_root,followup_root=followup_root,
        symbol=symbol,interval=interval,decision_ms=decision_ms,
        reports=reports,observed_by_ms=observed_by_ms,
    )
    output_dir.mkdir(mode=0o700,parents=False)
    payload={
        "schema":SCHEMA,"score":score,"reports":reports,
        "decision_symbol":symbol,"interval":interval,
        "decision_ms":decision_ms,"observed_by_ms":observed_by_ms,
        "raw_sources_embedded":False,
        "orders_submitted":0,
    }
    with (output_dir/"finite_scorecard.json").open("x",encoding="utf-8") as f:
        f.write(json.dumps(payload,sort_keys=True,indent=2,allow_nan=False)+"\n")
        f.flush()
        os.fsync(f.fileno())
    return score


def independently_reaudit_scorecard(
    record_dir:Path,decision_root:Path,followup_root:Path,
)->dict:
    """Recompute, not just hash-compare caller-controlled JSON."""
    if (not _is_safe_root(record_dir)
        or (record_dir/"finite_scorecard.json").is_symlink()):
        raise ValueError("invalid scorecard root")
    payload=json.loads((record_dir/"finite_scorecard.json").read_text())
    if (type(payload) is not dict or set(payload)!={
        "schema","score","reports","decision_symbol","interval",
        "decision_ms","observed_by_ms","raw_sources_embedded","orders_submitted",
    } or payload["schema"]!=SCHEMA or payload["raw_sources_embedded"] is not False
        or payload["orders_submitted"]!=0):
        raise ValueError("incomplete or executable scorecard")
    rebuilt=evaluate(
        decision_root=decision_root,followup_root=followup_root,
        symbol=payload["decision_symbol"],interval=payload["interval"],
        decision_ms=payload["decision_ms"],
        reports=payload["reports"],observed_by_ms=payload["observed_by_ms"],
    )
    if rebuilt != payload["score"]:
        raise ValueError("outcome/return scorecard diverges from original proof")
    return {
        "verdict":"PASSED_READONLY_ORIGINALS_AND_DECISION_RECOMPUTATION_ONLY",
        "score_sha256":rebuilt["score_sha256"],
        "advisory_direction_correct":rebuilt["advisory_direction_correct"],
        "baseline_direction_correct":rebuilt["prior_closed_bar_direction_correct"],
        "real_trading_results_proven":False,
        "orders_submitted":0,
    }


def main(argv=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--allow-research-scorecard",action="store_true")
    p.add_argument("--acknowledge-not-portfolio-pnl",action="store_true")
    p.add_argument("--decision-root",type=Path,required=True)
    p.add_argument("--followup-root",type=Path,required=True)
    p.add_argument("--output-dir",type=Path,required=True)
    p.add_argument("--symbol",required=True)
    p.add_argument("--interval",required=True)
    p.add_argument("--decision-ms",type=int,required=True)
    p.add_argument("--observed-by-ms",type=int,required=True)
    p.add_argument("--reports-json",type=Path,required=True)
    a=p.parse_args(argv)
    if not a.allow_research_scorecard or not a.acknowledge_not_portfolio_pnl:
        p.error("explicit research-only and non-PnL acknowledgement required")
    try:
        reports=json.loads(a.reports_json.read_text())
        result=write_sealed_evidence(
            a.output_dir,decision_root=a.decision_root,
            followup_root=a.followup_root,symbol=a.symbol,
            interval=a.interval,decision_ms=a.decision_ms,
            reports=reports,observed_by_ms=a.observed_by_ms,
        )
        inspect=independently_reaudit_scorecard(
            a.output_dir,a.decision_root,a.followup_root,
        )
    except (OSError,ValueError,TypeError,KeyError,IndexError) as exc:
        print(json.dumps({"verdict":"REFUSED","error_class":type(exc).__name__,
                          "real_trading_results_proven":False,"orders_submitted":0}))
        return 2
    print(json.dumps({"scorecard":result,"independent_audit":inspect},sort_keys=True))
    return 0


if __name__=="__main__":
    sys.exit(main())
