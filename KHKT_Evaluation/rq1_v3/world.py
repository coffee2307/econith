"""World rút gọn: trạng thái quan sát và lan truyền thay đổi giữa các quốc gia."""
import numpy as np
import pandas as pd
from .data import FEATURES


def panel_at(times, releases, countries):
    values, changes = [], []
    for country in countries:
        for feature in FEATURES:
            rows = releases[(releases.country == country) & (releases.feature == feature)].copy()
            if rows.empty:
                raise ValueError(f'Thiếu {country}/{feature}; không thay bằng node tổng hợp.')
            rows = rows.loc[rows.observation_at >= rows.observation_at.cummax()]
            # Kỳ vọng ngây thơ có thể tái lập; không gọi đây là đồng thuận thị trường.
            rows['innovation'] = rows.value.diff().fillna(0.)
            rows['cumulative'] = rows.innovation.cumsum()
            merged = pd.merge_asof(pd.DataFrame({'time': times}), rows,
                                   left_on='time', right_on='available_at', direction='backward')
            values.append(merged.value.to_numpy())
            cumulative = merged.cumulative.to_numpy()
            changes.append(np.r_[0., np.diff(cumulative)])
    return np.array(values).T, np.array(changes).T


def graph(cfg):
    countries = cfg['countries']
    a = np.asarray(cfg['network'], dtype=float)
    if a.shape != (len(countries), len(countries)) or not np.isfinite(a).all() or (a < 0).any():
        raise ValueError('Ma trận mạng không hợp lệ.')
    if np.any(np.diag(a) != 0) or np.any(a.sum(1) > 1.+1e-12):
        raise ValueError('Mạng phải có đường chéo 0 và tổng hàng <= 1.')
    if not cfg.get('network_source'):
        raise ValueError('Cần nguồn và thời điểm mạng.')
    return a


def features(levels, innovations, train, cfg, weights, *, linked=True):
    n = len(cfg['countries'])
    w = np.asarray(weights, float)
    if w.shape != (n,) or (w < 0).any() or not np.isclose(w.sum(), 1):
        raise ValueError('Trọng số tài sản phải không âm và tổng bằng 1.')
    network = graph(cfg) if linked else np.zeros((n, n))
    x = levels.reshape(-1, n, len(FEATURES))
    events = innovations.reshape(x.shape)
    mean = np.mean(x[train], axis=0)
    scale = np.std(x[train], axis=0)
    scale = np.where(scale > 1e-8, scale, 1.)
    state = (x-mean)/scale
    shocks = events/scale
    # Hệ số nhớ được học từ thay đổi đã quan sát trên train, giới hạn để ổn định.
    ids = np.flatnonzero(train)
    consecutive = ids[1:][np.diff(ids) == 1]
    prev, nxt = state[consecutive-1], state[consecutive]
    rho = np.clip(np.sum(prev*nxt, axis=0)/np.maximum(np.sum(prev*prev, axis=0), 1e-12), 0., .98)
    rng = np.random.default_rng(cfg['seed'])
    # Phân tán tham số là phân tích độ nhạy; chưa phải xác suất đã hiệu chỉnh.
    rhos = np.clip(rho[None] + rng.normal(0, .025, (cfg['paths'], n, len(FEATURES))), 0., .995)
    static = np.einsum('tnf,n->tf', state, w)
    dynamic = []
    for shock in shocks:
        paths = np.broadcast_to(shock, rhos.shape).copy()
        total = np.zeros_like(paths)
        for _ in range(cfg['rollout_steps']):
            paths = .8*rhos*paths + .2*np.einsum('ij,pjf->pif', network, paths)
            total += paths
        impact = np.einsum('pnf,n->pf', total, w)
        dynamic.append(np.r_[np.median(impact, axis=0),
                              np.quantile(impact,.9,axis=0)-np.quantile(impact,.1,axis=0)])
    return static, np.asarray(dynamic), {'rho': rho.tolist(), 'paths': cfg['paths'],
             'model': 'observed-state network surrogate',
             'uncertainty': 'parameter sensitivity; not calibrated predictive probability'}
