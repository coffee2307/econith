"""Chạy RQ1 v4 thăm dò: World phải tạo giá trị tăng thêm trên B1."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys

import numpy as np
import pandas as pd

from KHKT_Evaluation.common.metrics import diebold_mariano
from KHKT_Evaluation.rq1_v3 import data, statistics, world
from KHKT_Evaluation.rq1_v3.run import validate as validate_v3
from . import models


def validate(cfg):
    if cfg.get('mode') != 'exploratory':
        raise ValueError('RQ1 v4 chỉ là nhánh phát triển thăm dò; không được gọi là confirmatory.')
    validate_v3(cfg)
    if max(pd.Timestamp(f['test_end']) for f in cfg['folds']) > pd.Timestamp('2026-01-01T00:00:00Z'):
        raise ValueError('RQ1 v4 không được đọc holdout GLD 2026 đã dùng để xác nhận v3.3.')


def h1_evidence(metrics,controls,ci_b0,ci_b1):
    lower_b0=all(metrics['E1'][k]<metrics['B0'][k] for k in ('mae','rmse'))
    lower_b1=all(metrics['E1'][k]<metrics['B1'][k] for k in ('mae','rmse'))
    lower_c1=all(metrics['E1'][k]<controls[k]['median'] for k in ('mae','rmse'))
    ci0=all(ci_b0[k][1]<0 for k in ('mae','rmse'))
    ci1=all(ci_b1[k][1]<0 for k in ('mae','rmse'))
    tail=all(controls[k]['share_not_worse']<=.05 for k in ('mae','rmse'))
    strong=lower_b0 and lower_b1 and lower_c1 and ci0 and ci1 and tail
    return {'lower_than_B0_both':lower_b0,'lower_than_B1_both':lower_b1,
            'lower_than_C1_median_both':lower_c1,
            'ci95_E1_minus_B0_below_zero_both':ci0,
            'ci95_E1_minus_B1_below_zero_both':ci1,
            'C1_share_not_worse_at_most_0_05_both':tail,
            'strong_exploratory_support':strong}


def evaluate(market,releases,cfg):
    validate(cfg)
    releases=data.releases(releases,cfg['countries'])
    result,frames,dm_rows={},[],[]
    h=cfg['horizon_sessions']
    for asset,exposure in cfg['assets'].items():
        panel=data.market(market,asset,h)
        levels,events=world.panel_at(panel.index,releases,cfg['countries'])
        market_x=panel[['log_var_1','log_var_5','log_var_22']].to_numpy()
        y=panel.target.to_numpy()
        usable=np.isfinite(np.c_[levels,events,market_x,y]).all(1)
        diagnostics,parts,controls=[],[],[]
        for number,fold in enumerate(cfg['folds']):
            train,val,test=data.masks(panel,fold,usable)
            static,dynamic,meta=world.features(levels,events,train,cfg,exposure)
            _,isolated,_=world.features(levels,events,train,cfg,exposure,linked=False)
            static_x=models.regime_interactions(static,market_x,train)
            dynamic_x=models.regime_interactions(dynamic,market_x,train)
            isolated_x=models.regime_interactions(isolated,market_x,train)

            # Chuỗi lồng nhau: B0 -> B1 -> E_static -> E1.
            base_select,base_info=models.har(market_x,y,train,val,cfg['alphas'])
            b1_select,b1info=models.ratio_augment(levels,y,base_select,train,val,cfg)
            st_select,stinfo=models.ratio_augment(static_x,y,b1_select,train,val,cfg)
            _,dyinfo=models.ratio_augment(dynamic_x,y,st_select,train,val,cfg)
            _,c2info=models.ratio_augment(isolated_x,y,st_select,train,val,cfg)

            development=train|val
            base=models.refit_har(market_x,y,development,base_info)
            b1=models.refit_ratio_augment(levels,y,base,development,b1info)
            st=models.refit_ratio_augment(static_x,y,b1,development,stinfo)
            e1=models.refit_ratio_augment(dynamic_x,y,st,development,dyinfo)
            c2=models.refit_ratio_augment(isolated_x,y,st,development,c2info)
            f=pd.DataFrame({'time':panel.index[test],'asset':asset,'fold':number,
                            'target':y[test],'B0':base[test],'B1':b1[test],
                            'E_static':st[test],'E1':e1[test],'C2':c2[test]})
            parts.append(f)

            # C1 giữ nguyên B1 và chỉ xáo trộn phần thông tin do World tạo ra.
            joined=np.c_[static,dynamic]
            random_predictions=[]
            for repeat in range(cfg['control_repeats']):
                rng=np.random.default_rng(np.random.SeedSequence([cfg['seed'],number,repeat]))
                shuffled=statistics.shuffle(joined,(train,val,test),h,rng)
                cstatic=models.regime_interactions(shuffled[:,:static.shape[1]],market_x,train)
                cdynamic=models.regime_interactions(shuffled[:,static.shape[1]:],market_x,train)
                cst_select,cstinfo=models.ratio_augment(cstatic,y,b1_select,train,val,cfg)
                _,cdyninfo=models.ratio_augment(cdynamic,y,cst_select,train,val,cfg)
                cst=models.refit_ratio_augment(cstatic,y,b1,development,cstinfo)
                cp=models.refit_ratio_augment(cdynamic,y,cst,development,cdyninfo)
                random_predictions.append(cp[test])
            controls.append(np.asarray(random_predictions))

            for baseline,prediction in (('B0',base),('B1',b1),('E_static',st)):
                dm=diebold_mariano(y[test],e1[test],prediction[test],h=h,loss='abs')
                dm_rows.append({'asset':asset,'fold':number,'baseline':baseline,**dm})
            diagnostics.append({'fold':number,'counts':[int(m.sum()) for m in (train,val,test)],
                                'B0':base_info,'B1':b1info,'static':stinfo,
                                'dynamic':dyinfo,'world':meta,
                                'regime_channels':['base','market_stress','market_trend']})

        frame=pd.concat(parts,ignore_index=True); frames.append(frame)
        control=np.concatenate(controls,axis=1)
        metrics={name:statistics.score(frame.target.to_numpy(),frame[name].to_numpy())
                 for name in ('B0','B1','E_static','E1','C2')}
        c_scores=[statistics.score(frame.target.to_numpy(),row) for row in control]
        summary={k:{'median':float(np.median([s[k] for s in c_scores])),
                    'share_not_worse':float(np.mean([s[k]<=metrics['E1'][k] for s in c_scores]))}
                 for k in ('mae','rmse')}
        groups_b0=[f[['target','B0','E1']].to_numpy() for f in parts]
        groups_b1=[f[['target','B1','E1']].to_numpy() for f in parts]
        ci_b0=statistics.interval(groups_b0,cfg['bootstrap_repeats'],h,cfg['seed'])
        ci_b1=statistics.interval(groups_b1,cfg['bootstrap_repeats'],h,cfg['seed']+1)
        sensitivity={}
        gain=abs(frame.target-frame.B1)-abs(frame.target-frame.E1)
        for k in (0,1,2,5,10):
            f=frame.drop(gain.nlargest(k).index) if k else frame
            sensitivity[str(k)]={name:statistics.score(f.target.to_numpy(),f[name].to_numpy())
                                 for name in ('B0','B1','E1')}
        evidence=h1_evidence(metrics,summary,ci_b0,ci_b1)
        result[asset]={'metrics':metrics,'C1':summary,
                       'ci95_E1_minus_B0':ci_b0,'ci95_E1_minus_B1':ci_b1,
                       'h1_evidence':evidence,'folds':diagnostics,
                       'remove_best_days_vs_B1':sensitivity,'controls':control.tolist()}

    ps=[row['p_value_two_sided'] for row in dm_rows]
    for row,p in zip(dm_rows,statistics.holm(ps)):
        row['p_holm']=p
    supported=[a for a,v in result.items() if v['h1_evidence']['strong_exploratory_support']]
    return {'protocol':'rq1_v4_nested_world_regime','mode':'exploratory','assets':result,
            'dm':dm_rows,'h1_exploratory_supported':bool(supported),
            'h1_supported_assets':supported,'h1_confirmed':False,
            'h1_confirmation_blocker':('RQ1 v4 được phát triển sau khi xem dữ liệu đến 2026-08; '
                                       'chỉ được đánh giá thăm dò trên dữ liệu trước 2026.'),
            'limitations':['E1 v4 lồng B1 để đo giá trị tăng thêm của World; không thay đổi kết quả v3.3.',
                           'Tương tác chế độ thị trường được khóa bằng train nhưng vẫn là thiết kế thăm dò.',
                           'World vẫn là surrogate trạng thái đa thang, chưa phải kernel đa tác nhân đầy đủ.',
                           'Cần holdout thời gian mới sau ngày khóa để xác nhận v4.']},pd.concat(frames,ignore_index=True)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def configure_console():
    for stream in (sys.stdout,sys.stderr):
        reconfigure=getattr(stream,'reconfigure',None)
        if reconfigure is not None:
            reconfigure(errors='backslashreplace')


def main():
    configure_console()
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('market','releases','sessions','config','output'):
        parser.add_argument('--'+name,required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists():
        parser.error('Output đã tồn tại; chọn thư mục mới.')
    try:
        cfg=json.loads(args.config.read_text(encoding='utf-8'))
        market=pd.read_csv(args.market)
        data.audit_sessions(market,pd.read_csv(args.sessions),cfg['assets'])
        result,pred=evaluate(market,pd.read_csv(args.releases),cfg)
        result['provenance']={'config':cfg,
          'input_sha256':{name:digest(getattr(args,name)) for name in ('market','releases','sessions','config')},
          'python':platform.python_version(),'numpy':np.__version__,'pandas':pd.__version__,
          'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()}
        encoded=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)
    except (ValueError,KeyError,FileNotFoundError) as exc:
        parser.exit(2,f'RQ1 v4 bị chặn: {exc}\n')
    args.output.mkdir(parents=True,exist_ok=False)
    pred.to_csv(args.output/'predictions.csv',index=False)
    (args.output/'metrics.json').write_text(encoded,encoding='utf-8')
    assets=', '.join(result['h1_supported_assets']) or 'không có'
    print(f'Hoàn tất RQ1 v4 thăm dò: {args.output}. E1 vượt B0, B1 và C1 theo quy tắc mạnh: {assets}. '
          'Không thay đổi kết luận holdout v3.3.')


if __name__=='__main__':
    main()
