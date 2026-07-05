"""ECONITH :: training.quant.feature_pipeline  (PHASE A.5 -- the multi-frequency glue)

The deterministic timeline-synchronization layer that bridges the raw data lake
(``datasets/raw/``) and the labeling / backtesting tier. It is intentionally the
*only* place where three very different clocks are reconciled:

  * MARKET  -- high-frequency crypto order-flow (ticks / orderbook / mark price)
  * MACRO   -- low-frequency point-in-time snapshots (FRED / World Bank / ...)
  * TRADFI  -- session-based references (DXY, gold, S&P 500, crude, US10Y)

Pipeline stages
---------------
1. INGEST      -- recursively scan ``datasets/raw/{market,macro,tradfi}`` for
                  Parquet (and JSONL fallback) shards; empty tiers are skipped
                  gracefully.
2. HF FEATURES -- per ``symbol`` + ``ts_ms``, derive immediate microstructure
                  features:
                    * ``indicator_obi``           = bid_sz / (bid_sz + ask_sz + 1e-8)
                    * ``indicator_volume_delta``  = per-tick change in traded volume
3. ALIGN       -- ``join_asof`` (backward) each HF market tick to the most recent
                  known macro + tradfi snapshot: no look-ahead can ever leak.
4. COMPLETE    -- forward-fill then backward-fill the low-frequency columns so a
                  weekend/holiday market closure or a cross-market cadence gap
                  never propagates a null into the training set.
5. EXPORT      -- write one enriched Parquet per symbol into ``datasets/features/``.

Zero-ML boundary: this module uses only ``polars`` (with a stdlib JSON assist for
schema discovery). It imports nothing from ``ai/``, ``training`` model code, or
``torch`` -- it is pure, high-performance data plumbing.

Run:
    python -m training.quant.feature_pipeline --raw-root datasets/raw --out-dir datasets/features
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
)
logger = logging.getLogger("econith.training.quant.feature_pipeline")

try:  # Polars is the mandated high-performance engine for this tier.
    import polars as pl
except ImportError:  # pragma: no cover - surfaced as a clean actionable error
    pl = None  # type: ignore[assignment]

# Canonical raw-lake schema written by ``collectors.shared.persistence.SnapshotWriter``.
_CANON_SCHEMA: tuple[tuple[str, "object"], ...] = ()  # populated after polars import
_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")

# Neutral defaults so a degenerate book never emits a NaN into training data.
_NEUTRAL_OBI = 0.5


def _require_polars() -> None:
    if pl is None:
        raise SystemExit(
            "polars is required for the feature pipeline. Install it with:\n"
            "    pip install polars\n"
        )


def _canon_schema() -> tuple[tuple[str, object], ...]:
    """The canonical raw-lake columns + dtypes (built lazily post-import)."""
    return (
        ("ts_ms", pl.Int64),
        ("asset_class", pl.Utf8),
        ("symbol", pl.Utf8),
        ("channel", pl.Utf8),
        ("source", pl.Utf8),
        ("value", pl.Float64),
        ("payload", pl.Utf8),
    )


# ---------------------------------------------------------------------------
# Stage 1 -- ingest
# ---------------------------------------------------------------------------
def _normalize(df: "pl.DataFrame") -> "pl.DataFrame":
    """Coerce any raw shard to the canonical schema (missing cols -> typed null)."""
    projections = []
    for name, dtype in _canon_schema():
        if name in df.columns:
            projections.append(pl.col(name).cast(dtype, strict=False).alias(name))
        else:
            projections.append(pl.lit(None).cast(dtype).alias(name))
    return df.select(projections)


def _scan_asset_class(raw_root: Path, asset_class: str) -> Optional["pl.DataFrame"]:
    """Recursively read + normalize every shard for one asset class, or None."""
    base = raw_root / asset_class
    if not base.exists() or not base.is_dir():
        logger.info("raw tier '%s' absent under %s -- skipping", asset_class, raw_root)
        return None

    shards = sorted(base.rglob("*.parquet")) + sorted(base.rglob("*.jsonl"))
    frames: list["pl.DataFrame"] = []
    for shard in shards:
        try:
            df = pl.read_parquet(shard) if shard.suffix == ".parquet" else pl.read_ndjson(shard)
        except Exception as exc:  # noqa: BLE001 - a corrupt shard must not abort the run
            logger.warning("skipping unreadable shard %s (%s)", shard, exc)
            continue
        if df.height == 0:
            continue
        frames.append(_normalize(df))

    if not frames:
        logger.info("raw tier '%s' has no readable rows -- skipping", asset_class)
        return None
    combined = pl.concat(frames, how="vertical")
    logger.info("ingested %s: %d rows from %d shards", asset_class, combined.height, len(frames))
    return combined


def _discover_payload_keys(df: "pl.DataFrame", sample: int = 1000) -> list[str]:
    """Union of numeric, identifier-safe keys found across sampled JSON payloads."""
    if "payload" not in df.columns:
        return []
    keys: set[str] = set()
    for raw in df.get_column("payload").head(sample).to_list():
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        for key, val in obj.items():
            if (
                isinstance(val, (int, float))
                and not isinstance(val, bool)
                and _IDENT_RE.match(str(key))
            ):
                keys.add(str(key))
    return sorted(keys)


def _payload_float(paths: list[str]) -> "pl.Expr":
    """Coalesce the first finite float found across candidate JSON paths."""
    candidates = [
        pl.col("payload").str.json_path_match(path).cast(pl.Float64, strict=False)
        for path in paths
    ]
    return pl.coalesce(candidates)


def _payload_float_regex(patterns: list[str]) -> "pl.Expr":
    """Coalesce the first regex-captured float found in the JSON payload text."""
    candidates = [
        pl.col("payload").str.extract(pattern, group_index=1).cast(pl.Float64, strict=False)
        for pattern in patterns
    ]
    return pl.coalesce(candidates)


# ---------------------------------------------------------------------------
# Stage 2 -- high-frequency market features
# ---------------------------------------------------------------------------
def _build_market_features(market: "pl.DataFrame") -> "pl.DataFrame":
    """Aggregate raw market frames per (symbol, ts_ms) and derive HF indicators."""
    market = market.filter(pl.col("symbol").is_not_null() & (pl.col("symbol") != ""))

    # Local collector test runs may carry only depth updates. In that case there
    # is no scalar ``value`` or trade price field, so we derive the fair price
    # from the top-of-book mid: (best_bid + best_ask) / 2.
    best_bid_px = pl.coalesce(
        [
            _payload_float(["$.bid_price", "$.bidPrice", "$.b[0][0]", "$.bids[0][0]"]),
            _payload_float_regex([r'"b":\[\["([^"]+)"', r'"bids":\[\["([^"]+)"']),
        ]
    )
    best_ask_px = pl.coalesce(
        [
            _payload_float(["$.ask_price", "$.askPrice", "$.a[0][0]", "$.asks[0][0]"]),
            _payload_float_regex([r'"a":\[\["([^"]+)"', r'"asks":\[\["([^"]+)"']),
        ]
    )
    mid_px = ((best_bid_px + best_ask_px) / 2.0).alias("_mid_px")
    price_expr = pl.coalesce(
        [
            pl.col("value"),
            _payload_float(["$.p", "$.c", "$.markPrice"]),
            mid_px,
        ]
    )
    base = market

    # Microstructure extraction is best-effort: depth arrays or scalar book fields.
    try:
        base = base.with_columns(
            [
                best_bid_px.alias("_bid_px"),
                best_ask_px.alias("_ask_px"),
                mid_px,
                price_expr.alias("price"),
                _payload_float(
                    ["$.bid_sz", "$.bidSz", "$.B", "$.b[0][1]", "$.bids[0][1]"]
                ).alias("_bid_sz"),
                _payload_float(
                    ["$.ask_sz", "$.askSz", "$.A", "$.a[0][1]", "$.asks[0][1]"]
                ).alias("_ask_sz"),
                _payload_float(["$.q", "$.Q", "$.v", "$.qty"]).alias("_trade_qty"),
            ]
        )
        base = base.with_columns(
            [
                pl.coalesce(
                    [
                        pl.col("_bid_sz"),
                        _payload_float_regex([r'"b":\[\["[^"]+","([^"]+)"', r'"bids":\[\["[^"]+","([^"]+)"']),
                    ]
                ).alias("_bid_sz"),
                pl.coalesce(
                    [
                        pl.col("_ask_sz"),
                        _payload_float_regex([r'"a":\[\["[^"]+","([^"]+)"', r'"asks":\[\["[^"]+","([^"]+)"']),
                    ]
                ).alias("_ask_sz"),
            ]
        )
    except Exception as exc:  # noqa: BLE001 - degrade to a neutral book, never crash
        logger.warning("microstructure extraction failed (%s); using neutral book", exc)
        base = market.with_columns(
            [
                pl.lit(None).cast(pl.Float64).alias("_bid_px"),
                pl.lit(None).cast(pl.Float64).alias("_ask_px"),
                pl.lit(None).cast(pl.Float64).alias("_mid_px"),
                price_expr.alias("price"),
                pl.lit(None).cast(pl.Float64).alias("_bid_sz"),
                pl.lit(None).cast(pl.Float64).alias("_ask_sz"),
                pl.lit(None).cast(pl.Float64).alias("_trade_qty"),
            ]
        )

    grouped = (
        base.group_by(["symbol", "ts_ms"])
        .agg(
            [
                pl.col("price").drop_nulls().last().alias("price"),
                pl.col("_bid_sz").drop_nulls().last().alias("bid_sz"),
                pl.col("_ask_sz").drop_nulls().last().alias("ask_sz"),
                pl.col("_trade_qty").fill_null(0.0).sum().alias("trade_qty"),
            ]
        )
        .sort(["symbol", "ts_ms"])
    )

    # OBI: dimensionless imbalance in [0, 1]; forward-filled across ticks that
    # carry no book frame, then neutral (0.5) at the very start of a series.
    grouped = grouped.with_columns(
        (pl.col("bid_sz") / (pl.col("bid_sz") + pl.col("ask_sz") + 1e-8)).alias("indicator_obi")
    )
    grouped = grouped.with_columns(
        pl.col("indicator_obi").forward_fill().over("symbol").alias("indicator_obi")
    )
    grouped = grouped.with_columns(
        [
            pl.col("indicator_obi").fill_null(_NEUTRAL_OBI).alias("indicator_obi"),
            pl.col("trade_qty").diff().over("symbol").fill_null(0.0).alias("indicator_volume_delta"),
        ]
    )

    # Carry the last known price forward so an isolated book-only tick still
    # prices; drop any leading rows that never observed a price at all.
    grouped = grouped.with_columns(
        pl.col("price").forward_fill().over("symbol").alias("price")
    ).filter(pl.col("price").is_not_null())
    return grouped


# ---------------------------------------------------------------------------
# Stage 3 helpers -- low-frequency wide frames
# ---------------------------------------------------------------------------
def _build_macro_wide(macro: "pl.DataFrame") -> Optional["pl.DataFrame"]:
    """Explode macro payloads into a ts-indexed wide frame of ``macro_*`` columns."""
    keys = _discover_payload_keys(macro)
    if not keys:
        # No structured payload -> fall back to the single scalar ``value``.
        wide = (
            macro.select([pl.col("ts_ms"), pl.col("value").alias("macro_value")])
            .group_by("ts_ms")
            .agg(pl.col("macro_value").drop_nulls().last().alias("macro_value"))
            .sort("ts_ms")
        )
        return wide if wide.height else None

    exprs = [
        pl.col("payload").str.json_path_match(f"$.{key}").cast(pl.Float64, strict=False).alias(f"macro_{key}")
        for key in keys
    ]
    projected = macro.select([pl.col("ts_ms"), *exprs])
    agg = [pl.col(f"macro_{key}").drop_nulls().last().alias(f"macro_{key}") for key in keys]
    wide = projected.group_by("ts_ms").agg(agg).sort("ts_ms")
    return wide if wide.height else None


def _build_tradfi_frames(tradfi: "pl.DataFrame") -> list["pl.DataFrame"]:
    """One ts-indexed ``tradfi_<SYMBOL>`` price frame per traditional instrument."""
    price_expr = pl.coalesce(
        [pl.col("value"), _payload_float(["$.price", "$.p", "$.c", "$.regularMarketPrice"])]
    )
    priced = tradfi.with_columns(price_expr.alias("price")).filter(pl.col("price").is_not_null())

    frames: list["pl.DataFrame"] = []
    symbols = sorted(s for s in priced.get_column("symbol").unique().to_list() if s)
    for sym in symbols:
        column = f"tradfi_{sym}"
        sub = (
            priced.filter(pl.col("symbol") == sym)
            .select([pl.col("ts_ms"), pl.col("price").alias(column)])
            .group_by("ts_ms")
            .agg(pl.col(column).drop_nulls().last().alias(column))
            .sort("ts_ms")
        )
        if sub.height:
            frames.append(sub)
    return frames


def _build_context(low_freq_frames: list["pl.DataFrame"]) -> Optional["pl.DataFrame"]:
    """Fuse every low-frequency frame onto one master timeline, gap-filled."""
    frames = [f for f in low_freq_frames if f is not None and f.height > 0]
    if not frames:
        return None

    master = (
        pl.concat([f.select("ts_ms") for f in frames], how="vertical")
        .unique()
        .sort("ts_ms")
    )
    context = master
    for frame in frames:
        context = context.join_asof(frame.sort("ts_ms"), on="ts_ms", strategy="backward")

    value_cols = [c for c in context.columns if c != "ts_ms"]
    context = context.with_columns(
        [pl.col(c).forward_fill().backward_fill().alias(c) for c in value_cols]
    )
    return context.sort("ts_ms")


# ---------------------------------------------------------------------------
# Stage 4 -- per-symbol alignment
# ---------------------------------------------------------------------------
def _align_symbol(market_sym: "pl.DataFrame", context: Optional["pl.DataFrame"]) -> "pl.DataFrame":
    """Backward ``join_asof`` the market timeline to the macro/tradfi context."""
    aligned = market_sym.sort("ts_ms")
    if context is None or context.height == 0:
        return aligned

    aligned = aligned.join_asof(context, on="ts_ms", strategy="backward")
    ctx_cols = [c for c in context.columns if c != "ts_ms"]
    # Complete the record: forward/backward fill, then a hard zero floor so no
    # cross-market cadence gap can leak a null into the feature store.
    aligned = aligned.with_columns(
        [pl.col(c).forward_fill().backward_fill().alias(c) for c in ctx_cols]
    )
    aligned = aligned.with_columns([pl.col(c).fill_null(0.0).alias(c) for c in ctx_cols])
    return aligned


# ---------------------------------------------------------------------------
# Orchestration + export
# ---------------------------------------------------------------------------
def run_pipeline(
    raw_root: str | Path = "datasets/raw",
    out_dir: str | Path = "datasets/features",
    *,
    symbols: Optional[list[str]] = None,
    loader_compat: bool = True,
) -> dict:
    """Execute the full glue pipeline; return a structured run summary."""
    _require_polars()
    raw_root = Path(raw_root)
    out_dir = Path(out_dir)

    market = _scan_asset_class(raw_root, "market")
    macro = _scan_asset_class(raw_root, "macro")
    tradfi = _scan_asset_class(raw_root, "tradfi")

    if market is None:
        logger.warning("no market data under %s/market -- nothing to align", raw_root)
        return {"symbols": {}, "written": [], "reason": "no market data"}

    market_features = _build_market_features(market)
    if market_features.height == 0:
        logger.warning("market data present but produced zero priced rows")
        return {"symbols": {}, "written": [], "reason": "no priced market rows"}

    macro_wide = _build_macro_wide(macro) if macro is not None else None
    tradfi_frames = _build_tradfi_frames(tradfi) if tradfi is not None else []
    context = _build_context(([macro_wide] if macro_wide is not None else []) + tradfi_frames)
    context_cols = 0 if context is None else len([c for c in context.columns if c != "ts_ms"])
    logger.info("low-frequency context resolved: %d aligned columns", context_cols)

    out_dir.mkdir(parents=True, exist_ok=True)
    available = [s for s in market_features.get_column("symbol").unique().to_list() if s]
    if symbols:
        wanted = {s.upper() for s in symbols}
        available = [s for s in available if s.upper() in wanted]
    available.sort()

    written: list[str] = []
    stats: dict[str, int] = {}
    for sym in available:
        sub = market_features.filter(pl.col("symbol") == sym)
        aligned = _align_symbol(sub, context)
        # Final invariant guard on the mandatory indicators.
        aligned = aligned.with_columns(
            [
                pl.col("indicator_obi").fill_null(_NEUTRAL_OBI).alias("indicator_obi"),
                pl.col("indicator_volume_delta").fill_null(0.0).alias("indicator_volume_delta"),
            ]
        )

        canonical = out_dir / f"{sym}_features.parquet"
        aligned.write_parquet(canonical)
        written.append(str(canonical))
        # Loader-compatible shard so the existing FeatureLoader glob
        # (``features_*.parquet``) feeds ``training.quant.label_symbol`` unchanged.
        if loader_compat:
            shard = out_dir / f"features_{sym}.parquet"
            aligned.write_parquet(shard)
            written.append(str(shard))

        stats[sym] = aligned.height
        logger.info("aligned %s -> %d rows", sym, aligned.height)

    logger.info(
        "feature pipeline complete: %d symbols, %d files under %s",
        len(stats), len(written), out_dir,
    )
    return {"symbols": stats, "written": written}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="feature_pipeline.py",
        description="ECONITH multi-frequency glue (raw lake -> time-aligned feature store)",
    )
    parser.add_argument("--raw-root", default="datasets/raw", help="raw data lake root")
    parser.add_argument("--out-dir", default="datasets/features", help="feature store output dir")
    parser.add_argument(
        "--symbols",
        default="",
        help="optional comma-separated symbol filter (e.g. BTCUSDT,ETHUSDT)",
    )
    parser.add_argument(
        "--no-loader-compat",
        action="store_true",
        help="do not also emit features_<SYMBOL>.parquet shards for FeatureLoader",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] or None
    summary = run_pipeline(
        raw_root=args.raw_root,
        out_dir=args.out_dir,
        symbols=symbols,
        loader_compat=not args.no_loader_compat,
    )
    if not summary.get("symbols"):
        logger.warning("no feature files written (%s)", summary.get("reason", "unknown"))
        sys.exit(1)


if __name__ == "__main__":
    main()
