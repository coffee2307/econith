"""Kiểm thử nhân quả thời gian của tín hiệu công bố."""
import unittest
import numpy as np
import pandas as pd
from tests.test_rq1_v2 import fixture
from KHKT_Evaluation.rq1_v2.data import prepare
from KHKT_Evaluation.rq1_v2.innovations import release_features, enrich, causal_world_features
from KHKT_Evaluation.rq1_v2.world import replay
from KHKT_Evaluation.rq1_v2.run import evaluate
from KHKT_Evaluation.rq1_v2.model import select_predict


class TestInnovations(unittest.TestCase):
    def test_future_does_not_change_past(self):
        m, r, c = fixture()
        p, _ = prepare(m, r, c)
        a = release_features(p, c['macro_features'])
        q = p.copy()
        q.loc['2023':, 'inflation'] *= 2
        b = release_features(q, c['macro_features'])
        pd.testing.assert_frame_equal(a.loc[:'2022'], b.loc[:'2022'])

    def test_pulse_only_at_release(self):
        m, r, c = fixture()
        p, _ = prepare(m, r, c)
        a = release_features(p, c['macro_features'])
        fresh = p.inflation__release.ne(p.inflation__release.shift())
        self.assertTrue((a.loc[~fresh, 'inflation_innovation'] == 0).all())
        self.assertTrue(np.isfinite(a.to_numpy()).all())

    def test_counterfactual_repeatability(self):
        m, r, c = fixture()
        p, _ = prepare(m, r, c)
        p = p.iloc[:80]
        c['reaction_daily_scale'] = 1 / 30
        a = enrich(p, replay(p, c), c)
        pd.testing.assert_frame_equal(a, enrich(p, replay(p, c), c))
        self.assertEqual(len(a.columns), 20)

    def test_enhanced_pipeline(self):
        m, r, c = fixture()
        c.update(release_innovations=True, reaction_daily_scale=1 / 30)
        result, predictions, controls = evaluate(m, r, c)
        self.assertTrue(np.isfinite(controls).all())
        self.assertFalse(result['h1_confirmed'])
        self.assertIn('candidates', result['folds'][0]['selected']['E1'])
        self.assertEqual(controls.shape, (200, len(predictions)))

    def test_causal_world_ignores_future_releases(self):
        m, r, c = fixture()
        p, _ = prepare(m, r, c)
        c.update(reaction_daily_scale=1 / 30, impulse_simulation_days=7)
        a = causal_world_features(p, c)
        q = p.copy()
        q.loc['2023':, 'inflation'] *= 2
        b = causal_world_features(q, c)
        pd.testing.assert_frame_equal(a.loc[:'2022'], b.loc[:'2022'])
        self.assertTrue(np.isfinite(a.to_numpy()).all())

    def test_causal_pipeline_is_repeatable(self):
        m, r, c = fixture()
        c.update(causal_world_impulses=True, reaction_daily_scale=1 / 30,
                 impulse_simulation_days=7, impulse_half_life_days=7,
                 validation_blocks=3, required_validation_wins=3,
                 min_validation_improvement=.005, shrinkages=[.25, .5, 1.])
        a, frame, controls = evaluate(m, r, c)
        b, again, other_controls = evaluate(m, r, c)
        self.assertEqual(a['metrics'], b['metrics'])
        pd.testing.assert_frame_equal(frame, again)
        np.testing.assert_array_equal(controls, other_controls)
        self.assertEqual(len(a['folds'][0]['world_features']), 5)

    def test_stability_gate_rejects_one_block_signal(self):
        rng = np.random.default_rng(19)
        x = rng.normal(size=(150, 1))
        b0 = np.ones(150)
        y = np.ones(150)
        train = np.arange(150) < 50
        valid = (np.arange(150) >= 50) & (np.arange(150) < 110)
        test = np.arange(150) >= 110
        y[train] += .2 * x[train, 0]
        valid_rows = np.flatnonzero(valid)
        y[valid_rows[:20]] += .2 * x[valid_rows[:20], 0]
        y[valid_rows[20:]] -= .2 * x[valid_rows[20:], 0]
        _, selected = select_predict(
            x, y, b0, train, valid, test, [0.01], validation_blocks=3,
            required_validation_wins=3, min_relative_gain=.005)
        self.assertFalse(selected['active'])
        self.assertEqual(selected['candidates'][0]['block_wins'], 1)
