"""Manually reconcile STAGED forward Spot research only; never source capture.

This is not a session acceptance or a signed history-completeness claim.
All output is read-only research observer receipts in SIMULATION. The process
MUST NOT be used while the source socket/producer is still running.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

from infrastructure.spot_forward_recovery import recover_staged_forward_spot


def parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--research-recover",action="store_true")
    p.add_argument("--output-dir",required=True,type=Path)
    p.add_argument("--session-id",required=True)
    p.add_argument("--max-envelopes",type=int,default=2000)
    p.add_argument("--reconstruct-missing",action="store_true")
    p.add_argument("--max-book-staleness-ms",type=int,default=2500)
    return p


def validate(args: argparse.Namespace) -> None:
    if args.research_recover is not True:
        raise PermissionError("explicit isolated research recovery opt-in required")
    if (
        not args.output_dir.is_absolute()
        or not args.output_dir.is_dir()
        or type(args.session_id) is not str
        or not args.session_id.startswith("rec_")
        or len(args.session_id) > 48
        or type(args.max_envelopes) is not int
        or not 1 <= args.max_envelopes <= 2000
        or type(args.max_book_staleness_ms) is not int
        or not 100 <= args.max_book_staleness_ms <= 30000
    ):
        raise ValueError("invalid bounded recovery scope")


def main(argv: list[str] | None = None) -> int:
    args=parser().parse_args(argv)
    try:
        validate(args)
        report=asyncio.run(recover_staged_forward_spot(
            root=args.output_dir,session_id=args.session_id,
            max_envelopes=args.max_envelopes,
            reconstruct_missing=args.reconstruct_missing,
            max_book_staleness_ms=args.max_book_staleness_ms,
        ))
    except Exception as exc:
        print("PHASE1_STAGED_RECOVERY_DENIED: "+type(exc).__name__,file=sys.stderr)
        return 2
    print(json.dumps(asdict(report),sort_keys=True))
    return 0 if report.outbox_derived_acked==report.staged_envelopes else 2


if __name__=="__main__":
    raise SystemExit(main())
