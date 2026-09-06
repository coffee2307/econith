"""ECONITH :: training.pull_training_data

One-shot entrypoint to prepare folders and (when you ask) pull long-horizon
market / macro / TradFi history into ``datasets/training_lake/``.

Safe by default
---------------
* ``python -m training.pull_training_data``           → only create folders
* ``python -m training.pull_training_data pull``      → download (you opt-in)
* ``python -m training.pull_training_data status``    → show what is already there

After pull (and after you extract the VPS ``datasets.tar`` into the same lake)::

    tar -xf F:\\econith-coin-data\\datasets.tar -C F:\\econith\\datasets\\training_lake

    python -m training.quant.feature_pipeline \\
        --raw-root datasets/training_lake --out-dir datasets/features
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from training.mvp_lake import (  # noqa: E402
    _DEFAULT_SYMBOLS,
    cmd_backfill_fred,
    cmd_backfill_futures_alt,
    cmd_backfill_klines,
    cmd_backfill_tradfi,
    cmd_backfill_world_bank,
    cmd_status,
)

LAKE_NAME = "training_lake"
DEFAULT_LAKE = _ROOT / "datasets" / LAKE_NAME

# Empty layout created on init (no network).
_LAKE_SUBDIRS = (
    "market/klines_hist",
    "market/crypto_hf",  # optional: copy/extract VPS HF here if you prefer
    "macro/fred",
    "macro/world_bank",
    "tradfi/history",
    "alt/futures",
)


def _write_how_to(lake: Path) -> None:
    text = f"""ECONITH training_lake
====================
Canonical raw lake for MVP train (klines + macro + TradFi + VPS HF + futures alt).

Init folders only (no download):
  python -m training.pull_training_data

Pull history when YOU want (needs network; FRED needs FRED_API_KEY):
  python -m training.pull_training_data pull
  python -m training.pull_training_data pull --only klines
  python -m training.pull_training_data pull --only fred,tradfi
  python -m training.pull_training_data pull --only futures_alt

Extract VPS tick/depth tape into THIS lake (do not re-download 5y ticks):
  tar -xf F:\\econith-coin-data\\datasets.tar -C {lake}

Klines (swing) track:
  python -m training.quant.feature_pipeline --raw-root {lake.as_posix()} --out-dir datasets/features --market-subdirs klines_hist
  python -m training.quant.label_symbol --input datasets/features --horizon-preset swing_1h
  python -m training.train_ppo --dataset-profile klines --agent trend --timesteps 100000

HF (scalp) track:
  python -m training.mvp_lake backfill-futures-alt --raw-root {lake.as_posix()} --symbols BTCUSDT,ETHUSDT
  python -m training.hf_features --symbols BTCUSDT,ETHUSDT --days 7 --out-dir datasets/features_hf
  python -m training.quant.label_symbol --input datasets/features_hf --output datasets/processed/quant_labeled_hf.parquet --horizon-preset scalp
  python -m training.train_ppo --dataset-profile hf --agent scalper --timesteps 100000

Created: {datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")}
"""
    (lake / "HOW_TO_PULL.txt").write_text(text, encoding="utf-8")


def cmd_init(lake: Path) -> int:
    lake.mkdir(parents=True, exist_ok=True)
    for rel in _LAKE_SUBDIRS:
        (lake / rel).mkdir(parents=True, exist_ok=True)
        keep = lake / rel / ".gitkeep"
        if not keep.exists():
            keep.write_text("", encoding="utf-8")
    _write_how_to(lake)
    print(f"OK — folders ready under: {lake.resolve()}")
    print("No data downloaded. When ready, run:")
    print("  python -m training.pull_training_data pull")
    return 0


def cmd_pull(
    lake: Path,
    *,
    only: set[str],
    symbols: list[str],
    intervals: list[str],
    kline_start: str,
    kline_end: str,
    fred_start: str,
    tradfi_start: str,
    fred_api_key: str,
    wb_start_year: int,
) -> int:
    cmd_init(lake)
    wanted = only or {"klines", "fred", "tradfi", "worldbank"}
    rc = 0

    if "klines" in wanted:
        print("\n=== pull Binance klines (1h/1d history, NOT tick) ===")
        rc |= cmd_backfill_klines(
            symbols=symbols,
            intervals=intervals,
            start=kline_start,
            end=kline_end,
            raw_root=lake,
            base_url="https://api.binance.com",
        )

    if "fred" in wanted:
        print("\n=== pull FRED macro history ===")
        try:
            rc |= cmd_backfill_fred(
                start=fred_start, raw_root=lake, api_key=fred_api_key
            )
        except SystemExit as exc:
            print(f"FRED skipped/failed: {exc}")
            rc |= 1

    if "worldbank" in wanted or "wb" in wanted:
        print("\n=== pull World Bank structural macro ===")
        rc |= cmd_backfill_world_bank(start_year=wb_start_year, raw_root=lake)

    if "tradfi" in wanted:
        print("\n=== pull TradFi (DXY/gold/oil/SPX/FX) ===")
        rc |= cmd_backfill_tradfi(
            start=tradfi_start,
            end=kline_end,
            raw_root=lake,
        )

    if "futures_alt" in wanted or "futures-alt" in wanted or "alt" in wanted:
        print("\n=== pull Binance futures alt (funding + OI) ===")
        rc |= cmd_backfill_futures_alt(
            symbols=symbols,
            start=max(kline_start, "2024-01-01"),
            end=kline_end,
            raw_root=lake,
        )

    print(f"\nDone. Lake root: {lake.resolve()}")
    print("Next: extract VPS tar into the same lake (if not yet), then feature_pipeline / hf_features.")
    return rc


def build_parser() -> argparse.ArgumentParser:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    p = argparse.ArgumentParser(
        prog="pull_training_data",
        description="Create training_lake folders; pull only when you run 'pull'.",
    )
    p.add_argument(
        "--lake",
        default=str(DEFAULT_LAKE),
        help=f"lake root (default: datasets/{LAKE_NAME})",
    )
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("init", help="create folders only (default if no subcommand)")
    sub.add_parser("status", help="show parquet counts under the lake")

    pull = sub.add_parser("pull", help="DOWNLOAD history into the lake (opt-in)")
    pull.add_argument(
        "--only",
        default="",
        help="comma subset: klines,fred,worldbank,tradfi,futures_alt (default = all except futures_alt)",
    )
    pull.add_argument(
        "--symbols",
        default=",".join(_DEFAULT_SYMBOLS),
        help="Binance pairs for kline backfill",
    )
    pull.add_argument("--intervals", default="15m,1h,4h,1d")
    pull.add_argument("--kline-start", default="2020-01-01")
    pull.add_argument("--kline-end", default=today)
    pull.add_argument("--fred-start", default="1990-01-01")
    pull.add_argument("--tradfi-start", default="2018-01-01")
    pull.add_argument("--wb-start-year", type=int, default=1990)
    pull.add_argument(
        "--fred-api-key",
        default=os.getenv("FRED_API_KEY", ""),
        help="or set env FRED_API_KEY",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    # Allow bare `python -m training.pull_training_data` => init
    # Allow `status --lake X` by moving --lake before the subcommand.
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv and argv[0].startswith("-") and argv[0] not in ("-h", "--help")):
        if not argv or argv[0].startswith("-"):
            argv = ["init", *argv]
    # Normalize: `status --lake PATH` -> `--lake PATH status`
    if argv and argv[0] in ("init", "status", "pull") and "--lake" in argv[1:]:
        i = argv.index("--lake", 1)
        if i + 1 < len(argv):
            lake_pair = argv[i : i + 2]
            argv = lake_pair + argv[:i] + argv[i + 2 :]

    args = build_parser().parse_args(argv)
    lake = Path(args.lake)

    if args.cmd in (None, "init"):
        return cmd_init(lake)
    if args.cmd == "status":
        return cmd_status(lake)
    if args.cmd == "pull":
        only = {s.strip().lower() for s in args.only.split(",") if s.strip()}
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        intervals = [s.strip() for s in args.intervals.split(",") if s.strip()]
        return cmd_pull(
            lake,
            only=only,
            symbols=symbols,
            intervals=intervals,
            kline_start=args.kline_start,
            kline_end=args.kline_end,
            fred_start=args.fred_start,
            tradfi_start=args.tradfi_start,
            fred_api_key=args.fred_api_key,
            wb_start_year=int(args.wb_start_year),
        )
    raise SystemExit(f"unknown command {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
