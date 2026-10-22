"""Train-only, cost-aware selection with a continuous held-out research book.

Fixed hypotheses, historical rolling evaluation, no network or order adapters.
The last bar is a preregistered liquidation attempt, including exit friction.
"""
from __future__ import annotations

import math

from econith.quant.classical_challengers import NAMES, target, policy
from econith.quant.paper_audit import audit_paper_report
from econith.quant.paper_v1 import (
    Candle, PaperConfig, PaperReport, SimFill, SymbolRules, replay_spot, validated_candles,
)

TRAIN_BARS = 500
TEST_BARS = 125
WARMUP = 98
PERIODS = {'1h': 3600000, '4h': 14400000, '1d': 86400000}
COSTS = {'base': 1, 'stress3x': 3, 'stress5x': 5}


def _config(capital: float, ms: int, stress: int, *, lookback=97) -> PaperConfig:
    return PaperConfig(initial_cash=capital, timeframe_ms=ms, lookback=lookback,
                       max_asset_weight=.2, max_total_weight=.2, max_drawdown=.15,
                       fee_bps=10*stress, slippage_bps=2*stress,
                       half_spread_bps=stress, target_rebalance=True,
                       rebalance_band=.01)


def _metrics(run: PaperReport) -> dict:
    audit = audit_paper_report(run.to_dict())
    if audit['status'] != 'SIMULATED_LEDGER_RECONCILED':
        raise ValueError('unreconciled research book')
    return {
        'net_return_pct': run.net_return * 100,
        'net_pnl_usdt': run.final_equity - run.initial_cash,
        'max_drawdown_pct': run.max_drawdown * 100,
        'fill_legs': len(run.fills),
        'sell_legs': audit['closed_sell_legs'],
        'explicit_fees_usdt': audit['explicit_fees_paid'],
        'turnover_usdt': audit['gross_notional_turnover'],
        'halted': run.halted,
        'ledger_status': audit['status'],
        'residual_dust': run.last_positions,
        'report': run.to_dict(),
    }


def _passive(bars: list[Candle], symbol: str, rules: SymbolRules,
             cfg: PaperConfig, first: int) -> PaperReport:
    """Exactly one 20% entry and one terminal exit; no hidden rebalancing."""
    fee = cfg.fee_bps / 10000
    friction = (cfg.slippage_bps + cfg.half_spread_bps) / 10000
    cash, qty = cfg.initial_cash, 0.
    fills, curve = [], []
    peak, max_dd = cash, 0.
    for i, bar in enumerate(bars):
        if i == first:
            px = bar.open * (1 + friction)
            buy = rules.floor_qty(.2 * cash / (px * (1 + fee)))
            if buy * px >= rules.min_notional:
                qty = buy
                cash -= qty * px * (1 + fee)
                fills.append(SimFill(bar.open_ms, symbol, 'BUY', qty, px,
                                     qty * px * fee, 'passive_entry'))
        if i == len(bars) - 1 and qty:
            px = bar.open * (1 - friction)
            sell = rules.floor_qty(qty)
            if sell * px >= rules.min_notional:
                cash += sell * px * (1 - fee)
                qty = max(0., qty - sell)
                fills.append(SimFill(bar.open_ms, symbol, 'SELL', sell, px,
                                     sell * px * fee, 'passive_terminal_exit'))
        equity = cash + qty * bar.close
        peak = max(peak, equity)
        max_dd = max(max_dd, 1 - equity / peak)
        curve.append(dict(timestamp_ms=bar.open_ms, equity=equity,
                          cash=cash, exposure=equity-cash))
    return PaperReport(cfg.initial_cash, equity, equity/cfg.initial_cash-1,
                       max_dd, fills, curve, [], {symbol: qty}, False)


def choose_candidate(trials: list[dict]) -> str:
    """Fixed train-only rule; CASH also wins exact score ties."""
    best, score = 'CASH', 0.
    for row in trials:
        values = [row[k] for k in ('net_return', 'first_half_return',
                                  'second_half_return', 'max_drawdown', 'passive_return')]
        if not all(math.isfinite(x) for x in values):
            raise ValueError('nonfinite training result')
        eligible = (row['net_return'] > max(0., row['passive_return'])
                    and row['first_half_return'] > 0 and row['second_half_return'] > 0
                    and row['sell_legs'] >= 2 and row['max_drawdown'] < .15
                    and not row['halted'])
        candidate_score = row['net_return'] - .5 * row['max_drawdown']
        row['eligible'] = eligible
        row['score'] = candidate_score
        if eligible and candidate_score > score:
            best, score = row['name'], candidate_score
    return best


def _training(bars, symbol, rules, capital, ms):
    cfg = _config(capital, ms, 3)
    passive = _passive(bars, symbol, rules, cfg, WARMUP)
    audit_paper_report(passive.to_dict())
    trials = []
    half = WARMUP + (len(bars) - WARMUP) // 2
    for name in NAMES:
        base_policy = policy(name)
        def train_policy(s, h):
            return 0. if len(h) == len(bars)-1 else base_policy(s, h)
        run = replay_spot({symbol: bars}, {symbol: rules}, cfg, signal_policy=train_policy)
        audit = audit_paper_report(run.to_dict())
        middle = run.equity_curve[half-1]['equity']
        trials.append(dict(name=name, net_return=run.net_return,
                           first_half_return=middle/capital-1,
                           second_half_return=run.final_equity/middle-1,
                           max_drawdown=run.max_drawdown,
                           passive_return=passive.net_return,
                           sell_legs=audit['closed_sell_legs'],
                           explicit_fees_usdt=audit['explicit_fees_paid'],
                           turnover_usdt=audit['gross_notional_turnover'],
                           halted=run.halted, ledger_status=audit['status']))
    return trials


def walkforward(snapshot: dict, *, capital=100.) -> dict:
    """Freeze each selection before its next test block; carry one real book.

    Returns a historical simulation, not a statistical probability or proof of
    future profitability. Later training may include earlier test blocks, as
    it would prospectively; earlier decisions and holdings are never rewritten.
    """
    symbols = snapshot.get('symbols')
    interval = snapshot.get('timeframe')
    if (snapshot.get('source') != 'binance_spot_public' or interval not in PERIODS
        or not isinstance(symbols, list) or len(symbols) != 1
        or set(snapshot.get('market', {})) != set(symbols)
        or set(snapshot.get('rules', {})) != set(symbols)
        or type(capital) not in (float, int) or not math.isfinite(capital)
        or not 10 <= capital <= 1000):
        raise ValueError('invalid exact Spot snapshot/capital scope')
    symbol = symbols[0]
    ms = PERIODS[interval]
    bars = validated_candles(snapshot['market'][symbol], timeframe_ms=ms,
                             asof_ms=snapshot['asof_ms'])
    if not 800 <= len(bars) <= 1000:
        raise ValueError('need 800..1000 fully closed bars')
    rules = SymbolRules(**snapshot['rules'][symbol])
    folds, selected_by_length = [], {}
    for start in range(TRAIN_BARS, len(bars), TEST_BARS):
        stop = min(start + TEST_BARS, len(bars))
        # The callable never receives bars at or after the selected test start.
        train = bars[start-TRAIN_BARS:start]
        trials = _training(train, symbol, rules, capital, ms)
        selected = choose_candidate(trials)
        for index in range(start, stop):
            selected_by_length[index] = selected
        folds.append(dict(train_first_open_ms=train[0].open_ms,
                          train_last_close_ms=train[-1].close_ms,
                          test_first_open_ms=bars[start].open_ms,
                          test_last_close_ms=bars[stop-1].close_ms,
                          test_bars=stop-start, selected=selected,
                          training_cost_multiplier=3, trials=trials))
    def selected_policy(s, h):
        if len(h) == len(bars)-1:
            return 0.
        name = selected_by_length[len(h)]
        return 0. if name == 'CASH' else target(name, h[-TRAIN_BARS:])
    def reference_policy(s, h):
        return 0. if len(h) == len(bars)-1 else target('frozen_momentum24', h)
    continuous, passive, reference = {}, {}, {}
    for cost, multiplier in COSTS.items():
        cfg = _config(capital, ms, multiplier, lookback=TRAIN_BARS-1)
        for dest, signal in ((continuous, selected_policy), (reference, reference_policy)):
            run = replay_spot({symbol: bars}, {symbol: rules}, cfg, signal_policy=signal)
            dest[cost] = _metrics(run)
        passive[cost] = _metrics(_passive(bars, symbol, rules, cfg, TRAIN_BARS))
        continuous[cost]['beats_cash'] = continuous[cost]['net_return_pct'] > 0
        continuous[cost]['beats_passive'] = (
            continuous[cost]['net_return_pct'] > passive[cost]['net_return_pct'])
    # Fold PnL comes from the SAME continuous book, including cash and costs.
    for fold in folds:
        start = next(i for i, b in enumerate(bars) if b.open_ms == fold['test_first_open_ms'])
        stop = start + fold['test_bars']
        fold['continuous_return_pct'] = {}
        for cost in COSTS:
            curve = continuous[cost]['report']['equity_curve']
            fold['continuous_return_pct'][cost] = (
                curve[stop-1]['equity']/curve[start-1]['equity']-1)*100
    stressed = continuous['stress3x']
    exploratory = (stressed['beats_cash'] and stressed['beats_passive']
                   and stressed['sell_legs'] >= 30 and not stressed['halted']
                   and all(f['continuous_return_pct']['stress3x'] > 0 for f in folds))
    return dict(schema='econith-cost-aware-walkforward-v1', symbol=symbol, timeframe=interval,
                starting_virtual_usdt=capital, train_bars=TRAIN_BARS, test_block_bars=TEST_BARS,
                observed_closed_bars=len(bars), evaluated_bars=len(bars)-TRAIN_BARS,
                candidate_registry=list(NAMES), rebalance_band=.01,
                folds=folds, continuous=continuous, passive_buyhold=passive,
                frozen_momentum_reference=reference, exploratory_candidate=exploratory,
                profitable_alpha_proven=False, pristine_holdout=False,
                historical_current_listing_rules=True, historical_survivorship_possible=True,
                execution_mode='SIMULATION', real_exchange_orders=0,
                capital_readiness='REAL_CAPITAL_NOT_AUTHORIZED')
