"""ECONITH deterministic, read-only market replay with *simulated* spot fills.

Signals use only fully closed previous candles; fills use the next candle OPEN.
No exchange credentials, networking, leverage, or live order methods exist here.
This research engine is intentionally separate from the capital-bearing bridge.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass, field
from decimal import Decimal, ROUND_DOWN
from pathlib import Path


@dataclass(frozen=True)
class Candle:
    open_ms: int
    close_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    @classmethod
    def from_binance(cls, row: list) -> "Candle":
        if len(row) < 7:
            raise ValueError("incomplete Binance kline")
        return cls(int(row[0]), int(row[6]), *(float(row[i]) for i in (1, 2, 3, 4, 5)))


@dataclass(frozen=True)
class SymbolRules:
    step_size: str
    min_notional: float

    def __post_init__(self) -> None:
        if Decimal(self.step_size) <= 0 or not math.isfinite(self.min_notional) or self.min_notional <= 0:
            raise ValueError("invalid exchange symbol rules")

    def floor_qty(self, amount: float) -> float:
        if not math.isfinite(amount) or amount < 0:
            raise ValueError("invalid quantity")
        step = Decimal(self.step_size)
        if step <= 0:
            raise ValueError("invalid step size")
        return float((Decimal(str(amount)) / step).to_integral_value(rounding=ROUND_DOWN) * step)


@dataclass(frozen=True)
class PaperConfig:
    initial_cash: float = 10000.0
    lookback: int = 12
    entry_threshold: float = 0.003
    max_asset_weight: float = 0.20
    max_total_weight: float = 0.60
    fee_bps: float = 10.0
    slippage_bps: float = 2.0
    half_spread_bps: float = 1.0
    max_drawdown: float = 0.15
    timeframe_ms: int = 3600000

    def __post_init__(self) -> None:
        if not math.isfinite(self.initial_cash) or self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if self.lookback < 2 or self.timeframe_ms <= 0:
            raise ValueError("invalid lookback/timeframe")
        for x in (self.entry_threshold, self.max_asset_weight, self.max_total_weight,
                  self.fee_bps, self.slippage_bps, self.half_spread_bps, self.max_drawdown):
            if not math.isfinite(x) or x < 0:
                raise ValueError("negative/nonfinite risk or cost parameter")
        if not 0 < self.max_asset_weight <= self.max_total_weight <= 1:
            raise ValueError("invalid allocation caps")
        if not 0 < self.max_drawdown < 1:
            raise ValueError("drawdown must be between zero and one")


@dataclass(frozen=True)
class SimFill:
    timestamp_ms: int
    symbol: str
    side: str
    quantity: float
    price: float
    fee: float
    reason: str
    execution_mode: str = "SIMULATION"


@dataclass
class PaperReport:
    initial_cash: float
    final_equity: float
    net_return: float
    max_drawdown: float
    fills: list[SimFill] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    last_positions: dict[str, float] = field(default_factory=dict)
    halted: bool = False

    def to_dict(self) -> dict:
        return {
            **{k: v for k, v in asdict(self).items() if k != "fills"},
            "fills": [asdict(v) for v in self.fills],
        }


def validated_candles(rows: list, *, timeframe_ms: int, asof_ms: int) -> list[Candle]:
    """Reject broken/unfinished candles; never silently paper over gaps."""
    candles = [(r if isinstance(r, Candle) else Candle.from_binance(r) if isinstance(r, (list, tuple)) else Candle(**r))
               for r in rows]
    if not candles:
        raise ValueError("empty market data")
    previous = None
    for bar in candles:
        vals = (bar.open, bar.high, bar.low, bar.close, bar.volume)
        if not all(math.isfinite(v) for v in vals) or min(vals[:4]) <= 0 or bar.volume < 0:
            raise ValueError("nonfinite or invalid OHLCV")
        if bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close):
            raise ValueError("inconsistent OHLC bounds")
        if bar.open_ms < 0 or bar.close_ms - bar.open_ms + 1 != timeframe_ms:
            raise ValueError("invalid candle timestamps")
        if bar.close_ms >= asof_ms:
            raise ValueError("unclosed/future candle in research input")
        if previous is not None and bar.open_ms != previous.open_ms + timeframe_ms:
            raise ValueError("missing, duplicate or out-of-order candle")
        previous = bar
    return candles


def _momentum(closes: list[float], lookback: int) -> float:
    """Last close / close lookback bars earlier - 1; caller passes closed bars only."""
    if len(closes) < lookback + 1:
        return 0.0
    return closes[-1] / closes[-1 - lookback] - 1.0


def replay_spot(
    market: dict[str, list[Candle]], rules: dict[str, SymbolRules],
    cfg: PaperConfig = PaperConfig(),
) -> PaperReport:
    """Synchronous portfolio replay. Positions are NEVER short or leveraged.

    At time t, previous candles [0..t-1] alone determine target exposure;
    fills are simulated at candle t open, with adverse spread/slippage and fees.
    Portfolio equity is recorded once per shared timestamp at t close.
    """
    names = sorted(market)
    if not names or set(names) != set(rules):
        raise ValueError("symbols and exchange rules must match")
    length = len(market[names[0]])
    if length <= cfg.lookback + 1:
        raise ValueError("insufficient closed candles")
    reference = [b.open_ms for b in market[names[0]]]
    for name in names:
        bars = market[name]
        if len(bars) != length or [b.open_ms for b in bars] != reference:
            raise ValueError("multiasset timestamps must align")
        for i, bar in enumerate(bars):
            if bar.open <= 0 or bar.close <= 0 or bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close):
                raise ValueError("invalid bar prices")
            if i and bar.open_ms != bars[i-1].open_ms + cfg.timeframe_ms:
                raise ValueError("gap/overlap in input bars")

    cash = cfg.initial_cash
    positions = {name: 0.0 for name in names}
    fills: list[SimFill] = []
    rejected: list[dict] = []
    snapshots: list[dict] = []
    high_water = cfg.initial_cash
    max_dd = 0.0
    halted = False
    friction = (cfg.slippage_bps + cfg.half_spread_bps) / 10000.0
    fee = cfg.fee_bps / 10000.0

    for i in range(length):
        current = {name: market[name][i] for name in names}
        # Mark-to-market known only at previous close, never current close.
        prev_equity = cash + sum(positions[s] * market[s][i - 1].close for s in names) if i else cash
        if i > cfg.lookback:
            # Exit existing positions before entries. Strictly previous-bar features.
            entries: list[str] = []
            exits: list[str] = []
            for s in names:
                mom = _momentum([b.close for b in market[s][:i]], cfg.lookback)
                (entries if mom > cfg.entry_threshold and not halted else exits).append(s)
            for s in exits:
                qty = rules[s].floor_qty(positions[s])
                if qty <= 0:
                    continue
                px = current[s].open * (1.0 - friction)
                notional = qty * px
                if notional < rules[s].min_notional:
                    rejected.append({"time": reference[i], "symbol": s, "reason": "dust_exit"})
                    continue
                cash += notional * (1.0 - fee)
                positions[s] = max(0.0, positions[s] - qty)
                fills.append(SimFill(reference[i], s, "SELL", qty, px, notional * fee, "momentum_exit"))
            # Enforce total cap even when many assets qualify; no borrowed funds.
            eligible = len(entries)
            weight = min(cfg.max_asset_weight, cfg.max_total_weight / eligible) if eligible else 0.0
            for s in entries:
                buy_px = current[s].open * (1.0 + friction)
                target_value = prev_equity * weight
                current_value = positions[s] * current[s].open
                desired_value = max(0.0, target_value - current_value)
                purchase_budget = min(desired_value, cash / (1.0 + fee))
                qty = rules[s].floor_qty(purchase_budget / buy_px)
                if qty <= 0:
                    continue
                cost = qty * buy_px
                if cost < rules[s].min_notional:
                    rejected.append({"time": reference[i], "symbol": s, "reason": "below_min_notional"})
                    continue
                total = cost * (1.0 + fee)
                if total > cash + 1e-8:
                    raise AssertionError("cash cap breached")
                cash -= total
                positions[s] += qty
                fills.append(SimFill(reference[i], s, "BUY", qty, buy_px, cost * fee, "momentum_entry"))

        equity = cash + sum(positions[s] * current[s].close for s in names)
        if cash < -1e-7 or any(v < -1e-10 for v in positions.values()) or not math.isfinite(equity):
            raise AssertionError("ledger invariant failed")
        high_water = max(high_water, equity)
        dd = 1.0 - equity / high_water
        max_dd = max(max_dd, dd)
        snapshots.append({"timestamp_ms": reference[i], "equity": equity, "cash": cash,
                          "exposure": equity - cash})
        if dd >= cfg.max_drawdown:
            # Halt future entries. On the next open, sell eligible positions;
            # never invent a fill at the current close.
            halted = True

    final = snapshots[-1]["equity"]
    return PaperReport(cfg.initial_cash, final, final / cfg.initial_cash - 1,
                       max_dd, fills, snapshots, rejected, positions, halted)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ECONITH offline spot paper REPLAY (never live trading)")
    parser.add_argument("--snapshot-dir", type=Path, help="SHA-256 verified public capture directory")
    parser.add_argument("--input", type=Path, help="unverified raw JSON map symbol -> Binance klines")
    parser.add_argument("--rules", type=Path, help="raw JSON map symbol -> step/min_notional")
    parser.add_argument("--asof-ms", type=int, help="UTC knowledge cutoff (raw input mode)")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.snapshot_dir is not None:
        if args.input is not None or args.rules is not None or args.asof_ms is not None:
            parser.error("snapshot-dir cannot be combined with raw input arguments")
        from econith.quant.public_binance_data import load_snapshot
        snapshot = load_snapshot(args.snapshot_dir)
        raw, rule_data, asof_ms = snapshot["market"], snapshot["rules"], snapshot["asof_ms"]
        timeframe_ms = 86_400_000 if snapshot["timeframe"] == "1d" else 3_600_000
    else:
        if args.input is None or args.rules is None or args.asof_ms is None:
            parser.error("use --snapshot-dir, or all of --input --rules --asof-ms")
        raw = json.loads(args.input.read_text(encoding="utf-8"))
        rule_data = json.loads(args.rules.read_text(encoding="utf-8"))
        asof_ms = args.asof_ms
        timeframe_ms = 3_600_000
    market = {s: validated_candles(rows, timeframe_ms=timeframe_ms, asof_ms=asof_ms)
              for s, rows in raw.items()}
    rules = {s: SymbolRules(**item) for s, item in rule_data.items()}
    report = replay_spot(market, rules, PaperConfig(timeframe_ms=timeframe_ms))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    print(f"SIMULATION ONLY: final equity {report.final_equity:.2f}; "
          f"net return {report.net_return:.2%}; fills {len(report.fills)}; "
          f"halted={report.halted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
