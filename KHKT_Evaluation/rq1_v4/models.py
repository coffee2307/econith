"""Các tầng dự báo dương, lồng nhau và chỉ chọn cấu hình bằng validation."""
import numpy as np

from KHKT_Evaluation.rq1_v3.models import fit, har, predict, refit_har


def _gate(values, train):
    """Chuẩn hóa bền vững chỉ trên train rồi nén về [-1, 1]."""
    center = float(np.median(values[train]))
    q25, q75 = np.quantile(values[train], [.25, .75])
    scale = float(q75-q25)
    if not np.isfinite(scale) or scale <= 1e-12:
        scale = float(np.std(values[train]))
    if not np.isfinite(scale) or scale <= 1e-12:
        scale = 1.
    return np.tanh((values-center)/scale)


def regime_interactions(world_x, market_x, train):
    """Cho World phản ứng khác nhau theo mức và xu hướng biến động đã biết."""
    world_x = np.asarray(world_x, float)
    market_x = np.asarray(market_x, float)
    if world_x.ndim != 2 or market_x.ndim != 2 or len(world_x) != len(market_x):
        raise ValueError('Ma trận World/market không tương thích.')
    level = _gate(market_x[:, 2], train)
    trend = _gate(market_x[:, 0]-market_x[:, 2], train)
    stress = np.maximum(level, 0.)
    return np.concatenate((world_x,
                           world_x*stress[:, None],
                           world_x*trend[:, None]), axis=1)


def ratio_augment(x, y, base, train, validation, cfg):
    """Học phần hiệu chỉnh theo tỷ lệ; x=0 luôn trả lại đúng baseline."""
    x, y, base = np.asarray(x,float), np.asarray(y,float), np.asarray(base,float)
    ratio = np.log(np.maximum(y,1e-12)/np.maximum(base,1e-12))
    cap = float(np.quantile(np.abs(ratio[train]), .9))
    base_loss = np.array([np.mean(abs(y[validation]-base[validation])),
                          np.sqrt(np.mean((y[validation]-base[validation])**2))])
    blocks = np.array_split(np.flatnonzero(validation), cfg.get('validation_blocks',3))
    best, selected, rows = -np.inf, None, []
    for alpha in cfg['alphas']:
        model = fit(x[train],ratio[train],alpha,intercept=False)
        delta = np.clip(predict(model,x),-cap,cap)
        for weight in cfg['weights']:
            pred = np.maximum(base*np.exp(weight*delta),1e-12)
            errors = y[validation]-pred[validation]
            losses = np.array([np.mean(abs(errors)),np.sqrt(np.mean(errors**2))])
            gain = (base_loss-losses)/np.maximum(base_loss,1e-12)
            block_gains=[]; wins=0
            for block in blocks:
                base_block=np.array([np.mean(abs(y[block]-base[block])),
                                     np.sqrt(np.mean((y[block]-base[block])**2))])
                pred_block=np.array([np.mean(abs(y[block]-pred[block])),
                                     np.sqrt(np.mean((y[block]-pred[block])**2))])
                block_gain=(base_block-pred_block)/np.maximum(base_block,1e-12)
                block_gains.append(float(min(block_gain)))
                wins += int(np.all(block_gain>0))
            worst=min(block_gains)
            eligible=(wins>=cfg['validation_wins'] and min(gain)>=cfg['min_gain']
                      and worst>=cfg.get('min_block_gain',-1.))
            score=min(gain)+.25*worst
            rows.append({'alpha':alpha,'weight':weight,'gain_mae':float(gain[0]),
                         'gain_rmse':float(gain[1]),'wins':int(wins),
                         'block_gains':block_gains,'worst_block_gain':worst,
                         'eligible':bool(eligible)})
            if eligible and score>best:
                best,selected=score,(pred,alpha,weight)
    details={'active':selected is not None,'cap_log_ratio_train':cap,
             'form':'multiplicative_log_ratio','candidates':rows}
    if selected is None:
        return base.copy(),details
    pred,alpha,weight=selected
    details.update(alpha=alpha,weight=weight)
    return pred,details


def refit_ratio_augment(x,y,base,development,details):
    """Khớp lại lựa chọn đã khóa trên toàn bộ dữ liệu trước test."""
    if not details.get('active'):
        return np.asarray(base,float).copy()
    x,y,base=np.asarray(x,float),np.asarray(y,float),np.asarray(base,float)
    ratio=np.log(np.maximum(y,1e-12)/np.maximum(base,1e-12))
    cap=float(np.quantile(np.abs(ratio[development]),.9))
    model=fit(x[development],ratio[development],details['alpha'],intercept=False)
    delta=np.clip(predict(model,x),-cap,cap)
    return np.maximum(base*np.exp(details['weight']*delta),1e-12)
