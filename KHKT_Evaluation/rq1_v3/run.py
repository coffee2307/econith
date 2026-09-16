"""Chạy RQ1 v3 ở chế độ thăm dò hoặc theo giao thức xác nhận đã khóa."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import numpy as np
import pandas as pd
from . import data, models, world, statistics
from KHKT_Evaluation.common.metrics import diebold_mariano


def validate(cfg):
    mode = cfg.get('mode')
    if mode not in ('exploratory', 'confirmatory'):
        raise ValueError('mode phải là exploratory hoặc confirmatory.')
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
    blocks = cfg.get('validation_blocks', 3)
    half_lives = cfg.get('impulse_half_lives', [3., 7., 21.])
    if (not isinstance(blocks, int) or blocks < 2 or
            not 1 <= cfg['validation_wins'] <= blocks or
            not np.isfinite(cfg['min_gain']) or cfg['min_gain'] < 0 or
            not np.isfinite(cfg.get('min_block_gain', -1.))):
        raise ValueError('Cổng validation không hợp lệ.')
    if (not half_lives or any(not np.isfinite(x) or x <= 0 for x in half_lives) or
            len(set(float(x) for x in half_lives)) != len(half_lives)):
        raise ValueError('Chu kỳ bán rã của xung World không hợp lệ.')
    if mode == 'exploratory' and len(cfg['folds']) < 2:
        raise ValueError('Thăm dò cần ít nhất hai fold.')
    if mode == 'confirmatory':
        confirmation = cfg.get('confirmation', {})
        required = {'primary_assets', 'development_cutoff', 'holdout_end',
                    'minimum_test_rows', 'locked_at', 'decision_rule'}
        if required - set(confirmation):
            raise ValueError(f'Giao thức xác nhận thiếu {sorted(required-set(confirmation))}.')
        primary = confirmation['primary_assets']
        if (not isinstance(primary, list) or not primary or len(primary) != len(set(primary)) or
                set(primary) != set(cfg['assets'])):
            raise ValueError('Confirmatory chỉ được chứa đúng các tài sản chính đã đăng ký.')
        if len(cfg['folds']) != 1:
            raise ValueError('Confirmatory dùng đúng một holdout khóa, không chia để lựa kết quả.')
        cutoff = pd.Timestamp(confirmation['development_cutoff'])
        holdout_end = pd.Timestamp(confirmation['holdout_end'])
        locked_at = pd.Timestamp(confirmation['locked_at'])
        if (cutoff.tzinfo is None or holdout_end.tzinfo is None or locked_at.tzinfo is None or
                not cutoff < holdout_end):
            raise ValueError('Mốc khóa xác nhận phải có múi giờ và đúng thứ tự.')
        fold = cfg['folds'][0]
        if pd.Timestamp(fold['test_start']) != cutoff or pd.Timestamp(fold['test_end']) != holdout_end:
            raise ValueError('Ranh giới test phải khớp chính xác holdout đã đăng ký.')
        minimum = confirmation['minimum_test_rows']
        if not isinstance(minimum, int) or minimum < 64:
            raise ValueError('Holdout xác nhận cần tối thiểu 64 nhãn đã purge.')
        if confirmation['decision_rule'] != 'strong_support_all_primary_assets':
            raise ValueError('Quy tắc quyết định xác nhận chưa được hỗ trợ.')
    previous = None
    for fold in cfg['folds']:
        start = pd.Timestamp(fold['test_start'])
        end = pd.Timestamp(fold['test_end'])
        if previous is not None and start < previous:
            raise ValueError('Test fold chồng lấn.')
        previous = end
    world.graph(cfg)
    network_mode = cfg.get('network_mode', 'fixed')
    if network_mode not in ('fixed','train_association'):
        raise ValueError('Chế độ mạng không hợp lệ.')
    if mode == 'confirmatory' and network_mode != 'fixed':
        raise ValueError('Confirmatory phải dùng mạng cố định đã khóa trước holdout.')
    if network_mode == 'fixed':
        known = pd.Timestamp(cfg["network_available_at"])
        if known.tzinfo is None or known > min(pd.Timestamp(f["train_start"]) for f in cfg["folds"]):
            raise ValueError("Mạng phải được biết trước mọi fold huấn luyện.")
    else:
        for key, default in (('network_half_life',7.),('network_shrinkage',.25),('network_max_weight',.2)):
            value = cfg.get(key, default)
            if not np.isfinite(value) or value <= 0:
                raise ValueError(f'{key} phải dương và hữu hạn.')
        if cfg.get('network_max_weight',.2) > 1 or cfg.get('network_shrinkage',.25) > 1:
            raise ValueError('Co mạng và trọng số mạng tối đa không được vượt 1.')
    if not cfg["assets"]:
        raise ValueError("Cần ít nhất một tài sản.")
    for key in ('market_source', 'calendar_audit', 'release_provenance', 'data_use_history'):
        if not cfg.get(key):
            raise ValueError(f'Cần khai báo {key}.')


def h1_evidence(metrics, controls, interval):
    """Báo cáo tiêu chí H1; mode quyết định đây là thăm dò hay xác nhận."""
    lower_b0 = all(metrics['E1'][key] < metrics['B0'][key] for key in ('mae','rmse'))
    lower_control_median = all(metrics['E1'][key] < controls[key]['median']
                               for key in ('mae','rmse'))
    ci_below_zero = all(interval[key][1] < 0 for key in ('mae','rmse'))
    random_tail = all(controls[key]['share_not_worse'] <= .05 for key in ('mae','rmse'))
    strong = lower_b0 and lower_control_median and ci_below_zero and random_tail
    return {'lower_than_B0_both': lower_b0,
            'lower_than_C1_median_both': lower_control_median,
            'ci95_difference_below_zero_both': ci_below_zero,
            'C1_share_not_worse_at_most_0_05_both': random_tail,
            'meets_numeric_criteria': lower_b0 and lower_control_median,
            'strong_support': strong,
            'strong_exploratory_support': strong}


def h1_status(result, cfg):
    """Áp quy tắc đã khóa; không chọn tài sản sau khi nhìn holdout."""
    supported = [asset for asset, value in result.items()
                 if value['h1_evidence']['strong_support']]
    if cfg['mode'] == 'exploratory':
        return supported, False, ('Dữ liệu/fold đã được xem trong phát triển exploratory; '
                                  'cần giao thức khóa và holdout chưa xem.')
    primary = cfg['confirmation']['primary_assets']
    confirmed = all(asset in supported for asset in primary)
    blocker = None if confirmed else 'Ít nhất một tài sản chính không đạt quy tắc trên holdout khóa.'
    return supported, confirmed, blocker


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
            if (cfg['mode'] == 'confirmatory' and
                    int(test.sum()) < cfg['confirmation']['minimum_test_rows']):
                raise ValueError(
                    f'{asset} chỉ có {int(test.sum())} nhãn holdout; thấp hơn mức đã khóa.'
                )
            static,dynamic,meta = world.features(levels,events,train,cfg,exposure)
            _,isolated,_ = world.features(levels,events,train,cfg,exposure,linked=False)
            # Chọn cấu hình chỉ bằng train -> validation.
            base_select,base_info = models.har(market_x,y,train,val,cfg['alphas'])
            _,b1info = models.augment(levels,y,base_select,train,val,cfg)
            st_select,stinfo = models.augment(static,y,base_select,train,val,cfg)
            _,dyinfo = models.augment(dynamic,y,st_select,train,val,cfg)
            _,c2info = models.augment(isolated,y,st_select,train,val,cfg)
            # Sau khi khóa lựa chọn, dùng toàn bộ nhãn đã biết trước test để refit.
            development = train | val
            base = models.refit_har(market_x,y,development,base_info)
            b1 = models.refit_augment(levels,y,base,development,b1info)
            st = models.refit_augment(static,y,base,development,stinfo)
            e1 = models.refit_augment(dynamic,y,st,development,dyinfo)
            c2 = models.refit_augment(isolated,y,st,development,c2info)
            f = pd.DataFrame({'time':panel.index[test], 'asset':asset,'fold':number,
                              'target':y[test],'B0':base[test], 'B1':b1[test],
                              'E_static':st[test], 'E1':e1[test], 'C2':c2[test]})
            parts.append(f)
            joined = np.c_[static,dynamic]
            random_predictions = []
            for repeat in range(cfg['control_repeats']):
                rng = np.random.default_rng(np.random.SeedSequence([cfg['seed'],number,repeat]))
                shuffled = statistics.shuffle(joined,(train,val,test),h,rng)
                cst_select,cstinfo = models.augment(
                    shuffled[:,:static.shape[1]],y,base_select,train,val,cfg)
                _,cpinfo = models.augment(
                    shuffled[:,static.shape[1]:],y,cst_select,train,val,cfg)
                cst = models.refit_augment(
                    shuffled[:,:static.shape[1]],y,base,development,cstinfo)
                cp = models.refit_augment(
                    shuffled[:,static.shape[1]:],y,cst,development,cpinfo)
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
        evidence = h1_evidence(metrics,summary,ci)
        result[asset] = {'metrics':metrics,'C1':summary,'ci95_E1_minus_B0':ci,
                         'h1_evidence':evidence,
                         'folds':diagnostics,'remove_best_days':sensitivity,
                         'controls':control.tolist()}
    ps = [row['p_value_two_sided'] for row in dm_rows]
    for row,p in zip(dm_rows,statistics.holm(ps)):
        row['p_holm'] = p
    supported, confirmed, blocker = h1_status(result, cfg)
    return {'protocol':'rq1_v3_3_locked_holdout', 'mode':cfg['mode'],'assets':result,
            'dm':dm_rows,'h1_exploratory_supported':bool(supported) if cfg['mode'] == 'exploratory' else False,
            'h1_supported_assets':supported,'h1_confirmed':confirmed,
            'h1_confirmation_blocker':blocker,
            'confirmation':cfg.get('confirmation'),
            'limitations':['World là mạng trạng thái quan sát đa thang rút gọn, chưa phải kernel đa tác nhân đầy đủ.',
                           'Chưa có consensus, vintage đa quốc gia đã kiểm chứng hoặc xác suất kịch bản hiệu chỉnh.',
                           'C2 tắt mạng lan truyền nhưng vẫn giữ các trạng thái tĩnh quốc tế.',
                           'HAR dùng bình phương lợi suất ngày; chưa có realized variance trong ngày.',
                           'Thống kê được báo cáo theo tài sản; chưa kiểm định tổng hợp phụ thuộc chéo.']},pd.concat(frames,ignore_index=True)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def configure_console():
    """Không để bảng mã của terminal làm hỏng một lần chạy đã hoàn tất."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
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
    assets = ', '.join(result['h1_supported_assets']) or 'không có'
    if result['mode'] == 'confirmatory':
        status = 'H1 được xác nhận trên holdout khóa.' if result['h1_confirmed'] else 'H1 không được xác nhận trên holdout khóa.'
        print(f'Hoàn tất xác nhận: {args.output}. Tài sản đạt quy tắc H1: {assets}. {status}')
    else:
        print(f'Hoàn tất thăm dò: {args.output}. Tài sản có bằng chứng H1 mạnh: {assets}. '
              'H1 chưa được xác nhận vì chưa có holdout khóa chưa xem.')


if __name__=='__main__':
    main()
