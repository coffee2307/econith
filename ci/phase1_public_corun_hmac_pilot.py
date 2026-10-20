"""Read-only bounded Binance public Spot + co-running research HMAC pilot CLI.

No trading mode, keys, position/broker calls, fallback to legacy, or 24h
institutional claim. Only the pinned Binance PUBLIC WSS + REST lease is allowed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from ci.phase1_public_spot_acceptance import measure_public_clock
from ci.phase1_rotating_corun_hmac import co_run_rotating_hmac
from infrastructure.spot_public_pinned_lease import open_pinned_public_spot_lease


def main(argv=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--allow-public-market-data",action="store_true")
    p.add_argument("--allow-hmac-research",action="store_true")
    p.add_argument("--acknowledge-not-24h-proof",action="store_true")
    p.add_argument("--duration-s",type=float,default=120.)
    p.add_argument("--frames-per-segment",type=int,default=450)
    p.add_argument("--receiver-batch-size",type=int,choices=range(1,9),default=1)
    p.add_argument("--reuse-receiver-connection",action="store_true")
    p.add_argument("--output-dir",type=Path,required=True)
    a=p.parse_args(argv)
    if not (a.allow_public_market_data
            and a.allow_hmac_research
            and a.acknowledge_not_24h_proof):
        p.error("read-only nontrading public source and downstream pilot need three explicit acknowledgements")
    try:
        r=asyncio.run(co_run_rotating_hmac(
            a.output_dir,duration_s=a.duration_s,
            max_frames_per_segment=a.frames_per_segment,
            lease_factory=open_pinned_public_spot_lease,
            clock_preflight=measure_public_clock,
            explicit_public_research_approval=True,
            max_handoff_age_s=25.,
            receiver_batch_size=a.receiver_batch_size,
            reuse_receiver_connection=a.reuse_receiver_connection,
        ))
    except (Exception,) as exc:
        print(json.dumps({"verdict":"REFUSED_OR_QUARANTINED",
                          "failure_class":type(exc).__name__,
                          "orders_submitted":0},sort_keys=True))
        return 2
    keep=("verdict","schema","public_read_only_market_source",
          "source_ws_frames","sealed_segment_count","reconstructed_projections",
          "independent_receiver_receipts","forward_checkpoints",
          "concurrent_segments_completed","segment_handoff_lag_max_ms",
          "segment_backlog_peak","receiver_batch_size","receiver_connection_reuse",
          "source_original_commit_age_ms_max",
          "source_queue_high_watermark","process_peak_rss_kib_linux",
          "process_cpu_user_s","process_cpu_system_s",
          "source_journal_disk_peak_bytes","orders_submitted")
    print(json.dumps({k:r[k] for k in keep if k in r},sort_keys=True,allow_nan=False))
    return 0 if (
        r.get("verdict")=="PASSED_BOUNDED_CO_RUNNING_HMAC_SEGMENTS_RESEARCH_ONLY"
        and r.get("public_read_only_market_source") is True
        and r.get("independent_local_audit_passed") is True
        and r.get("orders_submitted")==0
    ) else 2


if __name__=="__main__":
    sys.exit(main())
