"""Time-bounded ONE-socket Spot research receipt, never live trading or certified venue coverage.

Unlike frame-segmented watch, one admitted source session remains attached to the
same pinned TLS WSS until the *wall-clock observation target* is reached.
A frame-budget exhaustion, disconnect, stale source, overflow, blocked SQLite,
missing synchronized REST bridge or early EOF is quarantined, never PASS.
This is bounded pilot code; a true 24h service requires durable rotation, restart
fencing, operator-led retention and an uninterrupted receiver-to-ledger proof.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import secrets
import sys
import time

from core.mode import QuantMode
from ci.phase1_public_spot_acceptance import measure_public_clock
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
from infrastructure.spot_public_capture_adapter import (
    OptInSpotIngressAdapter, SpotTransportError, _PUBLIC_WS, _PUBLIC_REST,
)
from infrastructure.spot_public_pinned_lease import open_pinned_public_spot_lease
from infrastructure.spot_forward_owner import exclusive_forward_owner


class DurationStream:
    """Yield EVERY delivered source frame until a monotonic deadline; never skip."""
    def __init__(self, source, *, duration_s: float, idle_s: float = 30.0,
                 monotonic=time.monotonic,
                 max_terminal_silence_s: float = 5.0):
        if not (math.isfinite(duration_s) and duration_s > 0):
            raise ValueError("duration must be positive and finite")
        if (type(max_terminal_silence_s) not in (int,float)
            or not math.isfinite(max_terminal_silence_s)
            or not 0.001 <= max_terminal_silence_s <= 30):
            raise ValueError("invalid terminal source liveness threshold")
        self.source=source
        self.max_terminal_silence_s=max_terminal_silence_s
        self.last_frame_monotonic_s=None
        self.duration_s=duration_s
        self.idle_s=idle_s
        self.monotonic=monotonic
        self.target_reached=False
        self.early_eof=False
        self.read_frames=0
        self.start_monotonic_s=None
        self.end_monotonic_s=None

    def _finish_at_deadline(self) -> None:
        if (self.last_frame_monotonic_s is None
            or self.monotonic() - self.last_frame_monotonic_s > self.max_terminal_silence_s):
            raise SpotTransportError("no sufficiently recent source frame at observation deadline",
                                     reason="WS_SILENT_AT_DEADLINE")
        self.target_reached=True

    @property
    def terminal_silence_s(self):
        if self.last_frame_monotonic_s is None or self.end_monotonic_s is None:
            return None
        return round(max(0.0,self.end_monotonic_s-self.last_frame_monotonic_s),3)

    async def __aiter__(self):
        it=aiter(self.source)
        start=self.monotonic()
        self.start_monotonic_s=start
        deadline=start+self.duration_s
        try:
            while True:
                remaining=deadline-self.monotonic()
                if remaining<=0:
                    self._finish_at_deadline()
                    return
                try:
                    raw=await asyncio.wait_for(anext(it),timeout=min(self.idle_s,remaining))
                except asyncio.TimeoutError as exc:
                    if self.monotonic()>=deadline:
                        self._finish_at_deadline()
                        return
                    raise SpotTransportError("idle before duration completed",reason="WS_IDLE_TIMEOUT") from exc
                except StopAsyncIteration as exc:
                    self.early_eof=True
                    raise SpotTransportError("public source EOF before duration",reason="WS_EARLY_EOF") from exc
                self.read_frames+=1
                self.last_frame_monotonic_s=self.monotonic()
                yield raw
        finally:
            self.end_monotonic_s=self.monotonic()

    @property
    def elapsed_s(self) -> float:
        if self.start_monotonic_s is None or self.end_monotonic_s is None:
            return 0.0
        return round(self.end_monotonic_s-self.start_monotonic_s,3)


def _fresh_private_root(root: Path) -> None:
    if (not root.is_absolute() or root.exists() or root!=root.resolve()
        or root.parent.is_symlink()):
        raise ValueError("fresh absolute non-aliased private evidence path required")
    root.mkdir(parents=False,mode=0o700,exist_ok=False)


async def _run_trial_owned(root: Path, *, duration_s: float = 90.0,
                    allow_public_market_data: bool = False,
                    lease_factory=open_pinned_public_spot_lease,
                    clock_preflight=measure_public_clock,
                    _fixture: bool = False, adapter_clock_ms=None,
                    stream_monotonic=time.monotonic) -> dict:
    if allow_public_market_data is not True:
        raise PermissionError("explicit research-only Binance public opt-in required")
    if type(duration_s) not in (int,float) or not math.isfinite(duration_s):
        raise ValueError("finite positive duration required")
    if not ((0.1<=duration_s<=180) if _fixture else (60<=duration_s<=180)):
        raise ValueError("production research target must be 60..180s")
    # Caller has already created a fresh private evidence scope and acquired
    # the exclusive OS owner marker before any clock/network source IO.
    report={"schema":"econith-phase1-single-socket-duration-v1",
            "verdict":"STARTED_NOT_CERTIFIED","mode":"SIMULATION",
            "source":"binance_public_data_only",
            "target_duration_s":duration_s,
            "queue_capacity":128,"freshness_cap_ms":5000,
            "frame_cap":4998,"single_socket_session_started":False,
            "continuous_source_connection_observed":False,
            "exchange_history_completeness_certified":False,
            "source_event_losslessness_certified":False,
            "history_continuity_verified":False,
            "default_runtime_cutover":False,
            "hmac_forward_receiver_tested":False,
            "institutional_phase1_complete":False,
            "orders_submitted":0,
            "exclusive_os_owner_fence_held":True,
            "max_terminal_source_silence_s":5.0}
    journal=None
    stream=None
    started=None
    try:
        # Preflight does not authenticate venue event payload signatures.
        clock=await clock_preflight(max_abs_offset_ms=3000,max_rtt_ms=2000)
        report["clock_rtt_ms"]=clock.round_trip_ms
        report["clock_offset_ms"]=clock.measured_offset_ms
        journal=OfflineSpotCaptureJournal(
            root/"original.sqlite",mode_provider=lambda:QuantMode.SIMULATION,
            max_body_bytes=262144,max_frames_per_session=5000,
            max_age_ms=5000,forward_capture_audit=True,reuse_connection=True,
        )
        adapter=OptInSpotIngressAdapter(
            journal,symbol="BTCUSDT",queue_capacity=128,
            cooperative_reader_yield=True,clock_ms=adapter_clock_ms,
        )
        session_id="sds_"+secrets.token_hex(8)
        report["session_id"]=session_id
        async with lease_factory(allow_public_market_data=True) as lease:
            # Only the actual pinned factory is trusted as authenticated TLS;
            # injected test fixture is never a public transport attestation.
            if lease_factory is open_pinned_public_spot_lease:
                lease.ensure_open()
            report["single_socket_session_started"]=True
            stream=DurationStream(lease.stream,duration_s=duration_s,
                                  monotonic=stream_monotonic)
            started=time.monotonic()
            async def verify_admission(record):
                now_ms=(adapter_clock_ms() if adapter_clock_ms else time.time_ns()//1_000_000)
                lag_ms=now_ms-record.received_at_ms
                if lag_ms<0 or lag_ms>5000:
                    raise SpotTransportError(
                        "original SQLite COMMIT admission-age out of budget",
                        reason="SOURCE_COMMIT_ADMISSION_AGE",
                    )
            result=await adapter.capture_open_stream(
                stream,lease.snapshot,
                session_id=session_id,source="binance_public_simulation",
                ws_endpoint=_PUBLIC_WS,rest_endpoint=_PUBLIC_REST,
                max_frames=4998,timeout_s=min(600.0,duration_s+35.0),
                idle_timeout_s=30.0,require_frame_limit=False,
                on_persisted=verify_admission,
            )
        report.update({
            "duration_stream_elapsed_s":stream.elapsed_s,
            "terminal_source_silence_s":stream.terminal_silence_s,
            "websocket_frames":result.websocket_frames,
            "snapshot_captures":result.snapshot_captures,
            "max_fifo_depth":result.queue_high_watermark,
            "depth_status":result.depth_status,
            "last_update_id":result.last_update_id,
        })
        if (not stream.target_reached or stream.early_eof
            or stream.elapsed_s < duration_s-0.05
            or result.websocket_frames<2 or result.snapshot_captures!=1
            or result.depth_status!="SYNCED"
            or result.queue_high_watermark>=128):
            raise SpotTransportError(
                "bounded connected duration/source proof incomplete",
                reason="DURATION_NOT_CERTIFIED",
            )
        report["continuous_source_connection_observed"]=True
        report["verdict"]="PASSED_BOUNDED_SINGLE_SOCKET_RESEARCH_ONLY"
    except Exception as exc:
        report["verdict"]="FAILED_OR_QUARANTINED"
        report["failure_class"]=type(exc).__name__
        if isinstance(exc,SpotTransportError):
            report["failure_code"]=exc.reason
    finally:
        if stream is not None:
            report["duration_stream_elapsed_s"]=stream.elapsed_s
            report["terminal_source_silence_s"]=stream.terminal_silence_s
            report["duration_target_reached"]=stream.target_reached
            report["read_frames"]=stream.read_frames
        report["wall_elapsed_s"]=round(time.monotonic()-started,3) if started is not None else 0
        if journal is not None:
            try: journal.close()
            except Exception:
                report["verdict"]="FAILED_OR_QUARANTINED"
                report["failure_code"]="JOURNAL_CLOSE_FAILED"
        path=root/"aggregate_report.json"
        with path.open("x",encoding="utf-8") as f:
            json.dump(report,f,indent=2,sort_keys=True,allow_nan=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
    return report


async def run_trial(root: Path, *, duration_s: float = 90.0,
                    allow_public_market_data: bool = False,
                    lease_factory=open_pinned_public_spot_lease,
                    clock_preflight=measure_public_clock,
                    _fixture: bool = False, adapter_clock_ms=None,
                    stream_monotonic=time.monotonic) -> dict:
    """Enforce one unstealable OS owner marker across capture and final audit record."""
    if allow_public_market_data is not True:
        raise PermissionError("explicit research-only Binance public opt-in required")
    if type(duration_s) not in (int,float) or not math.isfinite(duration_s):
        raise ValueError("finite positive duration required")
    if not ((0.1<=duration_s<=180) if _fixture else (60<=duration_s<=180)):
        raise ValueError("production research target must be 60..180s")
    _fresh_private_root(root)
    # A process hard-kill skips Python finally, leaving a persistent orphan
    # marker. Recovery must not auto-steal it, even after lease timeouts.
    with exclusive_forward_owner(root,role="CAPTURE"):
        return await _run_trial_owned(
            root,duration_s=duration_s,allow_public_market_data=True,
            lease_factory=lease_factory,clock_preflight=clock_preflight,
            _fixture=_fixture,adapter_clock_ms=adapter_clock_ms,
            stream_monotonic=stream_monotonic,
        )


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--allow-public-market-data",action="store_true")
    p.add_argument("--acknowledge-not-24h-proof",action="store_true")
    p.add_argument("--duration-s",type=float,default=90.0)
    p.add_argument("--output-dir",type=Path,required=True)
    args=p.parse_args(argv)
    if not (args.allow_public_market_data and args.acknowledge_not_24h_proof):
        p.error("explicit bounded read-only research consent required")
    try:
        report=asyncio.run(run_trial(args.output_dir,duration_s=args.duration_s,
                                    allow_public_market_data=True))
    except (ValueError,OSError,PermissionError) as exc:
        print(json.dumps({"verdict":"REFUSED","failure_class":type(exc).__name__,
                          "orders_submitted":0}))
        return 2
    print(json.dumps({k:v for k,v in report.items() if k!="session_id"},sort_keys=True))
    return 0 if report["verdict"]=="PASSED_BOUNDED_SINGLE_SOCKET_RESEARCH_ONLY" else 2


if __name__=="__main__":
    sys.exit(main())
