import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from KHKT_Evaluation.rq1_v3 import data, fetch_macro, fetch_market, models, world, statistics
from KHKT_Evaluation.rq1_v3.run import evaluate


def fixture():
    times = pd.date_range('2020-01-01', periods=500, freq='B', tz='UTC')
    rng = np.random.default_rng(72)
    market = pd.DataFrame({'asset':'TEST', 'time':times,
                          'adjusted_close':100*np.exp(np.cumsum(rng.normal(0,.01,len(times))))})
    rows = []
    for country in ('US','JP'):
        for feature in data.FEATURES:
            for i,t in enumerate(times[::15]):
                rows.append(dict(country=country,feature=feature,observation_at=t,
                                 available_at=t,value=.03+.005*np.sin(i),unit='fraction',source='synthetic software fixture',quality='observed'))
    stamp=lambda i: times[i].isoformat()
    cfg=dict(mode='exploratory',countries=['US','JP'],assets={'TEST':[.5,.5]},
             horizon_sessions=5, paths=3,rollout_steps=2,control_repeats=200,bootstrap_repeats=200,
             alphas=[1.],weights=[.25],validation_wins=2,min_gain=.001,seed=42,
             network=[[0,.2],[.1,0]],network_source='synthetic fixture',network_available_at='2019-01-01T00:00:00Z',
             market_source='synthetic fixture',calendar_audit='business-day fixture only',
             release_provenance='synthetic fixture',data_use_history='software testing only',
             folds=[dict(train_start=stamp(0),validation_start=stamp(160),test_start=stamp(240),test_end=stamp(330)),
                    dict(train_start=stamp(0),validation_start=stamp(240),test_start=stamp(330),test_end=stamp(490))])
    return market,pd.DataFrame(rows),cfg


class TestRQ1V3(unittest.TestCase):
    def test_market_collectors_parse_adjusted_and_crypto_close(self):
        dates=pd.date_range('2020-01-01 17:00:00Z',periods=100,freq='D')
        payload={'chart':{'error':None,'result':[{
            'timestamp':[int(x.timestamp()) for x in dates],
            'indicators':{'adjclose':[{'adjclose':[100.+i for i in range(100)]}]}}]}}
        rows=fetch_market.yahoo_prices('SPY','2020-01-01','2020-04-30',lambda _:payload)
        self.assertEqual(len(rows),100); self.assertEqual(rows[0][1],100.)

        start=pd.Timestamp('2020-01-01',tz='UTC')
        batch=[]
        for i in range(100):
            opened=int((start+pd.Timedelta(days=i)).timestamp()*1000)
            closed=opened+86_400_000-1
            batch.append([opened,'0','0','0',str(200+i),'0',closed])
        calls=[]
        def requester(_):
            calls.append(1)
            return batch if len(calls)==1 else []
        market,sessions=fetch_market.binance_prices('BTCUSDT','2020-01-01','2020-12-31',requester)
        self.assertEqual(len(market),100); self.assertEqual(market[0]['time'],sessions[0]['time'])

    def test_macro_collector_adds_country_and_quality(self):
        def normalized(feature, series, transform, observations, start):
            return [{'available_at':'2020-02-02T00:00:00+00:00',
                     'observation_at':'2020-01-01T00:00:00+00:00',
                     'feature':feature,'value':.01,'unit':'fraction','source':series}]
        with patch.object(fetch_macro,'request_series',return_value=[{}]), \
             patch.object(fetch_macro,'normalize',side_effect=normalized):
            frame=fetch_macro.collect('x','2020-01-01','2020-12-31',pause=0)
        self.assertEqual(len(frame),8)
        self.assertEqual(set(frame.country),{'US','JP'})
        self.assertEqual(set(frame.quality),{'observed'})

    def test_asset_catalog_covers_distinct_risk_groups(self):
        groups={spec[0] for spec in fetch_market.ASSETS.values()}
        self.assertGreaterEqual(len(fetch_market.ASSETS),10)
        self.assertTrue({'gold','oil','usd','crypto_bitcoin','bond_us_long_treasury'} <= groups)

    def test_future_release_does_not_change_past(self):
        market,r,cfg=fixture()
        r=data.releases(r,cfg['countries'])
        before=world.panel_at(market.time,r,cfg['countries'])
        r.loc[r.available_at>market.time.iloc[300],'value']+=10
        after=world.panel_at(market.time,r,cfg['countries'])
        for a,b in zip(before,after): np.testing.assert_allclose(a[:301],b[:301],equal_nan=True)

    def test_reject_naive_and_synthetic_quality(self):
        with self.assertRaises(ValueError): data.utc(['2020-01-01'])
        _,r,cfg=fixture(); r['quality']='synthetic'
        with self.assertRaises(ValueError): data.releases(r,cfg['countries'])

    def test_same_release_time_allows_multiple_observation_periods(self):
        market,r,cfg=fixture()
        mask=(r.country=='US') & (r.feature=='interest_rate')
        ids=r.index[mask][:3]
        shared=r.loc[ids[2],'available_at']
        r.loc[ids[1:],'available_at']=shared
        checked=data.releases(r,cfg['countries'])
        self.assertEqual((checked.loc[(checked.country=='US') &
                                     (checked.feature=='interest_rate'),
                                     'available_at']==shared).sum(),2)
        levels,_=world.panel_at(market.time,checked,cfg['countries'])
        position=np.flatnonzero(market.time >= shared)[0]
        self.assertAlmostEqual(levels[position,0],r.loc[ids[2],'value'])
        duplicate=pd.concat([r,r.loc[[ids[2]]]],ignore_index=True)
        with self.assertRaises(ValueError): data.releases(duplicate,cfg['countries'])

    def test_purge_and_forward_target(self):
        m,_,cfg=fixture(); p=data.market(m,'TEST',5)
        expected=np.sqrt(np.mean(np.diff(np.log(m.adjusted_close.iloc[30:36]))**2))
        self.assertAlmostEqual(p.target.iloc[30],expected)
        masks=data.masks(p,cfg['folds'][0],np.isfinite(p.target))
        self.assertFalse(np.any(masks[0]&masks[1]))
        self.assertTrue((p.label_end[masks[0]]<pd.Timestamp(cfg['folds'][0]['validation_start'])).all())

    def test_calendar_gap_is_rejected(self):
        m,_,_=fixture(); sessions=m[["asset","time"]]
        data.audit_sessions(m,sessions,["TEST"])
        with self.assertRaises(ValueError): data.audit_sessions(m.drop(index=3),sessions,["TEST"])

    def test_zero_signal_falls_back(self):
        x=np.zeros((120,2)); y=np.linspace(.01,.02,120); base=np.full(120,.015)
        train=np.arange(120)<60; val=(np.arange(120)>=60)&(np.arange(120)<90)
        _,_,cfg=fixture(); pred,meta=models.augment(x,y,base,train,val,cfg)
        np.testing.assert_array_equal(pred,base); self.assertFalse(meta['active'])

    def test_holm_and_identical_bootstrap(self):
        np.testing.assert_allclose(statistics.holm([.01,.04,.03]),[.03,.06,.06])
        x=np.ones((80,3)); self.assertEqual(statistics.interval([x],200,5,42),{'mae':[0.,0.],'rmse':[0.,0.]})

    def test_shuffle_preserves_pairs_and_partitions(self):
        x=np.c_[np.arange(120),np.arange(120)*2]
        masks=[(np.arange(120)>=i)&(np.arange(120)<i+40) for i in (0,40,80)]
        y=statistics.shuffle(x,masks,5,np.random.default_rng(1))
        np.testing.assert_array_equal(y[:,1],2*y[:,0])
        for mask in masks: self.assertEqual(set(y[mask,0]),set(x[mask,0]))

    def test_full_pipeline_and_test_labels(self):
        m,r,cfg=fixture(); first,pred=evaluate(m,r,cfg)
        second,pred2=evaluate(m,r,cfg)
        self.assertEqual(first,second); pd.testing.assert_frame_equal(pred,pred2)
        self.assertFalse(first['h1_confirmed']); self.assertTrue(np.isfinite(pred.select_dtypes('number')).all().all())
        # Model selection cannot read labels from the test partition.
        p=data.market(m,'TEST',5); levels,events=world.panel_at(p.index,data.releases(r,cfg['countries']),cfg['countries'])
        masks=data.masks(p,cfg['folds'][0],np.isfinite(np.c_[levels,events,p.target,p.log_var_22]).all(1))
        train,val,test=masks; x=p[['log_var_1','log_var_5','log_var_22']].to_numpy(); y=p.target.to_numpy()
        b,info=models.har(x,y,train,val,cfg['alphas']); y2=y.copy(); y2[test]*=100
        b2,info2=models.har(x,y2,train,val,cfg['alphas']); np.testing.assert_allclose(b,b2,equal_nan=True)
        a,ai=models.augment(levels,y,b,train,val,cfg); a2,ai2=models.augment(levels,y2,b2,train,val,cfg)
        self.assertEqual(ai,ai2); np.testing.assert_allclose(a,a2,equal_nan=True)

    def test_multiasset_and_cli(self):
        m,r,cfg=fixture()
        other=m.copy(); other["asset"]="SECOND"; other["adjusted_close"]*=2
        m=pd.concat([m,other],ignore_index=True); cfg["assets"]["SECOND"]=[1.,0.]
        result,pred=evaluate(m,r,cfg)
        self.assertEqual(set(result["assets"]),{"TEST","SECOND"})
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); m.to_csv(root/"market.csv",index=False); r.to_csv(root/"releases.csv",index=False)
            m[["asset","time"]].to_csv(root/"sessions.csv",index=False)
            (root/"config.json").write_text(json.dumps(cfg),encoding="utf-8")
            cmd=[sys.executable,"-m","KHKT_Evaluation.rq1_v3.run"]
            for arg in ("market","releases","sessions","config"):
                cmd.extend(["--"+arg,str(root/(arg+(".json" if arg=="config" else ".csv")))])
            cmd.extend(["--output",str(root/"out")])
            env=os.environ.copy(); env['PYTHONIOENCODING']='cp1258:strict'
            run=subprocess.run(cmd,capture_output=True,env=env)
            self.assertEqual(run.returncode,0,run.stderr.decode('ascii',errors='backslashreplace'))
            saved=(root/"out"/"metrics.json").read_bytes()
            self.assertNotEqual(subprocess.run(cmd,capture_output=True,env=env).returncode,0)
            self.assertEqual(saved,(root/"out"/"metrics.json").read_bytes())

    def test_missing_country_and_future_network_blocked(self):
        m,r,cfg=fixture(); r=r[r.country=='US']
        with self.assertRaises(ValueError): evaluate(m,r,cfg)
        m,r,cfg=fixture(); cfg['network_available_at']='2025-01-01T00:00:00Z'
        with self.assertRaises(ValueError): evaluate(m,r,cfg)

if __name__=='__main__': unittest.main()
