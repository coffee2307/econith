import unittest

import numpy as np
import pandas as pd

from KHKT_Evaluation.rq1_v4 import models
from KHKT_Evaluation.rq1_v4.run import evaluate, h1_evidence, validate
from tests.test_rq1_v3 import fixture


class TestRQ1V4(unittest.TestCase):
    def test_regime_interactions_use_train_scaling_only(self):
        rng=np.random.default_rng(4)
        world_x=rng.normal(size=(100,3)); market_x=rng.normal(size=(100,3))
        train=np.arange(100)<60
        first=models.regime_interactions(world_x,market_x,train)
        changed=market_x.copy(); changed[60:]+=1000
        second=models.regime_interactions(world_x,changed,train)
        np.testing.assert_allclose(first[:60],second[:60])
        self.assertEqual(first.shape,(100,9))

    def test_ratio_layer_is_positive_and_ignores_test_labels(self):
        x=np.linspace(-1,1,150)[:,None]
        base=np.full(150,.02); y=base*np.exp(.8*x[:,0])
        train=np.arange(150)<80; val=(np.arange(150)>=80)&(np.arange(150)<120)
        cfg={'alphas':[.1,1.],'weights':[.1,.25],
             'validation_blocks':3,'validation_wins':2,'min_gain':0.,'min_block_gain':-1.}
        first,info=models.ratio_augment(x,y,base,train,val,cfg)
        changed=y.copy(); changed[120:]*=100
        second,info2=models.ratio_augment(x,changed,base,train,val,cfg)
        self.assertEqual(info,info2); np.testing.assert_allclose(first,second)
        self.assertTrue(info['active']); self.assertTrue((first>0).all())

    def test_zero_world_layer_returns_exact_baseline(self):
        x=np.zeros((120,5)); y=np.linspace(.01,.02,120); base=np.full(120,.015)
        train=np.arange(120)<60; val=(np.arange(120)>=60)&(np.arange(120)<90)
        _,_,cfg=fixture()
        pred,info=models.ratio_augment(x,y,base,train,val,cfg)
        np.testing.assert_array_equal(pred,base); self.assertFalse(info['active'])

    def test_h1_evidence_requires_increment_over_b1(self):
        metrics={'B0':{'mae':3.,'rmse':4.},'B1':{'mae':2.,'rmse':3.},
                 'E1':{'mae':1.,'rmse':2.}}
        controls={'mae':{'median':1.5,'share_not_worse':.01},
                  'rmse':{'median':2.5,'share_not_worse':.02}}
        ci={'mae':[-.4,-.1],'rmse':[-.5,-.2]}
        self.assertTrue(h1_evidence(metrics,controls,ci,ci)['strong_exploratory_support'])
        metrics['B1']['mae']=.9
        evidence=h1_evidence(metrics,controls,ci,ci)
        self.assertFalse(evidence['lower_than_B1_both'])
        self.assertFalse(evidence['strong_exploratory_support'])

    def test_v4_blocks_the_spent_2026_holdout(self):
        _,_,cfg=fixture(); cfg['folds'][-1]['test_end']='2026-09-01T00:00:00Z'
        with self.assertRaises(ValueError): validate(cfg)

    def test_full_pipeline_is_finite_and_never_confirms(self):
        market,releases,cfg=fixture()
        result,pred=evaluate(market,releases,cfg)
        self.assertEqual(result['protocol'],'rq1_v4_nested_world_regime')
        self.assertFalse(result['h1_confirmed'])
        self.assertTrue(np.isfinite(pred.select_dtypes('number')).all().all())
        self.assertIn('ci95_E1_minus_B1',result['assets']['TEST'])
        self.assertEqual(np.asarray(result['assets']['TEST']['controls']).shape[0],
                         cfg['control_repeats'])


if __name__=='__main__':
    unittest.main()
