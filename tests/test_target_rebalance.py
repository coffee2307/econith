"""Target reductions and cost bands must affect the actual simulated book."""
from dataclasses import replace
import unittest

from econith.quant.paper_audit import audit_paper_report
from econith.quant.paper_v1 import Candle, PaperConfig, SymbolRules, replay_spot


def replay(weights, *, band=.01, fee=0, prices=None):
    prices = prices or [100.] * 12
    bars = [Candle(i * 3600000, (i + 1) * 3600000 - 1,
                   p, p, p, p, 10.) for i, p in enumerate(prices)]
    cfg = PaperConfig(initial_cash=1000, lookback=2, max_asset_weight=.2,
                      max_total_weight=.2, fee_bps=fee, slippage_bps=0,
                      half_spread_bps=0, target_rebalance=True,
                      rebalance_band=band)
    out = replay_spot({'A': bars}, {'A': SymbolRules('.000001', 1)}, cfg,
                      signal_policy=lambda s, h: weights.get(len(h), .1))
    assert audit_paper_report(out.to_dict())['status'] == 'SIMULATED_LEDGER_RECONCILED'
    return out


class TargetRebalanceTests(unittest.TestCase):
    def test_positive_target_reduction_sells_old_risk(self):
        out = replay({3: .2})
        self.assertEqual([f.side for f in out.fills], ['BUY', 'SELL'])
        self.assertAlmostEqual(out.last_positions['A'], 1.)

    def test_band_suppresses_small_changes_but_zero_target_exits(self):
        out = replay({3: .105, 4: .1, **{i: 0 for i in range(5, 12)}})
        self.assertEqual([f.side for f in out.fills], ['BUY', 'SELL'])
        self.assertEqual(out.fills[1].timestamp_ms, 5 * 3600000)
        self.assertEqual(out.last_positions['A'], 0)

    def test_fees_paid_on_both_partial_fill_legs(self):
        out = replay({3: .2}, fee=30)
        self.assertEqual(len(out.fills), 2)
        self.assertLess(out.final_equity, 1000)
        for f in out.fills:
            self.assertAlmostEqual(f.fee, f.price * f.quantity * .003)

    def test_gap_uses_open_equity_for_exposure_cap(self):
        out = replay({i: .2 for i in range(3, 12)}, prices=[100.] * 4 + [200.] * 8)
        at_gap = out.equity_curve[4]
        self.assertLessEqual(at_gap['exposure'] / at_gap['equity'], .2 + 1e-6)

    def test_invalid_band_refused(self):
        for band in [-.01, .21, float('nan'), float('inf'), True]:
            with self.subTest(band=band), self.assertRaises(ValueError):
                replace(PaperConfig(), target_rebalance=True, rebalance_band=band)
