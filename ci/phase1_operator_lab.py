"""One-command safe Phase 1 operator doctor, public Spot trial, and evidence report.

Pure standard-library host doctor/report. Running public data is always an
EXPLICIT separate 'smoke' command with --allow-public-market-data; no account
credentials, order APIs, default runtime, GPU, container or admin privileges.

Run: python -m ci.phase1_operator_lab doctor
     python -m ci.phase1_operator_lab smoke --allow-public-market-data
     python -m ci.phase1_operator_lab report --trial-dir ABSOLUTE_PATH
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_PROFILES={
    "quick":{"attempts":3,"max_frames":160,"timeout_s":45,
             "max_storage_mb":100,"watch_hours":None},
    "diagnostic":{"attempts":3,"max_frames":500,"timeout_s":120,
             "max_storage_mb":150,"watch_hours":None},
    "hour":{"attempts":2048,"max_frames":1000,"timeout_s":120,
            "max_storage_mb":1800,"watch_hours":1.0},
    "day":{"attempts":2048,"max_frames":2000,"timeout_s":120,
           "max_storage_mb":1800,"watch_hours":24.0},
}


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_evidence_base() -> Path:
    return (_root().parent/"ECONITH-Phase1-Evidence").resolve()


def _memory() -> tuple[int|None,int|None]:
    if sys.platform=="win32":
        class MemoryStatus(ctypes.Structure):
            _fields_=[("length",ctypes.c_ulong),
                      ("load",ctypes.c_ulong),
                      ("total",ctypes.c_ulonglong),
                      ("avail",ctypes.c_ulonglong),
                      ("page_total",ctypes.c_ulonglong),
                      ("page_avail",ctypes.c_ulonglong),
                      ("virtual_total",ctypes.c_ulonglong),
                      ("virtual_avail",ctypes.c_ulonglong),
                      ("extended",ctypes.c_ulonglong)]
        status=MemoryStatus()
        status.length=ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.total,status.avail
    elif sys.platform.startswith("linux"):
        try:
            data={}
            for line in Path("/proc/meminfo").read_text().splitlines():
                k,_,v=line.partition(":")
                if k in ("MemTotal","MemAvailable"):
                    data[k]=int(v.strip().split()[0])*1024
            return data.get("MemTotal"),data.get("MemAvailable")
        except (OSError,ValueError,IndexError):
            pass
    return None,None


def doctor(*,evidence_base:Path|None=None) -> dict:
    base=(evidence_base or _default_evidence_base()).expanduser().resolve()
    target=base if base.exists() else next(
        (parent for parent in base.parents if parent.exists()),Path.cwd(),
    )
    total,free=_memory()
    disk=shutil.disk_usage(target)
    ram_gib=round(total/2**30,2) if total is not None else None
    free_gib=round(free/2**30,2) if free is not None else None
    warnings=[]
    if free is not None and free < 1.5*2**30:
        warnings.append("LOW_AVAILABLE_RAM: close browsers/heavy apps before trial")
    if disk.free < 4*2**30:
        warnings.append("LOW_DISK_HEADROOM: use another drive or a CPU-only VPS")
    if sys.version_info[:2]!=(3,12):
        warnings.append("UNTESTED_PYTHON: CI validates Python 3.12")
    packages={}
    for module in ("httpx","websockets","pydantic","numpy"):
        try:
            __import__(module)
            packages[module]="available"
        except ImportError:
            packages[module]="missing"
    if any(value=="missing" for value in packages.values()):
        warnings.append("MISSING_DEPENDENCIES: install pinned requirements.txt in a venv")
    return {
        "platform":platform.platform(),
        "python":platform.python_version(),
        "cpu_logical":os.cpu_count(),
        "ram_total_gib":ram_gib,
        "ram_available_gib":free_gib,
        "evidence_target_drive_free_gib":round(disk.free/2**30,2),
        "recommended_local_profile":"quick",
        "gpu_required":False,
        "packages":packages,
        "warnings":warnings,
        "no_live_trading":True,
    }


def _readonly(db_path:Path) -> sqlite3.Connection:
    if not db_path.is_file() or db_path.is_symlink():
        raise ValueError("expected SQLite evidence ledger not found")
    return sqlite3.connect(db_path.resolve().as_uri()+"?mode=ro",uri=True)


def inspect_trial(trial_dir:Path) -> dict:
    root=trial_dir.expanduser().resolve()
    ledger=root/"evidence"/"forward_supervisor.sqlite"
    with _readonly(ledger) as db:
        quick=db.execute("PRAGMA quick_check").fetchone()[0]
        if quick!="ok":
            raise ValueError("supervisor ledger integrity failure")
        plan=db.execute("""
            SELECT planned_watch_duration_s,attempts_ceiling,
                   storage_ceiling_bytes,admitted_at_ms,completed_at_ms,
                   completion_code FROM forward_watch_plan
        """).fetchone()
        attempts=db.execute("""
            SELECT iteration,session_id,status,failure_code,originals,
                   receiver_acks,queue_high_watermark,receiver_process_starts,
                   max_receiver_ack_latency_ms
            FROM forward_watch_attempt ORDER BY iteration
        """).fetchall()
        outages=db.execute("""
            SELECT opened_ms,restored_ms,last_failure_code,coverage_verified
            FROM forward_watch_outage ORDER BY outage_id
        """).fetchall()
        boundaries=db.execute("""
            SELECT previous_iteration,next_iteration,coverage_verified
            FROM forward_watch_unobserved_boundary ORDER BY boundary_id
        """).fetchall()
    if plan is None:
        raise ValueError("missing durable operator watch plan")
    if any(x[3]!=0 for x in outages) or any(x[2]!=0 for x in boundaries):
        raise ValueError("history gap proofs cannot be relabeled as verified")
    passed=sum(x[2]=="PASSED" for x in attempts)
    failed=sum(x[2]=="FAILED" for x in attempts)
    incomplete=sum(x[2]=="STARTED" for x in attempts)
    target=plan[0] is None or plan[5]=="TARGET_REACHED"
    verdict=(
        "UNRESOLVED_INTERRUPTED_SOURCE" if incomplete else
        "FAILED_OR_QUARANTINED_SOURCE" if failed else
        "DURATION_TARGET_NOT_REACHED" if not target else
        "BOUNDED_SEGMENTS_ACCEPTED_WITH_UNKNOWN_HISTORY_GAPS"
        if passed else "NO_ACCEPTED_SESSIONS"
    )
    by_reason={}
    for x in attempts:
        if x[3]:
            by_reason[x[3]]=by_reason.get(x[3],0)+1
    result={
        "verdict":verdict,
        "attempts":len(attempts),
        "passed":passed,"failed":failed,"incomplete":incomplete,
        "open_outages":sum(x[1] is None for x in outages),
        "outage_windows":len(outages),
        "unknown_session_boundaries":len(boundaries),
        "completion_code":plan[5],
        "duration_target_s":plan[0],
        "attempts_budget":plan[1],
        "storage_ceiling_bytes":plan[2],
        "first_admission_utc":datetime.fromtimestamp(
            plan[3]/1000,tz=timezone.utc,
        ).isoformat(),
        "last_completion_utc":(
            datetime.fromtimestamp(plan[4]/1000,tz=timezone.utc).isoformat()
            if plan[4] is not None else None
        ),
        "total_originals_in_passed_attempts":sum(x[4] for x in attempts if x[2]=="PASSED"),
        "total_receiver_acks_in_passed_attempts":sum(x[5] for x in attempts if x[2]=="PASSED"),
        "max_queue_high_watermark":max((x[6] for x in attempts),default=0),
        "receiver_process_starts":sum(x[7] for x in attempts),
        "max_receiver_ack_latency_ms":round(
            max((x[8] for x in attempts),default=0),2,
        ),
        "failure_classes":by_reason,
        "failed_attempt_diagnostics":[
            {"iteration":x[0],"reason":x[3],
             "source_queue_high_watermark":x[6]}
            for x in attempts if x[2]=="FAILED"
        ],
        "operator_trial_not_institutional_phase1_certification":True,
        "gap_free_history_verified":False,
        "continuous_market_feed_certified":False,
        "default_verified_runtime_cutover_complete":False,
        "orders_submitted":0,
    }
    # Host sampling is observational, not source-proven or a formal CPU/RAM
    # acceptance certificate. Old trial folders legitimately lack this file.
    host=trial_dir/"host_resources.jsonl"
    if host.is_file() and not host.is_symlink():
        ram_values=[]
        try:
            with host.open("r",encoding="utf-8") as stream:
                for index,line in enumerate(stream):
                    if index>=50_000:
                        break
                    sample=json.loads(line)
                    value=sample.get("available_ram_gib")
                    if type(value) in (int,float) and 0<=value<=10000:
                        ram_values.append(value)
        except (OSError,ValueError,TypeError):
            ram_values=[]
        result["operator_host_ram_min_available_gib"]=(
            round(min(ram_values),3) if ram_values else None
        )
        result["operator_host_resource_samples"]=len(ram_values)
    else:
        result["operator_host_ram_min_available_gib"]=None
        result["operator_host_resource_samples"]=0
    return result


def _write_report(trial_dir:Path,summary:dict) -> None:
    # Reports do not overwrite earlier trial evidence; only derived
    # operator-readable documents are recreated from the immutable ledgers.
    (trial_dir/"report.json").write_text(
        json.dumps(summary,sort_keys=True,indent=2)+"\n",encoding="utf-8",
    )
    lines=[
        "# ECONITH Phase 1 — operator observation report","",
        "**Verdict:** "+summary["verdict"],"",
        f"- Accepted segments: {summary['passed']}/{summary['attempts']}",
        f"- Failed / interrupted: {summary['failed']} / {summary['incomplete']}",
        f"- Outage windows: {summary['outage_windows']} (open {summary['open_outages']})",
        f"- Unknown boundaries between sessions: {summary['unknown_session_boundaries']}",
        f"- Original rows from accepted segments: {summary['total_originals_in_passed_attempts']}",
        f"- Independent receiver ACKs: {summary['total_receiver_acks_in_passed_attempts']}",
        f"- Maximum source queue watermark (includes instrumented failures): {summary['max_queue_high_watermark']}",
        f"- Error codes: {summary['failure_classes']}",
        f"- Minimum sampled host RAM available: {summary.get('operator_host_ram_min_available_gib')} GiB",
        f"- Maximum observed receiver ACK latency: {summary['max_receiver_ack_latency_ms']} ms",
        f"- Operator time target completed: {summary['completion_code']}",
        "",
        "**Not certified:** gap-free market history, truly continuous public feed,",
        "default verified runtime cutover, multi-asset coverage or Phase 1 100%.",
        "Do not enable real exchange orders or allocate capital from this report.","",
        "Raw SQLite evidence is retained under `evidence/`. Any failed or",
        "interrupted source requires an operator review; do not erase it.","",
    ]
    (trial_dir/"REPORT.md").write_text("\n".join(lines),encoding="utf-8")



def _profile_launch_argv(*,profile:str,trial:Path,python_executable:str) -> list[str]:
    """Build a bounded public-research launch, without starting network I/O.

    Longer operator profiles deliberately use the same fresh-public-WSS
    isolation that passed the real 3x500 diagnostic. A duration target
    describes an observation window, NOT gap-free source history.
    """
    if profile not in _PROFILES:
        raise ValueError("invalid public trial profile")
    if not isinstance(trial,Path) or not trial.is_absolute():
        raise ValueError("operator trial root must be absolute")
    if type(python_executable) is not str or not python_executable:
        raise ValueError("Python executable required")
    config=_PROFILES[profile]
    argv=[
        python_executable,
        "-m","ci.phase1_forward_endurance_trial",
        "--allow-public-market-data","--persistent-receiver","--single-socket",
        "--output-dir",str(trial/"evidence"),
        "--attempts",str(config["attempts"]),
        "--max-frames",str(config["max_frames"]),
        "--timeout-s",str(config["timeout_s"]),
        "--max-storage-mb",str(config["max_storage_mb"]),
        "--ws-idle-timeout-s","15",
    ]
    if profile in ("diagnostic","hour","day"):
        # Sealed source segments do not form gap-free history. The old
        # retained WSS lease could accumulate stale unread frames between
        # attempts; use a new pinned TLS lease per segment in longer trials.
        argv.append("--fresh-socket-per-segment")
    if config["watch_hours"] is not None:
        argv+=["--watch-hours",str(config["watch_hours"])]
    return argv


def run_smoke(*,allow_public:bool,profile:str,
              evidence_base:Path|None=None,
              python_executable:str|None=None) -> int:
    if allow_public is not True:
        raise PermissionError("select explicit --allow-public-market-data")
    if profile not in _PROFILES:
        raise ValueError("invalid public trial profile")
    health=doctor(evidence_base=evidence_base)
    if any(value=="missing" for value in health["packages"].values()):
        raise RuntimeError("missing Python dependencies; run pinned venv setup")
    if health["ram_available_gib"] is not None and health["ram_available_gib"] < 0.25:
        raise RuntimeError("unsafe memory pressure; close apps or use a CPU VPS")
    # A 24h endurance run on a paging-starved Windows host is not a
    # reproducible market-data acceptance test. Quick remains opt-in,
    # but Hour/Day require a conservative local RAM headroom preflight.
    if (
        profile in ("diagnostic","hour","day")
        and health["ram_available_gib"] is not None
        and health["ram_available_gib"] < 1.5
    ):
        raise RuntimeError(
            "insufficient free RAM for Diagnostic/Hour/Day: at least 1.5 GiB "
            "available required; free memory or use a CPU VPS"
        )
    base=(evidence_base or _default_evidence_base()).expanduser().resolve()
    if base==_root() or _root() in base.parents:
        raise ValueError("evidence must be OUTSIDE git checkout")
    base.mkdir(parents=True,exist_ok=True)
    trial=base/(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                 +"_"+profile+"_"+secrets.token_hex(4))
    trial.mkdir(exist_ok=False)
    (trial/"doctor.json").write_text(
        json.dumps(health,indent=2)+"\n",encoding="utf-8",
    )
    argv=_profile_launch_argv(
        profile=profile,trial=trial,
        python_executable=python_executable or sys.executable,
    )
    (trial/"launch.json").write_text(
        json.dumps({
            "profile":profile,
            "argv_tokens_without_executable":argv[1:],
            "research_only":True,
            "public_network":True,
        },indent=2)+"\n",encoding="utf-8",
    )
    print("Evidence directory:",trial,flush=True)
    print("Research-only public Binance; no credentials/orders",flush=True)
    previous=None
    with (trial/"stdout.log").open("wb") as out, (trial/"stderr.log").open("wb") as err, (
        trial/"host_resources.jsonl"
    ).open("w",encoding="utf-8") as host_log:
        proc=subprocess.Popen(argv,cwd=_root(),stdout=out,stderr=err)
        try:
            while proc.poll() is None:
                total,available=_memory()
                host_log.write(json.dumps({
                    "observed_utc":datetime.now(timezone.utc).isoformat(),
                    "available_ram_gib":(
                        round(available/2**30,3) if available is not None else None
                    ),
                    "total_ram_gib":(
                        round(total/2**30,3) if total is not None else None
                    ),
                },sort_keys=True)+"\n")
                host_log.flush()
                time.sleep(3)
                ledger=trial/"evidence"/"forward_supervisor.sqlite"
                if ledger.is_file():
                    try:
                        with _readonly(ledger) as db:
                            counts=db.execute("""
                                SELECT COUNT(*),COALESCE(SUM(status='PASSED'),0),
                                       COALESCE(SUM(status='FAILED'),0)
                                FROM forward_watch_attempt
                            """).fetchone()
                        if counts!=previous:
                            print(f"Started {counts[0]} | passed {counts[1]} | failed {counts[2]}",
                                  flush=True)
                            previous=counts
                    except sqlite3.Error:
                        pass
            code=proc.returncode
        except KeyboardInterrupt:
            proc.terminate()
            try:
                proc.wait(timeout=12)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            code=130
            print("Operator stopped trial; evidence retained for audit",flush=True)
    try:
        summary=inspect_trial(trial)
        summary["child_process_exit_code"]=code
        _write_report(trial,summary)
        print("Report:",trial/"REPORT.md",flush=True)
        print("Verdict:",summary["verdict"],flush=True)
    except (sqlite3.Error,OSError,ValueError) as exc:
        print("Incomplete evidence; inspect stderr.log:",type(exc).__name__,
              file=sys.stderr)
    return code


def main(argv:list[str]|None=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_subparsers(dest="task",required=True)
    d=group.add_parser("doctor")
    d.add_argument("--output-base",type=Path)
    s=group.add_parser("smoke")
    s.add_argument("--allow-public-market-data",action="store_true")
    s.add_argument("--profile",choices=tuple(_PROFILES),default="quick")
    s.add_argument("--output-base",type=Path)
    report=group.add_parser("report")
    report.add_argument("--trial-dir",type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        if args.task=="doctor":
            print(json.dumps(doctor(evidence_base=args.output_base),
                             indent=2,ensure_ascii=False))
            return 0
        if args.task=="smoke":
            return run_smoke(
                allow_public=args.allow_public_market_data,profile=args.profile,
                evidence_base=args.output_base,
            )
        trial=args.trial_dir.expanduser().resolve()
        status=inspect_trial(trial)
        _write_report(trial,status)
        print(json.dumps(status,indent=2,ensure_ascii=False))
        return 0
    except (ValueError,PermissionError,RuntimeError,OSError) as exc:
        print("OPERATOR_LAB_REFUSED:",type(exc).__name__,str(exc),
              file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
