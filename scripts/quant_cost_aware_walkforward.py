"""Fixed 12-case public-data walk-forward experiment. Never submits orders."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from econith.quant.cost_aware_walkforward import (
    NAMES, TRAIN_BARS, TEST_BARS, COSTS, walkforward,
)
from econith.quant.public_binance_data import write_snapshot
from scripts.paper_trade_quickstart import _capture_exact_public_pair

PLAN = {
    'schema': 'econith-p207-frozen-before-capture-v1',
    'symbols': ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'DOGEUSDT'],
    'intervals': ['1h', '4h', '1d'],
    'requested_bars': 1000, 'capital_usdt': 100.,
    'training_bars': TRAIN_BARS, 'test_block_bars': TEST_BARS,
    'candidates': list(NAMES), 'cost_multipliers': COSTS,
    'selection_cost_multiplier': 3,
    'selection_rule': 'both train halves positive, beats cash and cost-matched passive, '
                      '>=2 sells, DD<15%, no halt; maximize return-0.5*DD; otherwise CASH',
    'max_asset_weight': .2, 'max_total_weight': .2, 'rebalance_band': .01,
    'base_fee_bps': 10, 'base_slippage_bps': 2, 'base_half_spread_bps': 1,
    'terminal_rule': 'last candle open: attempt liquidation with exit costs; disclose dust',
    'continuous_book': True, 'retune_after_outcomes': False,
    'pristine_holdout': False, 'historical_rules_point_in_time': False,
}


def _save(path: Path, data: dict) -> None:
    raw = (json.dumps(data, sort_keys=True, indent=2, allow_nan=False)+'\n').encode()
    with path.open('xb') as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def run_matrix(root: Path, *, capture=_capture_exact_public_pair) -> dict:
    if (not root.is_absolute() or root.exists() or root.is_symlink()
        or root != root.resolve() or not root.parent.is_dir()):
        raise ValueError('new absolute evidence directory required')
    root.mkdir(mode=0o700)
    # Freeze the experiment and implementation hashes BEFORE the first request.
    _save(root/'frozen_plan.json', PLAN)
    base = Path(__file__).resolve().parents[1]
    files = ['econith/quant/paper_v1.py', 'econith/quant/classical_challengers.py',
             'econith/quant/cost_aware_walkforward.py', 'scripts/quant_cost_aware_walkforward.py',
             'econith/quant/paper_audit.py', 'econith/quant/public_binance_data.py',
             'scripts/paper_trade_quickstart.py', 'ci/phase1_spot_multiasset_ohlcv.py']
    hashes = {name: hashlib.sha256((base/name).read_bytes()).hexdigest() for name in files}
    _save(root/'implementation_sha256.json', hashes)
    cases = []
    for symbol in PLAN['symbols']:
        for interval in PLAN['intervals']:
            entry = dict(symbol=symbol, interval=interval, status='FAILED')
            try:
                snapshot = capture([symbol], limit=PLAN['requested_bars'], interval=interval)
                if (snapshot.get('symbols') != [symbol] or snapshot.get('timeframe') != interval
                    or snapshot.get('source') != 'binance_spot_public'):
                    raise ValueError('source/pair/interval substitution refused')
                dest = root/(symbol.lower()+'_'+interval)
                # Preserve the source even if simulation subsequently refuses it.
                manifest = write_snapshot(snapshot, dest)
                result = walkforward(snapshot, capital=PLAN['capital_usdt'])
                _save(dest/'walkforward.json', result)
                entry.update(status='COMPLETE', result=result,
                             snapshot_sha256=manifest['sha256'])
            except (ValueError, OSError, RuntimeError, KeyError, TypeError, IndexError) as exc:
                entry['error_class'] = type(exc).__name__
            cases.append(entry)
            print(json.dumps({k: v for k, v in entry.items()
                              if k not in ('result', 'snapshot_sha256')}, sort_keys=True), flush=True)
    complete = [c for c in cases if c['status'] == 'COMPLETE']
    brief = []
    for case in complete:
        r = case['result']
        brief.append(dict(symbol=case['symbol'], interval=case['interval'],
                          selected_by_fold=[f['selected'] for f in r['folds']],
                          base_net_pct=r['continuous']['base']['net_return_pct'],
                          stress3x_net_pct=r['continuous']['stress3x']['net_return_pct'],
                          stress5x_net_pct=r['continuous']['stress5x']['net_return_pct'],
                          stress3x_reference_pct=r['frozen_momentum_reference']['stress3x']['net_return_pct'],
                          stress3x_passive_pct=r['passive_buyhold']['stress3x']['net_return_pct'],
                          stress3x_fill_legs=r['continuous']['stress3x']['fill_legs'],
                          stress3x_fees=r['continuous']['stress3x']['explicit_fees_usdt'],
                          exploratory_candidate=r['exploratory_candidate']))
    result = dict(schema='econith-p207-matrix-v1', expected_cases=12,
                  completed_cases=len(complete), cases=cases, comparison=brief,
                  status='COMPLETE' if len(complete) == 12 else 'INCOMPLETE',
                  plan_sha256=hashlib.sha256((root/'frozen_plan.json').read_bytes()).hexdigest(),
                  positive_stress3x_cases=sum(c['stress3x_net_pct'] > 0 for c in brief),
                  exploratory_candidates=sum(c['exploratory_candidate'] for c in brief),
                  profitable_alpha_proven=False, real_exchange_orders=0,
                  capital_readiness='REAL_CAPITAL_NOT_AUTHORIZED')
    _save(root/'matrix.json', result)
    _save(root/'summary.json', {k: v for k, v in result.items() if k != 'cases'})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--allow-public-market-data', action='store_true')
    parser.add_argument('--simulation-only', action='store_true')
    args = parser.parse_args(argv)
    if not (args.allow_public_market_data and args.simulation_only):
        parser.error('explicit public-data and simulation-only opt-in required')
    try:
        result = run_matrix(args.output_dir)
    except (ValueError, OSError) as exc:
        print(json.dumps(dict(status='REFUSED', error_class=type(exc).__name__)))
        return 2
    print(json.dumps({k: v for k, v in result.items() if k != 'cases'}, sort_keys=True))
    # A loss is a valid observation; missing data is incomplete research.
    return 0 if result['status'] == 'COMPLETE' else 2


if __name__ == '__main__':
    raise SystemExit(main())
