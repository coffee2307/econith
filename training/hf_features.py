"""ECONITH :: training.hf_features — build HF feature store from VPS tick lake.

Reads raw tick desks (``datasets/training_lake/market/crypto_*``) through
``training/quant/microstructure.build_hf_features`` and writes compact Parquet
frames into ``datasets/features_hf/``.

Writes both ``{SYM}_hf.parquet`` (human-readable) and ``features_{SYM}.parquet``
(FeatureLoader contract used by ``label_symbol``). Batches are **concatenated
per symbol** before the final write so a multi-batch run does not keep only the
last batch.

Default is a **subset** (symbols × days) so it fits on a weak PC. Run full only
on a host with enough RAM/disk (H200 station or VPS).

Examples
--------
    # subset: BTC/ETH, 2 days
    python -m training.hf_features --symbols BTCUSDT,ETHUSDT --days 2

    # write into datasets/features_hf (default)
    python -m training.hf_features --symbols BTCUSDT --days 1 --out-dir datasets/features_hf
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
)
logger = logging.getLogger("econith.training.hf_features")

DEFAULT_LAKE = _ROOT / "datasets" / "training_lake" / "market"
DEFAULT_ALT = _ROOT / "datasets" / "training_lake" / "alt" / "futures"
DEFAULT_OUT = _ROOT / "datasets" / "features_hf"

# Binance USDT-M perps fund every 8h at 00:00/08:00/16:00 UTC.
_FUNDING_PERIOD_MS = 8 * 3_600_000


def _scan_shards(lake: Path, symbols: list[str], days: int) -> list[Path]:
    """Collect parquet shards for symbols, restricted to the most recent N days."""
    shards: list[Path] = []
    sym_upper = {s.upper() for s in symbols}
    for desk in ("crypto_majors", "crypto_high_beta", "crypto_meme"):
        base = lake / desk
        if not base.exists():
            continue
        for sym_dir in sorted(base.iterdir()):
            if sym_dir.name.upper() not in sym_upper:
                continue
            day_dirs = sorted(
                [d for d in sym_dir.iterdir() if d.is_dir() and d.name.startswith("20")]
            )
            for d in day_dirs[-days:] if days > 0 else day_dirs:
                shards.extend(sorted(d.glob("*.parquet")))
    logger.info("scanned %d shards (symbols=%s, days=%d)", len(shards), symbols, days)
    return shards


def _read_shard(path: Path) -> "pl.DataFrame | None":
    import polars as pl

    try:
        return pl.read_parquet(path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("skipping unreadable shard %s (%s)", path, exc)
        return None


def _concat(frames: list["pl.DataFrame"]) -> "pl.DataFrame":
    import polars as pl

    if not frames:
        return pl.DataFrame()
    if len(frames) == 1:
        return frames[0]
    return pl.concat(frames, how="vertical")


def _map_ppo_contract(hf: "pl.DataFrame") -> "pl.DataFrame":
    """Alias microstructure cols onto the live PPO_FEATURE_COLS contract."""
    import polars as pl

    extras: list[pl.Expr] = [
        pl.col("indicator_obi").alias("obi"),
        pl.col("indicator_trade_imbalance").alias("volume_delta"),
        pl.col("trade_volume").alias("trade_count"),
        # Seconds until next 8h funding boundary (UTC). Neutral when ts missing.
        (
            (
                _FUNDING_PERIOD_MS
                - (pl.col("ts_ms") % _FUNDING_PERIOD_MS)
            ).cast(pl.Float64)
            / 1000.0
        ).alias("time_to_funding_s"),
    ]
    # Prefer real channel columns when present; else fill neutrals.
    if "buy_volume" not in hf.columns:
        extras.append(pl.lit(0.0).alias("buy_volume"))
    if "sell_volume" not in hf.columns:
        extras.append(pl.lit(0.0).alias("sell_volume"))
    if "funding_rate" not in hf.columns:
        extras.append(pl.lit(0.0).alias("funding_rate"))
    if "open_interest" not in hf.columns:
        extras.append(pl.lit(0.0).alias("open_interest"))
    if "oi_change_pct" not in hf.columns:
        extras.append(pl.lit(0.0).alias("oi_change_pct"))
    if "liquidation_notional" not in hf.columns:
        extras.append(pl.lit(0.0).alias("liquidation_notional"))
    return hf.with_columns(extras)


def _load_alt_frames(alt_root: Path, symbols: list[str]) -> "pl.DataFrame | None":
    """Load funding/OI/liquidation history and coalesce to one row per (symbol, ts)."""
    import polars as pl

    if not alt_root.exists():
        return None
    paths = sorted(alt_root.rglob("*.parquet"))
    if not paths:
        return None
    frames: list[pl.DataFrame] = []
    want = {s.upper() for s in symbols}
    for path in paths:
        try:
            df = pl.read_parquet(path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("skipping alt shard %s (%s)", path, exc)
            continue
        if "symbol" not in df.columns or "ts_ms" not in df.columns:
            continue
        df = df.with_columns(pl.col("symbol").cast(pl.Utf8).str.to_uppercase())
        df = df.filter(pl.col("symbol").is_in(list(want)))
        if df.height:
            frames.append(df)
    if not frames:
        return None
    alt = _concat(frames)
    aggs = []
    for c in ("funding_rate", "open_interest", "oi_change_pct", "liquidation_notional"):
        if c in alt.columns:
            aggs.append(pl.col(c).drop_nulls().last().fill_null(0.0).alias(c))
        else:
            aggs.append(pl.lit(0.0).alias(c))
    alt = alt.group_by(["symbol", "ts_ms"]).agg(aggs).sort(["symbol", "ts_ms"])
    # Treat exact zeros as missing so sparse funding can forward-fill across OI ticks.
    fill_cols = ["funding_rate", "open_interest", "oi_change_pct", "liquidation_notional"]
    alt = alt.with_columns(
        [
            pl.when(pl.col(c) == 0.0).then(None).otherwise(pl.col(c)).alias(c)
            for c in fill_cols
        ]
    )
    alt = alt.with_columns(
        [pl.col(c).forward_fill().over("symbol").fill_null(0.0).alias(c) for c in fill_cols]
    )
    logger.info("loaded %d alt futures rows from %s", alt.height, alt_root)
    return alt


def _merge_alt(hf: "pl.DataFrame", alt: "pl.DataFrame | None") -> "pl.DataFrame":
    """As-of merge funding / OI / liquidation onto the HF feature clock."""
    import polars as pl

    if alt is None or alt.height == 0:
        return hf

    side = alt.select(
        [
            "symbol",
            "ts_ms",
            "funding_rate",
            "open_interest",
            "oi_change_pct",
            "liquidation_notional",
        ]
    ).sort(["symbol", "ts_ms"])
    overwrite = ["funding_rate", "open_interest", "oi_change_pct", "liquidation_notional"]
    base = hf.drop([c for c in overwrite if c in hf.columns])
    return base.join_asof(side, on="ts_ms", by="symbol", strategy="backward")


def run(
    *,
    lake_root: Path,
    out_dir: Path,
    symbols: list[str],
    days: int,
    levels: int,
    batch_shards: int,
    alt_root: Path | None = None,
) -> dict:
    import polars as pl

    from training.quant.microstructure import build_hf_features

    shards = _scan_shards(lake_root, symbols, days)
    if not shards:
        logger.warning("no shards found under %s", lake_root)
        return {"written": 0, "symbols": {}}

    out_dir.mkdir(parents=True, exist_ok=True)
    alt = _load_alt_frames(alt_root or DEFAULT_ALT, symbols)
    # Accumulate all batch frames per symbol, then write once.
    by_symbol: dict[str, list[pl.DataFrame]] = {s: [] for s in symbols}
    frame_count = 0

    for batch_start in range(0, len(shards), batch_shards):
        batch = shards[batch_start : batch_start + batch_shards]
        logger.info(
            "processing shard batch %d-%d / %d",
            batch_start + 1,
            batch_start + len(batch),
            len(shards),
        )
        frames = [f for f in (_read_shard(p) for p in batch) if f is not None]
        if not frames:
            continue
        market = _concat(frames)
        if market.height == 0:
            continue

        hf = build_hf_features(market, levels=levels)
        if hf.height == 0:
            logger.warning(
                "build_hf_features returned 0 rows for batch starting %s", batch[0].name
            )
            continue

        hf = _map_ppo_contract(hf)
        hf = _merge_alt(hf, alt)

        for sym in hf.get_column("symbol").unique().to_list():
            key = str(sym).upper()
            sub = hf.filter(pl.col("symbol") == sym)
            by_symbol.setdefault(key, []).append(sub)
            frame_count += sub.height

    written: list[str] = []
    stats: dict[str, int] = {}
    for sym, parts in by_symbol.items():
        if not parts:
            continue
        combined = _concat(parts).unique(subset=["symbol", "ts_ms"], keep="last").sort(
            ["symbol", "ts_ms"]
        )
        # FeatureLoader contract: features_{SYM}.parquet
        loader_path = out_dir / f"features_{sym}.parquet"
        human_path = out_dir / f"{sym}_hf.parquet"
        combined.write_parquet(loader_path, compression="snappy")
        combined.write_parquet(human_path, compression="snappy")
        written.extend([str(loader_path), str(human_path)])
        stats[sym] = combined.height
        logger.info("wrote %s -> %d rows (%s)", sym, combined.height, loader_path.name)

    logger.info("hf_features done: %d rows across batches, %d symbols", frame_count, len(stats))
    return {"written": len(written), "rows": frame_count, "symbols": stats}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hf_features", description="Build HF features from VPS tick lake"
    )
    p.add_argument("--lake-root", default=str(DEFAULT_LAKE), help="raw market lake root")
    p.add_argument("--out-dir", default=str(DEFAULT_OUT), help="output feature dir")
    p.add_argument("--alt-root", default=str(DEFAULT_ALT), help="optional funding/OI lake")
    p.add_argument("--symbols", default="BTCUSDT,ETHUSDT", help="comma-separated symbols")
    p.add_argument(
        "--days", type=int, default=2, help="most recent N day-partitions per symbol (0=all)"
    )
    p.add_argument("--levels", type=int, default=5, help="order book depth levels")
    p.add_argument("--batch-shards", type=int, default=500, help="shards per read batch")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    summary = run(
        lake_root=Path(args.lake_root),
        out_dir=Path(args.out_dir),
        symbols=symbols,
        days=args.days,
        levels=args.levels,
        batch_shards=args.batch_shards,
        alt_root=Path(args.alt_root),
    )
    if summary["written"] == 0:
        logger.error("no feature files written")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
