"""Frozen deterministic classical quant challengers on strictly prior closed bars.

Five families, no learned parameters, no external IO, no exchange trade
adapters. Same original cash/fill/risk engine as legacy for honest comparison.
Inspired by *public research*, NOT claiming proprietary fund strategies.
"""
from __future__ import annotations

import math
from statistics import pstdev
from typing import Callable

from econith.quant.paper_v1 import Candle

NAMES = (
    "frozen_momentum24",
    "trend_vol_v1",
    "donchian_v1",
    "mean_revert_v1",
    "deterministic_ensemble_v1",
)
CANDIDATE_WARMUP = 97
MAX_WEIGHT = .20


def _bar_vol(closes: list[float], n: int = 24) -> float:
    # Returns from n+1 fully closed observations only. Zero data volatility
    # is NOT interpreted as no risk or infinite desired leverage.
    if len(closes) < n + 1:
        return 0.0
    returns = [math.log(closes[i] / closes[i - 1])
               for i in range(len(closes) - n, len(closes))]
    v = pstdev(returns)
    return v if math.isfinite(v) else 0.0


def _vol_target(history: tuple[Candle, ...]) -> float:
    v = _bar_vol([x.close for x in history])
    return min(MAX_WEIGHT, MAX_WEIGHT * (.025 / v)) if v > 0 else 0.0


def _momentum(history: tuple[Candle, ...]) -> float:
    if len(history) < 25:
        return 0.
    return .20 if history[-1].close / history[-25].close - 1 > .01 else 0.


def _trend(history: tuple[Candle, ...]) -> float:
    if len(history) < 73:
        return 0.
    close = history[-1].close
    earlier = history[-73].close
    r24 = close / history[-25].close - 1
    r72 = close / earlier - 1
    mean72 = sum(x.close for x in history[-72:]) / 72.
    if r24 <= .01 or r72 <= 0 or close <= mean72:
        return 0.
    return _vol_target(history)


def _breakout(history: tuple[Candle, ...]) -> float:
    if len(history) < 73:
        return 0.
    close = history[-1].close
    mean48 = sum(x.close for x in history[-48:]) / 48.
    uptrend72 = close > history[-73].close
    if not uptrend72 or close <= mean48:
        return 0.
    # The last 12 FINISHED bars may contain a close breakout above
    # the preceding 48 FINISHED highs. Current fill candle is absent.
    for i in range(len(history) - 12, len(history)):
        upper = max(x.high for x in history[i - 48:i])
        if history[i].close > upper:
            return _vol_target(history)
    return 0.


def _revert(history: tuple[Candle, ...]) -> float:
    if len(history) < 97:
        return 0.
    close = history[-1].close
    mid = sum(x.close for x in history[-20:]) / 20.
    sd = pstdev([x.close for x in history[-20:]])
    if sd <= 0:
        return 0.
    z = (close - mid) / sd
    long_return = close / history[-97].close - 1
    if z < -1.5 and long_return > -.03:
        return .10
    return 0.


def target(name: str, history: tuple[Candle, ...]) -> float:
    if name not in NAMES:
        raise ValueError("unknown frozen strategy")
    if not isinstance(history, tuple) or any(type(x) is not Candle for x in history):
        raise ValueError("strategy requires an immutable prior closed candle history")
    if not history or any((not math.isfinite(x.close) or x.close <= 0)
                          for x in history):
        raise ValueError("nonfinite strategy history")
    if name == "frozen_momentum24":
        result = _momentum(history)
    elif name == "trend_vol_v1":
        result = _trend(history)
    elif name == "donchian_v1":
        result = _breakout(history)
    elif name == "mean_revert_v1":
        result = _revert(history)
    else:
        result = .5 * _trend(history) + .3 * _breakout(history) + .2 * _revert(history)
    if not 0 <= result <= MAX_WEIGHT or not math.isfinite(result):
        raise ValueError("strategy escaped frozen target bounds")
    return result


def policy(name: str) -> Callable[[str, tuple[Candle, ...]], float]:
    if name not in NAMES:
        raise ValueError("unknown frozen family")
    def make_weight(symbol: str, history: tuple[Candle, ...]) -> float:
        return target(name, history)
    return make_weight
