"""P1-07 opt-in 5-role OpenAI analyst panel, advisory ONLY, no order path.

Requires a privately audited as-of Spot OHLCV root. Real OpenAI Responses
calls are ONLY inside CLI after explicit paid-call opt-in, with zero SDK
retries, no tools, no conversation storage request and bounded output tokens.

A user-provided future decision deadline is deliberately conservative: every
LLM's locally observed completion must occur before that deadline. If a
provider response is slow, incomplete, refused or lacks structured metadata,
the whole advisory fails closed. No profit or exchange provenance claimed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Callable

from ci.phase1_pit_multiagent_advisory import (
    _ROLES, _VOTES, _canonical, adjudicate_advisory,
    verified_asof_evidence,
)

SCHEMA="econith-p1-openai-panel-optional-research-v1"
_MAX_OUTPUT_TOKENS=240
_MAX_INPUT_TOKENS=4096
_MAX_CALLS=5
_MODEL_RE=re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,95}$")
_SYSTEM=(
    "You are one member of a five-role, simulation-only ECONITH crypto research "
    "panel. Use ONLY the supplied independently checked closed-candle summary; "
    "do not invent news, live prices, fill costs, unprovided indicators, "
    "returns or market history. No tools, execution, orders, quantities, "
    "accounts, broker access or investment instructions. Output only the "
    "requested JSON object with a concise rationale under 1200 characters."
)
_ROLE_TASK={
    "technical":"Review the limited technical closed-candle evidence. Choose BULL, BEAR or NEUTRAL.",
    "bull":"Describe a plausible bullish research hypothesis using ONLY supplied data; stance BULL.",
    "bear":"Describe a plausible bearish research hypothesis using ONLY supplied data; stance BEAR.",
    "risk":"Evaluate whether this very limited data supports even a research rating. Prefer VETO when evidence is insufficient; otherwise ALLOW_RESEARCH.",
    "portfolio":"After considering the limits of the evidence, choose a non-executable research rating: Buy, Overweight, Hold, Underweight or Sell. Default Hold when unsupported.",
}


def _prompt(evidence:dict,role:str)->str:
    context={key:evidence[key] for key in (
        "symbol","interval","source_observed_after_ms","decision_ms",
        "last_closed_candle_end_ms","last_closed_price","evidence_sha256",
        "historical_listing_vintage_certified",
        "market_history_continuity_certified",
    )}
    return (_ROLE_TASK[role]+"\nVerified limited source evidence:\n"+
            json.dumps(context,sort_keys=True,separators=(",",":"),allow_nan=False))


def run_panel(
    *, source_root:Path,symbol:str,interval:str,decision_ms:int,model:str,
    client:Any,clock_ms:Callable[[],int]=lambda:time.time_ns()//1_000_000,
)->dict:
    """Makes at most five Responses API calls, no retries in this function.

    Caller must supply an actual no-retry SDK client for billed calls.
    Mocking client in offline tests cannot prove real provider success.
    """
    if (type(model) is not str or not _MODEL_RE.fullmatch(model)
        or type(decision_ms) is not int or not callable(clock_ms)
        or client is None or not hasattr(getattr(client,"responses",None),"create")):
        raise ValueError("invalid model, deadline or model client")
    start=clock_ms()
    if (type(start) is not int or start<=0
        or not 0<decision_ms-start<=180_000):
        raise ValueError("research decision deadline must be future and <=180s")
    evidence=verified_asof_evidence(
        source_root,symbol=symbol,interval=interval,decision_ms=decision_ms,
    )
    outputs=[]
    events=[]
    for role in _ROLES:
        if clock_ms()>decision_ms:
            raise ValueError("model research exceeded decision deadline")
        prompt=_prompt(evidence,role)
        allowed=sorted(_VOTES[role])
        response=client.responses.create(
            model=model,store=False,stream=False,tools=[],
            tool_choice="none",
            instructions=_SYSTEM,
            input=prompt,
            text={"format":{
                "type":"json_schema","name":f"econith_{role}_research_v1",
                "strict":True,
                "schema":{
                    "type":"object","additionalProperties":False,
                    "properties":{
                        "stance":{"type":"string","enum":allowed},
                        "rationale":{"type":"string"},
                    },"required":["stance","rationale"],
                },
            }},
            max_output_tokens=_MAX_OUTPUT_TOKENS,
        )
        done=clock_ms()
        if type(done) is not int or done>decision_ms or done<start:
            raise ValueError("model response arrived after decision deadline")
        if (getattr(response,"status",None)!="completed"
            or type(getattr(response,"output_text",None)) is not str
            or len(response.output_text)>4096):
            raise ValueError("refusal/incomplete or oversized model response")
        result=json.loads(response.output_text)
        if (type(result) is not dict or set(result)!={"stance","rationale"}
            or type(result["stance"]) is not str
            or result["stance"] not in _VOTES[role]
            or type(result["rationale"]) is not str
            or not 1<=len(result["rationale"])<=1200):
            raise ValueError("model did not produce strict approved research fields")
        usage=getattr(response,"usage",None)
        input_tokens=getattr(usage,"input_tokens",None)
        output_tokens=getattr(usage,"output_tokens",None)
        if (type(input_tokens) is not int or not 0<=input_tokens<=_MAX_INPUT_TOKENS
            or type(output_tokens) is not int
            or not 0<=output_tokens<=_MAX_OUTPUT_TOKENS):
            raise ValueError("token usage absent/outside research call budget")
        reports={
            "role":role,"stance":result["stance"],
            "evidence_sha256":evidence["evidence_sha256"],
            "available_at_ms":done,"rationale":result["rationale"],
        }
        outputs.append(reports)
        events.append({
            "role":role,"model":model,
            "system_sha256":hashlib.sha256(_SYSTEM.encode()).hexdigest(),
            "input_sha256":hashlib.sha256(prompt.encode()).hexdigest(),
            "output_sha256":hashlib.sha256(response.output_text.encode()).hexdigest(),
            "completed_at_ms":done,
            "input_tokens":input_tokens,
            "output_tokens":output_tokens,
            "store_requested":False,"tools_enabled":False,
        })
    decision=adjudicate_advisory(evidence,outputs,source_root=source_root)
    return {
        "schema":SCHEMA,"verdict":"PASSED_BOUNDED_LLM_ADVISORY_ONLY",
        "model":model,"evidence":evidence,"advisory":decision,
        "reports":outputs,"model_events":events,
        "model_calls":len(events),"total_input_tokens":sum(x["input_tokens"] for x in events),
        "total_output_tokens":sum(x["output_tokens"] for x in events),
        "model_cost_usd_unverified":True,
        "agent_provider_responses_not_exchange_signed":True,
        "historical_backtest_proven":False,"realized_pnl_proven":False,
        "broker_access":False,"orders_submitted":0,
    }


def save_advisory(record_root:Path,record:dict,*,source_root:Path)->None:
    """Write a finished decision locally, never a raw API key or order plan."""
    if (not isinstance(record_root,Path) or not record_root.is_absolute()
        or record_root.exists() or record_root.is_symlink()
        or record_root!=record_root.resolve()
        or record_root.parent.is_symlink()
        or record.get("verdict")!="PASSED_BOUNDED_LLM_ADVISORY_ONLY"
        or record.get("orders_submitted")!=0):
        raise ValueError("not a safe completed isolated advisory directory")
    actual=adjudicate_advisory(
        record["evidence"],record["reports"],source_root=source_root,
    )
    if actual!=record["advisory"] or record.get("model_calls")!=5:
        raise ValueError("advisory has not survived local source recheck")
    record_root.mkdir(mode=0o700,parents=False)
    with (record_root/"llm_advisory.json").open("x",encoding="utf-8") as f:
        json.dump(record,f,sort_keys=True,indent=2,allow_nan=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())


def main(argv=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--allow-billed-llm-research",action="store_true")
    p.add_argument("--acknowledge-usage-charges",action="store_true")
    p.add_argument("--source-root",type=Path,required=True)
    p.add_argument("--output-dir",type=Path,required=True)
    p.add_argument("--symbol",required=True)
    p.add_argument("--interval",required=True)
    p.add_argument("--model",required=True)
    p.add_argument("--decision-window-seconds",type=int,default=120)
    a=p.parse_args(argv)
    if (not a.allow_billed_llm_research
        or not a.acknowledge_usage_charges
        or not os.environ.get("OPENAI_API_KEY")
        or not 10<=a.decision_window_seconds<=180):
        p.error("explicit billed research consent, OPENAI_API_KEY, and 10-180s decision window required")
    # No OpenAI call or dependency initialization before explicit opt-in.
    from openai import OpenAI
    client=OpenAI(
        api_key=os.environ["OPENAI_API_KEY"],max_retries=0,timeout=20.0,
    )
    try:
        report=run_panel(
            source_root=a.source_root,symbol=a.symbol,interval=a.interval,
            decision_ms=time.time_ns()//1_000_000+a.decision_window_seconds*1000,
            model=a.model,client=client,
        )
        save_advisory(a.output_dir,report,source_root=a.source_root)
    except (ValueError,TypeError,KeyError,OSError,json.JSONDecodeError):
        print(json.dumps({
            "verdict":"REFUSED_NO_RESEARCH_REPORT",
            "partial_model_spend_possible":True,
            "orders_submitted":0,
        }))
        return 2
    print(json.dumps({
        "verdict":report["verdict"],"model_calls":report["model_calls"],
        "output_dir":str(a.output_dir),"orders_submitted":0,
        "cost_usd_measured":False,
    }))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
