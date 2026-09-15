"""Kiểm thử nhân quả thời gian của tín hiệu công bố."""
import unittest
import numpy as np
import pandas as pd
from tests.test_rq1_v2 import fixture
from KHKT_Evaluation.rq1_v2.data import prepare
from KHKT_Evaluation.rq1_v2.innovations import release_features, enrich
from KHKT_Evaluation.rq1_v2.world import replay
from KHKT_Evaluation.rq1_v2.run import evaluate


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
