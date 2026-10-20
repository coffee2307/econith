"""Finite, fail-closed point-in-time multi-agent ADVISORY contract.

Inspired by TradingAgents v0.7.0's typed analyst/debate/risk/portfolio roles
and point-in-time data policy. This is an original bounded research adapter,
NOT a TradingAgents runtime, an LLM integration, a live signal, or an order path.

A source report captured later than the decision time can NEVER justify a
historical recommendation even if the OHLCV candle closed before that time.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ci.phase1_spot_multiasset_ohlcv import (
    INTERVALS, SYMBOLS, audit_klines, strict_decimal,
)
from ci.phase1_spot_multiasset_ohlcv_audit import inspect as inspect_originals

SCHEMA = "econith-p1-pit-multiagent-advisory-v1"
MAX_SOURCE_AGE_MS = 600_000  # finite research only: no stale market snapshot
MAX_RATIONALE_CHARS = 1200
_ROLES = ("technical", "bull", "bear", "risk", "portfolio")
_VOTES = {
    "technical": frozenset(("BULL", "BEAR", "NEUTRAL")),
    "bull": frozenset(("BULL",)),
    "bear": frozenset(("BEAR",)),
    "risk": frozenset(("ALLOW_RESEARCH", "VETO")),
    "portfolio": frozenset(("Buy", "Overweight", "Hold", "Underweight", "Sell")),
}


def _canonical(obj: dict) -> bytes:
    return json.dumps(
        obj, ensure_ascii=False, sort_keys=True, separators=(",",":"),
        allow_nan=False,
    ).encode("utf-8")


def verified_asof_evidence(
    root: Path, *, symbol: str, interval: str, decision_ms: int,
) -> dict:
    """Read private original REST evidence after full independent quality audit.

    Uses observed source capture *end*, not vendor candle close alone, as the
    non-negotiable PIT upper bound. No inferred historical listing vintage.
    """
    if (type(decision_ms) is not int or decision_ms <= 0
        or type(symbol) is not str or symbol not in SYMBOLS
        or type(interval) is not str or interval not in INTERVALS
        or not isinstance(root, Path) or not root.is_absolute()
        or root.is_symlink() or not root.is_dir()
        or root != root.resolve()):
        raise ValueError("invalid finite PIT source/advisory identity")
    proof = inspect_originals(root)
    if proof.get("verdict") != "PASSED_FINITE_REST_ONLY":
        raise ValueError("independent original quality refused")
    report = json.loads((root/"aggregate_report.json").read_text(encoding="utf-8"))
    started = report.get("source_time_before_ms")
    observed = report.get("source_time_after_ms")
    if (type(started) is not int or type(observed) is not int
        or not 0 < started <= observed <= decision_ms
        or decision_ms - observed > MAX_SOURCE_AGE_MS):
        raise ValueError("LOOKAHEAD_OR_STALE: source first-observed vintage unavailable at decision")
    matching = [
        row for row in report.get("assets",[])
        if type(row) is dict and row.get("symbol")==symbol
    ]
    if len(matching)!=1:
        raise ValueError("symbol identity or present-day listing evidence missing")
    asset = matching[0]
    market=asset.get("market_snapshot")
    if (type(market) is not dict
        or market.get("status_at_capture")!="TRADING"
        or market.get("quote_asset")!="USDT"):
        raise ValueError("market not TRADING at independently audited capture")
    # A current exchangeInfo response can never assert what a symbol's
    # listing/tradability was at a date BEFORE this source capture.
    row=asset.get("intervals",{}).get(interval)
    if type(row) is not dict or row.get("closed_as_of_source_cutoff") is not True:
        raise ValueError("closed candle as-of evidence missing")
    raw_path=root/f"klines_{symbol.lower()}_{interval}.json"
    if raw_path.is_symlink():
        raise ValueError("original file alias rejected")
    raw=raw_path.read_bytes()
    source_sha=hashlib.sha256(raw).hexdigest()
    if report["originals"].get(raw_path.name,{}).get("sha256")!=source_sha:
        raise ValueError("original OHLCV digest differs from audited report")
    measured=audit_klines(raw,interval=interval,cutoff_ms=row["cutoff_ms"])
    candles=json.loads(raw)
    last=candles[-1]
    close_ms=last[6]
    if (type(close_ms) is not int or close_ms>started
        or close_ms>=decision_ms or measured["last_close_ms"]!=close_ms
        or row["last_close_ms"]!=close_ms):
        raise ValueError("candle closes after evidence or decision cutoff")
    price=str(strict_decimal(last[4],"last closed candle close"))
    result={
        "schema":SCHEMA,"symbol":symbol,"interval":interval,
        "decision_ms":decision_ms,
        "source_observed_before_ms":started,
        "source_observed_after_ms":observed,
        "last_closed_candle_end_ms":close_ms,
        "last_closed_price":price,
        "source_original_sha256":source_sha,
        "source_report_sha256":hashlib.sha256(
            (root/"aggregate_report.json").read_bytes()
        ).hexdigest(),
        "symbol_trading_at_capture_only":True,
        "historical_listing_vintage_certified":False,
        "market_history_continuity_certified":False,
        "venue_completeness_certified":False,
        "default_runtime_cutover":False,
        "orders_submitted":0,
    }
    result["evidence_sha256"]=hashlib.sha256(_canonical(result)).hexdigest()
    return result


def adjudicate_advisory(evidence:dict, reports:list[dict[str,Any]])->dict:
    """Typed analyst/research/risk review; no economic execution side-effects.

    A veto or inconclusive research view is WITHHELD; approved conclusions
    remain only non-executable research ratings, never order instructions.
    """
    if (type(evidence) is not dict
        or evidence.get("schema")!=SCHEMA
        or evidence.get("orders_submitted")!=0
        or type(evidence.get("evidence_sha256")) is not str
        or len(evidence["evidence_sha256"])!=64
        or hashlib.sha256(_canonical({
            k:v for k,v in evidence.items() if k!="evidence_sha256"
        })).hexdigest()!=evidence["evidence_sha256"]
        or type(evidence.get("decision_ms")) is not int
        or evidence.get("market_history_continuity_certified") is not False
        or evidence.get("historical_listing_vintage_certified") is not False
        or type(reports) is not list or len(reports)!=len(_ROLES)):
        raise ValueError("incomplete or altered evidence / structured agent quorum")
    by_role={}
    for item in reports:
        if (type(item) is not dict
            or set(item)!={"role","stance","evidence_sha256","available_at_ms","rationale"}):
            raise ValueError("agent schema permits no extra fields, order sizes or tools")
        role=item.get("role")
        if role not in _ROLES or role in by_role:
            raise ValueError("missing, duplicated or unsupported analyst role")
        if (type(item["stance"]) is not str
            or item["stance"] not in _VOTES[role]
            or item["evidence_sha256"]!=evidence["evidence_sha256"]
            or type(item["available_at_ms"]) is not int
            or not evidence["source_observed_after_ms"] <= item["available_at_ms"] <= evidence["decision_ms"]
            or type(item["rationale"]) is not str
            or not 1<=len(item["rationale"])<=MAX_RATIONALE_CHARS
            or any(ord(ch)<32 and ch not in "\n\t" for ch in item["rationale"])):
            raise ValueError("LOOKAHEAD_OR_UNGROUNDED: invalid agent report")
        by_role[role]=item
    if set(by_role)!=set(_ROLES):
        raise ValueError("required analyst/bull/bear/risk/portfolio quorum missing")
    veto=by_role["risk"]["stance"]=="VETO"
    rating=by_role["portfolio"]["stance"]
    # HOLD is a useful finding but never creates a trading signal.
    final="WITHHELD_RISK_VETO" if veto else (
        "RESEARCH_HOLD_NO_ACTION" if rating=="Hold" else
        "RESEARCH_RATING_NON_EXECUTABLE"
    )
    return {
        "schema":SCHEMA,"verdict":final,
        "symbol":evidence["symbol"],"interval":evidence["interval"],
        "decision_ms":evidence["decision_ms"],
        "evidence_sha256":evidence["evidence_sha256"],
        "risk_veto":veto,
        "portfolio_research_rating":rating if not veto else "WITHHELD",
        "analyst_roles":list(_ROLES),
        "advisory_only":True,"live_execution_authorized":False,
        "can_place_orders":False,"orders_submitted":0,
        "historical_listing_vintage_certified":False,
        "source_history_continuity_certified":False,
        "realized_returns_tested":False,
    }
