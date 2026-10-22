"""Causal selection, cash controls and full loss-inclusive evidence."""
import copy
import unittest
from unittest.mock import patch

from econith.quant.cost_aware_walkforward import walkforward, choose_candidate


def snapshot(n=900, trend='flat'):
    ms = 14400000
    rows = []
    for i in range(n):
        p = 100. if trend == 'flat' else 100. * 1.001 ** i
        rows.append([i*ms, str(p), str(p*1.005), str(p*.995), str(p), '500', (i+1)*ms-1])
    return {'source': 'binance_spot_public', 'symbols': ['BTCUSDT'],
            'timeframe': '4h', 'asof_ms': n*ms, 'market': {'BTCUSDT': rows},
            'rules': {'BTCUSDT': {'step_size': '.000001', 'min_notional': 5}}}


class CostAwareWalkforwardTests(unittest.TestCase):
    def test_cash_wins_when_every_train_candidate_is_unprofitable(self):
        trials = [{'name': 'x', 'net_return': -.1, 'first_half_return': -.1,
                   'second_half_return': -.1, 'max_drawdown': .1,
                   'sell_legs': 4, 'passive_return': -.2, 'halted': False}]
        self.assertEqual(choose_candidate(trials), 'CASH')

    def test_one_good_half_or_passive_underperformance_does_not_qualify(self):
        trial = dict(name='x', net_return=.05, first_half_return=.03,
                     second_half_return=-.01, max_drawdown=.01, sell_legs=4,
                     passive_return=.04, halted=False)
        self.assertEqual(choose_candidate([trial]), 'CASH')
        trial.update(second_half_return=.02, passive_return=.06)
        self.assertEqual(choose_candidate([trial]), 'CASH')
        trial.update(passive_return=.01)
        self.assertEqual(choose_candidate([trial]), 'x')

    def test_flat_data_retains_all_folds_and_zero_returns(self):
        out = walkforward(snapshot())
        self.assertEqual(len(out['folds']), 4)
        self.assertEqual(sum(f['test_bars'] for f in out['folds']), 400)
        self.assertTrue(all(f['selected'] == 'CASH' for f in out['folds']))
        self.assertEqual(out['continuous']['stress3x']['net_return_pct'], 0.)
        self.assertFalse(out['profitable_alpha_proven'])
        self.assertEqual(out['continuous']['stress3x']['ledger_status'],
                         'SIMULATED_LEDGER_RECONCILED')

    def test_training_selections_and_past_fills_ignore_future_prices(self):
        original = snapshot(trend='up')
        changed = copy.deepcopy(original)
        for row in changed['market']['BTCUSDT'][700:]:
            row[1:5] = [str(float(v)*.4) for v in row[1:5]]
        a, b = walkforward(original), walkforward(changed)
        self.assertEqual(a['folds'][:2], b['folds'][:2])
        cutoff = 700 * 14400000
        for cost in ('base', 'stress3x', 'stress5x'):
            x = a['continuous'][cost]['report']['fills']
            y = b['continuous'][cost]['report']['fills']
            self.assertEqual([f for f in x if f['timestamp_ms'] < cutoff],
                             [f for f in y if f['timestamp_ms'] < cutoff])

    def test_selected_model_never_gets_the_fill_candle(self):
        seen = []
        def spy(name, history):
            seen.append(len(history))
            return .2
        with patch('econith.quant.cost_aware_walkforward.choose_candidate', return_value='trend_vol_v1'), \
             patch('econith.quant.cost_aware_walkforward.target', side_effect=spy):
            out = walkforward(snapshot(trend='up'))
        fills = out['continuous']['base']['report']['fills']
        self.assertGreaterEqual(min(seen), 500)
        self.assertGreaterEqual(fills[0]['timestamp_ms'], 500 * 14400000)
        self.assertLessEqual(out['continuous']['base']['report']['last_positions']['BTCUSDT'], .00000101)

    def test_invalid_source_gap_or_short_data_refused(self):
        for fault in ('source', 'gap', 'short', 'nan'):
            data = snapshot()
            if fault == 'source': data['source'] = 'unknown'
            if fault == 'gap': data['market']['BTCUSDT'].pop(600)
            if fault == 'short': data = snapshot(n=600)
            if fault == 'nan': data['market']['BTCUSDT'][600][4] = 'nan'
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                walkforward(data)

class MatrixEvidenceTests(unittest.TestCase):
    def test_missing_cases_and_existing_output_are_preserved(self):
        import json
        import tempfile
        from pathlib import Path
        from scripts.quant_cost_aware_walkforward import run_matrix
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'evidence'
            def fail(*args, **kwargs):
                self.assertTrue((root/'frozen_plan.json').is_file())
                raise RuntimeError('source unavailable')
            result = run_matrix(root, capture=fail)
            self.assertEqual(len(result['cases']), 12)
            self.assertEqual(result['completed_cases'], 0)
            self.assertEqual(result['status'], 'INCOMPLETE')
            self.assertEqual(json.loads((root/'matrix.json').read_text()), result)
            with self.assertRaises(ValueError):
                run_matrix(root, capture=fail)

class ActivePolicyCausalityTests(unittest.TestCase):
    def test_future_mutation_preserves_actual_earlier_fills(self):
        data = snapshot(trend='up')
        changed = copy.deepcopy(data)
        for row in changed['market']['BTCUSDT'][700:]:
            row[1:5] = [str(float(v)*.4) for v in row[1:5]]
        with patch('econith.quant.cost_aware_walkforward.choose_candidate', return_value='trend_vol_v1'):
            a, b = walkforward(data), walkforward(changed)
        limit = 700 * 14400000
        first = a['continuous']['base']['report']['fills']
        second = b['continuous']['base']['report']['fills']
        self.assertTrue(any(f['timestamp_ms'] < limit for f in first))
        self.assertEqual([f for f in first if f['timestamp_ms'] < limit],
                         [f for f in second if f['timestamp_ms'] < limit])
        self.assertNotEqual(a['continuous']['base']['net_return_pct'],
                            b['continuous']['base']['net_return_pct'])

    def test_passive_is_one_entry_one_exit_with_both_costs(self):
        out = walkforward(snapshot())
        for cost in ('base', 'stress3x', 'stress5x'):
            passive = out['passive_buyhold'][cost]
            self.assertEqual(passive['fill_legs'], 2)
            self.assertLess(passive['net_return_pct'], 0)
            self.assertEqual([f['side'] for f in passive['report']['fills']], ['BUY', 'SELL'])
