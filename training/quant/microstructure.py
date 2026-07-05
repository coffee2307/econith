"""ECONITH :: training.quant.microstructure  (PHASE 1 -- HF microstructure engine)

The canonical, high-performance order-flow feature engine. Every high-frequency
crypto microstructure metric the training/backtesting tiers consume is defined
*here*, once, as vectorised Polars expressions -- never as ad-hoc regex in the
glue layer.

Input contract
--------------
A Polars ``DataFrame`` in the canonical raw-lake schema written by the VPS
collector (``econith-vps-collector/storage.py``)::

    ts_ms:int  asset_class:str  symbol:str  channel:str  source:str
    value:f64  payload:str(JSON)

Three Binance USD-M Futures channels are recognised:

  * ``depthUpdate``      -- from ``depth20@100ms``; full top-20 snapshot each
                            frame: payload ``b``/``a`` = ``[[price, qty], ...]``
                            (bids descending, asks ascending).
  * ``aggTrade``         -- payload ``p`` price, ``q`` qty, ``m`` isBuyerMaker
                            (aggressor is the SELLER when ``m`` is true).
  * ``markPriceUpdate``  -- payload ``p`` mark price, ``r`` funding rate.

Output
------
One per-``(symbol, ts_ms)`` feature frame carrying (levels default L1..L5):

    indicator_obi_l1 .. l5   order-book imbalance ratio in [0, 1] (0.5 neutral)
    indicator_obi            primary OBI (== L1, kept for backward-compat)
    indicator_depth_imbalance signed cumulative-L5 imbalance in [-1, 1]
    indicator_book_slope     size-weighted liquidity decay imbalance
    spread / rel_spread      best_ask - best_bid, and /mid
    microprice               size-weighted top-of-book fair price
    indicator_trade_imbalance rolling signed-volume imbalance in [-1, 1]
    indicator_vpin           Volume-Synchronised Probability of Informed Trading
    funding_rate             perp funding rate from markPrice
    price                    fair price (trade VWAP > mark > mid, forward-filled)

Performance design
------------------
* Zero Python row loops. Payloads are decoded with Polars' native
  ``str.json_decode`` into typed structs, then all math is columnar.
* The only Python iteration is over the *compile-time* fixed ``levels`` range.
* Safe to run over a multi-GB raw lake; degrades gracefully (neutral values,
  never NaN/raise) when a channel or level is absent.

Zero-ML boundary: imports only ``polars`` + stdlib.
"""
from __future__ import annotations

import logging
from typing import Optional

import polars as pl

logger = logging.getLogger("econith.training.quant.microstructure")

__all__ = [
    "DEFAULT_DEPTH_LEVELS",
    "build_hf_features",
    "compute_depth_features",
    "compute_trade_features",
    "compute_vpin",
    "compute_mark_features",
]

DEFAULT_DEPTH_LEVELS: int = 5

# Numerical floor so a one-sided / empty book never divides by zero.
_EPS: float = 1e-9
# Neutral order-book imbalance (a perfectly balanced or unknown book).
_NEUTRAL_OBI: float = 0.5

# Channel identifiers as emitted by Binance ``data.e``.
_CH_DEPTH: str = "depthUpdate"
_CH_TRADE: str = "aggTrade"
_CH_MARK: str = "markPriceUpdate"

# Partial JSON schemas: only the fields we need are decoded; extra keys ignored.
_DEPTH_DTYPE = pl.Struct(
    {"b": pl.List(pl.List(pl.Utf8)), "a": pl.List(pl.List(pl.Utf8))}
)
_TRADE_DTYPE = pl.Struct({"p": pl.Utf8, "q": pl.Utf8, "m": pl.Boolean, "T": pl.Int64})
_MARK_DTYPE = pl.Struct({"p": pl.Utf8, "r": pl.Utf8})


# ---------------------------------------------------------------------------
# small expression helpers
# ---------------------------------------------------------------------------
def _book_cell(side: pl.Expr, level: int, field: int) -> pl.Expr:
    """Extract ``side[level][field]`` (field 0=price, 1=size) as a float.

    ``null_on_oob`` guarantees a missing level yields a typed null rather than
    raising, so a thin book (< ``levels`` deep) degrades cleanly.
    """
    return (
        side.list.get(level, null_on_oob=True)
        .list.get(field, null_on_oob=True)
        .cast(pl.Float64, strict=False)
    )


def _safe_ratio(numer: pl.Expr, denom: pl.Expr) -> pl.Expr:
    return numer / (denom + _EPS)


# ---------------------------------------------------------------------------
# depth (order book) features
# ---------------------------------------------------------------------------
def compute_depth_features(
    market: pl.DataFrame, *, levels: int = DEFAULT_DEPTH_LEVELS
) -> Optional[pl.DataFrame]:
    """Per-``(symbol, ts_ms)`` order-book features from ``depthUpdate`` frames.

    Returns ``None`` when no depth frames are present.
    """
    depth = market.filter(pl.col("channel") == _CH_DEPTH)
    if depth.height == 0:
        return None

    book = pl.col("payload").str.json_decode(_DEPTH_DTYPE)
    bids = book.struct.field("b")
    asks = book.struct.field("a")

    # Explicit per-level price/size columns (compile-time loop over fixed levels).
    level_cols: list[pl.Expr] = []
    for i in range(levels):
        level_cols.extend(
            [
                _book_cell(bids, i, 0).alias(f"_bid_px_{i + 1}"),
                _book_cell(bids, i, 1).alias(f"_bid_sz_{i + 1}"),
                _book_cell(asks, i, 0).alias(f"_ask_px_{i + 1}"),
                _book_cell(asks, i, 1).alias(f"_ask_sz_{i + 1}"),
            ]
        )
    depth = depth.with_columns(level_cols)

    # Collapse multiple frames sharing a millisecond to the last observed book.
    agg_exprs: list[pl.Expr] = []
    for i in range(1, levels + 1):
        for tag in ("bid_px", "bid_sz", "ask_px", "ask_sz"):
            col = f"_{tag}_{i}"
            agg_exprs.append(pl.col(col).drop_nulls().last().alias(col))
    snap = (
        depth.group_by(["symbol", "ts_ms"]).agg(agg_exprs).sort(["symbol", "ts_ms"])
    )

    # Cumulative depth per side across L1..Ln, and per-level OBI ratios.
    cum_bid = pl.lit(0.0)
    cum_ask = pl.lit(0.0)
    obi_cols: list[pl.Expr] = []
    for i in range(1, levels + 1):
        cum_bid = cum_bid + pl.col(f"_bid_sz_{i}").fill_null(0.0)
        cum_ask = cum_ask + pl.col(f"_ask_sz_{i}").fill_null(0.0)
        obi_cols.append(
            _safe_ratio(cum_bid, cum_bid + cum_ask).alias(f"indicator_obi_l{i}")
        )
    snap = snap.with_columns(obi_cols)

    # Cumulative totals at the deepest requested level for signed imbalance/slope.
    cum_bid_full = pl.sum_horizontal(
        [pl.col(f"_bid_sz_{i}").fill_null(0.0) for i in range(1, levels + 1)]
    )
    cum_ask_full = pl.sum_horizontal(
        [pl.col(f"_ask_sz_{i}").fill_null(0.0) for i in range(1, levels + 1)]
    )

    best_bid = pl.col("_bid_px_1")
    best_ask = pl.col("_ask_px_1")
    bid_sz1 = pl.col("_bid_sz_1").fill_null(0.0)
    ask_sz1 = pl.col("_ask_sz_1").fill_null(0.0)
    mid = (best_bid + best_ask) / 2.0
    spread = (best_ask - best_bid)

    # Book slope: size accrued per unit of price distance from the touch, one
    # side vs the other -> a signed liquidity-decay imbalance in [-1, 1].
    bid_span = (best_bid - pl.col(f"_bid_px_{levels}")).abs()
    ask_span = (pl.col(f"_ask_px_{levels}") - best_ask).abs()
    bid_slope = _safe_ratio(cum_bid_full, bid_span)
    ask_slope = _safe_ratio(cum_ask_full, ask_span)

    snap = snap.with_columns(
        [
            best_bid.alias("best_bid"),
            best_ask.alias("best_ask"),
            mid.alias("mid"),
            spread.alias("spread"),
            _safe_ratio(spread, mid).alias("rel_spread"),
            # Microprice: fair value weighted by the *opposite* top size.
            _safe_ratio(
                best_bid * ask_sz1 + best_ask * bid_sz1, bid_sz1 + ask_sz1
            ).alias("microprice"),
            (
                (cum_bid_full - cum_ask_full) / (cum_bid_full + cum_ask_full + _EPS)
            ).alias("indicator_depth_imbalance"),
            (
                (bid_slope - ask_slope) / (bid_slope + ask_slope + _EPS)
            ).alias("indicator_book_slope"),
        ]
    )

    # Primary OBI kept as L1 for backward compatibility with the legacy pipeline.
    snap = snap.with_columns(pl.col("indicator_obi_l1").alias("indicator_obi"))

    keep = (
        ["symbol", "ts_ms", "best_bid", "best_ask", "mid", "spread", "rel_spread",
         "microprice", "indicator_obi", "indicator_depth_imbalance",
         "indicator_book_slope"]
        + [f"indicator_obi_l{i}" for i in range(1, levels + 1)]
    )
    return snap.select(keep)


# ---------------------------------------------------------------------------
# trade features (trade imbalance)
# ---------------------------------------------------------------------------
def _decode_trades(market: pl.DataFrame) -> Optional[pl.DataFrame]:
    """Decode ``aggTrade`` frames into signed trade rows, or ``None``."""
    trades = market.filter(pl.col("channel") == _CH_TRADE)
    if trades.height == 0:
        return None

    t = pl.col("payload").str.json_decode(_TRADE_DTYPE)
    price = t.struct.field("p").cast(pl.Float64, strict=False)
    qty = t.struct.field("q").cast(pl.Float64, strict=False)
    # ``m`` == buyer-is-maker -> the aggressor is the SELLER -> a sell.
    is_buy = ~t.struct.field("m").fill_null(False)
    trade_time = pl.coalesce([t.struct.field("T"), pl.col("ts_ms")])

    decoded = market.filter(pl.col("channel") == _CH_TRADE).with_columns(
        [
            price.alias("trade_px"),
            qty.fill_null(0.0).alias("trade_qty"),
            is_buy.alias("is_buy"),
            trade_time.alias("trade_time"),
        ]
    )
    return decoded.filter(pl.col("trade_qty") > 0.0)


def compute_trade_features(
    market: pl.DataFrame, *, imbalance_window: int = 50
) -> Optional[pl.DataFrame]:
    """Per-``(symbol, ts_ms)`` trade-flow features from ``aggTrade`` frames.

    ``indicator_trade_imbalance`` is the signed traded-volume imbalance over the
    trailing ``imbalance_window`` aggregated ticks, in ``[-1, 1]``.
    """
    decoded = _decode_trades(market)
    if decoded is None:
        return None

    decoded = decoded.with_columns(
        pl.when(pl.col("is_buy"))
        .then(pl.col("trade_qty"))
        .otherwise(-pl.col("trade_qty"))
        .alias("_signed_qty")
    )

    per_ts = (
        decoded.group_by(["symbol", "ts_ms"])
        .agg(
            [
                _safe_ratio(
                    (pl.col("trade_px") * pl.col("trade_qty")).sum(),
                    pl.col("trade_qty").sum(),
                ).alias("trade_vwap"),
                pl.col("trade_qty").sum().alias("trade_volume"),
                pl.col("_signed_qty").sum().alias("_signed_vol"),
                pl.col("trade_qty").filter(pl.col("is_buy")).sum().alias("buy_volume"),
                pl.col("trade_qty").filter(~pl.col("is_buy")).sum().alias("sell_volume"),
            ]
        )
        .sort(["symbol", "ts_ms"])
    )

    per_ts = per_ts.with_columns(
        _safe_ratio(
            pl.col("_signed_vol")
            .rolling_sum(window_size=imbalance_window, min_periods=1)
            .over("symbol"),
            pl.col("trade_volume")
            .rolling_sum(window_size=imbalance_window, min_periods=1)
            .over("symbol"),
        ).alias("indicator_trade_imbalance")
    )
    return per_ts.drop("_signed_vol")


# ---------------------------------------------------------------------------
# VPIN -- Volume-Synchronised Probability of Informed Trading
# ---------------------------------------------------------------------------
def compute_vpin(
    market: pl.DataFrame,
    *,
    n_buckets: int = 50,
    bucket_volume: Optional[float] = None,
    target_buckets: int = 200,
) -> Optional[pl.DataFrame]:
    """Per-``(symbol, ts_ms)`` VPIN from ``aggTrade`` frames.

    Methodology (Easley, Lopez de Prado & O'Hara, 2012), adapted to use the
    exact aggressor sign Binance provides instead of bulk-volume classification:

      1. Order trades chronologically per symbol and accumulate volume.
      2. Slice the volume axis into equal buckets of size ``V`` (per-symbol; if
         ``bucket_volume`` is None, ``V = total_volume / target_buckets``).
      3. Per bucket, order imbalance ``OI = |buy_vol - sell_vol|``.
      4. ``VPIN = sum(OI over last n_buckets) / sum(bucket_vol over last n)`` in
         ``[0, 1]`` -- a normalised toxicity/informed-flow estimate.

    The bucket VPIN is broadcast back to every trade in the bucket, then reduced
    to the last value per ``(symbol, ts_ms)`` so it aligns to the feature clock.
    """
    decoded = _decode_trades(market)
    if decoded is None:
        return None

    trades = decoded.select(
        ["symbol", "ts_ms", "trade_time", "trade_qty", "is_buy"]
    ).sort(["symbol", "trade_time", "ts_ms"])

    trades = trades.with_columns(
        pl.col("trade_qty").cum_sum().over("symbol").alias("_cumvol")
    )

    # Resolve the per-symbol bucket volume V.
    if bucket_volume is not None and bucket_volume > 0:
        trades = trades.with_columns(pl.lit(float(bucket_volume)).alias("_V"))
    else:
        vol_by_sym = trades.group_by("symbol").agg(
            (pl.col("trade_qty").sum() / max(1, target_buckets)).alias("_V")
        )
        # Guard degenerate (all-zero) volume so V is strictly positive.
        vol_by_sym = vol_by_sym.with_columns(
            pl.when(pl.col("_V") > 0).then(pl.col("_V")).otherwise(_EPS).alias("_V")
        )
        trades = trades.join(vol_by_sym, on="symbol", how="left")

    trades = trades.with_columns(
        (pl.col("_cumvol") / pl.col("_V")).floor().cast(pl.Int64).alias("_bucket")
    )

    buckets = (
        trades.group_by(["symbol", "_bucket"])
        .agg(
            [
                pl.col("trade_qty").filter(pl.col("is_buy")).sum().alias("_buy"),
                pl.col("trade_qty").filter(~pl.col("is_buy")).sum().alias("_sell"),
                pl.col("trade_qty").sum().alias("_bvol"),
            ]
        )
        .sort(["symbol", "_bucket"])
    )
    buckets = buckets.with_columns(
        (pl.col("_buy") - pl.col("_sell")).abs().alias("_oi")
    )
    buckets = buckets.with_columns(
        _safe_ratio(
            pl.col("_oi").rolling_sum(window_size=n_buckets, min_periods=1).over("symbol"),
            pl.col("_bvol").rolling_sum(window_size=n_buckets, min_periods=1).over("symbol"),
        ).alias("indicator_vpin")
    )

    # Broadcast bucket VPIN to trades, then reduce to the feature clock.
    trades = trades.join(
        buckets.select(["symbol", "_bucket", "indicator_vpin"]),
        on=["symbol", "_bucket"],
        how="left",
    )
    return (
        trades.group_by(["symbol", "ts_ms"])
        .agg(pl.col("indicator_vpin").drop_nulls().last().alias("indicator_vpin"))
        .sort(["symbol", "ts_ms"])
    )


# ---------------------------------------------------------------------------
# mark price + funding
# ---------------------------------------------------------------------------
def compute_mark_features(market: pl.DataFrame) -> Optional[pl.DataFrame]:
    """Per-``(symbol, ts_ms)`` mark price + funding rate from ``markPriceUpdate``."""
    mark = market.filter(pl.col("channel") == _CH_MARK)
    if mark.height == 0:
        return None

    m = pl.col("payload").str.json_decode(_MARK_DTYPE)
    mark = mark.with_columns(
        [
            pl.coalesce([m.struct.field("p").cast(pl.Float64, strict=False), pl.col("value")])
            .alias("mark_price"),
            m.struct.field("r").cast(pl.Float64, strict=False).alias("funding_rate"),
        ]
    )
    return (
        mark.group_by(["symbol", "ts_ms"])
        .agg(
            [
                pl.col("mark_price").drop_nulls().last().alias("mark_price"),
                pl.col("funding_rate").drop_nulls().last().alias("funding_rate"),
            ]
        )
        .sort(["symbol", "ts_ms"])
    )


# ---------------------------------------------------------------------------
# top-level assembler
# ---------------------------------------------------------------------------
def build_hf_features(
    market: pl.DataFrame,
    *,
    levels: int = DEFAULT_DEPTH_LEVELS,
    trade_imbalance_window: int = 50,
    vpin_buckets: int = 50,
    bucket_volume: Optional[float] = None,
) -> pl.DataFrame:
    """Fuse depth + trade + mark channels into one per-``(symbol, ts_ms)`` frame.

    Robust to missing channels: a depth-only lake still yields OBI/slope/spread,
    with trade/VPIN/funding columns filled to neutral so downstream schemas stay
    stable. Never raises on degenerate input; never emits a NaN.
    """
    market = market.filter(pl.col("symbol").is_not_null() & (pl.col("symbol") != ""))
    if market.height == 0:
        return market.head(0)

    depth = compute_depth_features(market, levels=levels)
    trades = compute_trade_features(market, imbalance_window=trade_imbalance_window)
    vpin = compute_vpin(market, n_buckets=vpin_buckets, bucket_volume=bucket_volume)
    mark = compute_mark_features(market)

    frames = [f for f in (depth, trades, vpin, mark) if f is not None]
    if not frames:
        return market.head(0)

    fused = frames[0]
    for frame in frames[1:]:
        fused = fused.join(frame, on=["symbol", "ts_ms"], how="full", coalesce=True)
    fused = fused.sort(["symbol", "ts_ms"])

    # Fair price: prefer executed VWAP, then mark, then book mid; carry forward.
    price = pl.coalesce(
        [
            pl.col("trade_vwap") if "trade_vwap" in fused.columns else pl.lit(None),
            pl.col("mark_price") if "mark_price" in fused.columns else pl.lit(None),
            pl.col("mid") if "mid" in fused.columns else pl.lit(None),
        ]
    )
    fused = fused.with_columns(price.cast(pl.Float64, strict=False).alias("price"))

    # Forward-fill book/mark state across trade-only ticks (and vice-versa).
    carry_cols = [
        c
        for c in (
            "best_bid", "best_ask", "mid", "spread", "rel_spread", "microprice",
            "indicator_obi", "indicator_depth_imbalance", "indicator_book_slope",
            "mark_price", "funding_rate", "price",
        )
        + tuple(f"indicator_obi_l{i}" for i in range(1, levels + 1))
        if c in fused.columns
    ]
    fused = fused.with_columns(
        [pl.col(c).forward_fill().over("symbol").alias(c) for c in carry_cols]
    )

    # Canonical HF schema with neutral defaults. Columns are *created* when their
    # source channel was absent (e.g. a depth-only lake), guaranteeing a dense,
    # stable, NaN-free contract for every downstream (label / backtest / train).
    neutral = {
        "indicator_obi": _NEUTRAL_OBI,
        "indicator_depth_imbalance": 0.0,
        "indicator_book_slope": 0.0,
        "indicator_trade_imbalance": 0.0,
        "indicator_vpin": 0.0,
        "funding_rate": 0.0,
        "buy_volume": 0.0,
        "sell_volume": 0.0,
        "trade_volume": 0.0,
    }
    for i in range(1, levels + 1):
        neutral[f"indicator_obi_l{i}"] = _NEUTRAL_OBI

    materialize = [
        (
            pl.col(c).fill_null(v)
            if c in fused.columns
            else pl.lit(v, dtype=pl.Float64)
        ).alias(c)
        for c, v in neutral.items()
    ]
    fused = fused.with_columns(materialize)

    # Drop rows that never observed any price (pure leading trade-imbalance gaps).
    if "price" in fused.columns:
        fused = fused.filter(pl.col("price").is_not_null())
    return fused
