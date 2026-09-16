"""Chạy v3 thăm dò; chặn xác nhận khi dữ liệu/World chưa được nghiệm thu."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import numpy as np
import pandas as pd
from . import data, models, world, statistics
from KHKT_Evaluation.common.metrics import diebold_mariano


def validate(cfg):
    if cfg.get('mode') != 'exploratory':
        raise ValueError('V3 hiện chỉ được nghiệm thu thăm dò; chưa cho phép confirmatory.')
    if len(cfg['countries']) < 2 or len(set(cfg['countries'])) != len(cfg['countries']):
        raise ValueError('Cần ít nhất hai quốc gia khác nhau.')
    if cfg['horizon_sessions'] < 2 or cfg['paths'] < 2 or cfg['rollout_steps'] < 1:
        raise ValueError('Horizon, paths hoặc rollout không hợp lệ.')
    if cfg['control_repeats'] < 200 or cfg['bootstrap_repeats'] < 200:
        raise ValueError('Cần ít nhất 200 lần đối chứng và bootstrap.')
    if not cfg['alphas'] or any(not np.isfinite(a) or a <= 0 for a in cfg['alphas']):
        raise ValueError('Alpha phải dương và hữu hạn.')
    if not cfg['weights'] or any(not np.isfinite(w) or not 0 < w <= 1 for w in cfg['weights']):
        raise ValueError('Trọng số phối hợp phải thuộc (0,1].')
    if not 1 <= cfg['validation_wins'] <= 3 or not np.isfinite(cfg['min_gain']) or cfg['min_gain'] < 0:
        raise ValueError('Cổng validation không hợp lệ.')
    if len(cfg['folds']) < 2:
        raise ValueError('Cần ít nhất hai fold.')
    previous = None
    for fold in cfg['folds']:
        start = pd.Timestamp(fold['test_start'])
        end = pd.Timestamp(fold['test_end'])
        if previous is not None and start < previous:
            raise ValueError('Test fold chồng lấn.')
        previous = end
    world.graph(cfg)
    known = pd.Timestamp(cfg["network_available_at"])
    if known.tzinfo is None or known > min(pd.Timestamp(f["train_start"]) for f in cfg["folds"]):
        raise ValueError("Mạng phải được biết trước mọi fold huấn luyện.")
    if not cfg["assets"]:
        raise ValueError("Cần ít nhất một tài sản.")
    for key in ('market_source', 'calendar_audit', 'release_provenance', 'data_use_history'):
        if not cfg.get(key):
            raise ValueError(f'Cần khai báo {key}.')


def evaluate(market, releases, cfg):
    validate(cfg)
    releases = data.releases(releases, cfg['countries'])
    result, frames = {}, []
    dm_rows = []
    h = cfg['horizon_sessions']
    for asset, exposure in cfg['assets'].items():
        panel = data.market(market,asset,h)
        levels, events = world.panel_at(panel.index,releases,cfg['countries'])
        market_x = panel[['log_var_1','log_var_5','log_var_22']].to_numpy()
        y = panel.target.to_numpy()
        usable = np.isfinite(np.c_[levels,events,market_x,y]).all(1)
        diagnostics, parts, controls = [], [], []
        for number, fold in enumerate(cfg['folds']):
            train,val,test = data.masks(panel,fold,usable)
            static,dynamic,meta = world.features(levels,events,train,cfg,exposure)
            _,isolated,_ = world.features(levels,events,train,cfg,exposure,linked=False)
            base,base_info = models.har(market_x,y,train,val,cfg['alphas'])
            b1,b1info = models.augment(levels,y,base,train,val,cfg)
            st,stinfo = models.augment(static,y,base,train,val,cfg)
            e1,dyinfo = models.augment(dynamic,y,st,train,val,cfg)
            c2,_ = models.augment(isolated,y,st,train,val,cfg)
            f = pd.DataFrame({'time':panel.index[test], 'asset':asset,'fold':number,
                              'target':y[test],'B0':base[test], 'B1':b1[test],
                              'E_static':st[test], 'E1':e1[test], 'C2':c2[test]})
            parts.append(f)
            joined = np.c_[static,dynamic]
            random_predictions = []
            for repeat in range(cfg['control_repeats']):
                rng = np.random.default_rng(np.random.SeedSequence([cfg['seed'],number,repeat]))
                shuffled = statistics.shuffle(joined,(train,val,test),h,rng)
                cst,_ = models.augment(shuffled[:,:static.shape[1]],y,base,train,val,cfg)
                cp,_ = models.augment(shuffled[:,static.shape[1]:],y,cst,train,val,cfg)
                random_predictions.append(cp[test])
            controls.append(np.asarray(random_predictions))
            dm = diebold_mariano(y[test],e1[test],base[test],h=h,loss='abs')
            dm_rows.append({'asset':asset,'fold':number,**dm})
            diagnostics.append({'fold':number,'counts':[int(m.sum()) for m in (train,val,test)],
                                'B0':base_info,'B1':b1info,'static':stinfo,'dynamic':dyinfo,'world':meta})
        frame = pd.concat(parts,ignore_index=True)
        frames.append(frame)
        control = np.concatenate(controls,axis=1)
        metrics = {name:statistics.score(frame.target.to_numpy(),frame[name].to_numpy())
                   for name in ('B0','B1','E_static','E1','C2')}
        c_scores = [statistics.score(frame.target.to_numpy(),row) for row in control]
        summary = {k:{'median':float(np.median([s[k] for s in c_scores])),
                       'share_not_worse':float(np.mean([s[k]<=metrics['E1'][k] for s in c_scores]))}
                   for k in ('mae','rmse')}
        groups = [f[['target','B0','E1']].to_numpy() for f in parts]
        ci = statistics.interval(groups,cfg['bootstrap_repeats'],h,cfg['seed'])
        sensitivity = {}
        gain = abs(frame.target-frame.B0)-abs(frame.target-frame.E1)
        for k in (0,1,2,5,10):
            f = frame.drop(gain.nlargest(k).index) if k else frame
            sensitivity[str(k)] = {name:statistics.score(f.target.to_numpy(),f[name].to_numpy())
                                   for name in ('B0','E1')}
        result[asset] = {'metrics':metrics,'C1':summary,'ci95_E1_minus_B0':ci,
                         'folds':diagnostics,'remove_best_days':sensitivity,
                         'controls':control.tolist()}
    ps = [row['p_value_two_sided'] for row in dm_rows]
    for row,p in zip(dm_rows,statistics.holm(ps)):
        row['p_holm'] = p
    return {'protocol':'rq1_v3_observed_network', 'mode':'exploratory','assets':result,
            'dm':dm_rows,'h1_confirmed':False,
            'limitations':['World là mạng trạng thái quan sát rút gọn, chưa phải kernel đa tác nhân đầy đủ.',
                           'Chưa có consensus, vintage đa quốc gia đã kiểm chứng hoặc xác suất kịch bản hiệu chỉnh.',
                           'C2 tắt mạng lan truyền nhưng vẫn giữ các trạng thái tĩnh quốc tế.',
                           'HAR dùng bình phương lợi suất ngày; chưa có realized variance trong ngày.',
                           'Thống kê được báo cáo theo tài sản; chưa kiểm định tổng hợp phụ thuộc chéo.']},pd.concat(frames,ignore_index=True)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('market','releases','sessions','config','output'):
        parser.add_argument('--'+name,required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists():
        parser.error('Output đã tồn tại; chọn thư mục mới.')
    try:
        cfg=json.loads(args.config.read_text(encoding='utf-8'))
        market = pd.read_csv(args.market)
        data.audit_sessions(market, pd.read_csv(args.sessions), cfg['assets'])
        result,pred=evaluate(market,pd.read_csv(args.releases),cfg)
        result['provenance']={'config':cfg,'input_sha256':{name:digest(getattr(args,name)) for name in ('market','releases','sessions','config')},
                              'python':platform.python_version(),'numpy':np.__version__,'pandas':pd.__version__,
                              'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()}
        encoded=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)
    except (ValueError,KeyError,FileNotFoundError) as exc:
        parser.exit(2,f'RQ1 v3 bị chặn: {exc}\n')
    args.output.mkdir(parents=True,exist_ok=False)
    pred.to_csv(args.output/'predictions.csv',index=False)
    (args.output/'metrics.json').write_text(encoded,encoding='utf-8')
    print(f'Hoàn tất thăm dò: {args.output}. H1 chưa được xác nhận.')


if __name__=='__main__':
    main()
