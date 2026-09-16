"""World rút gọn: trạng thái, xung có trí nhớ và lan truyền giữa quốc gia."""
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
            rows = rows.sort_values(['available_at', 'observation_at'])
            rows = rows.loc[rows.observation_at >= rows.observation_at.cummax()]
            # Một ngày có thể công bố nhiều kỳ. Tại cùng thời điểm, trạng thái mới
            # nhất là kỳ quan sát gần nhất; merge_asof không cần tự xử lý khóa trùng.
            rows = rows.groupby('available_at', as_index=False, sort=True).tail(1)
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


def decayed_impulses(events, half_lives):
    """Tạo xung nhân quả còn tác dụng qua nhiều phiên, không dùng dữ liệu tương lai."""
    events = np.asarray(events, float)
    half_lives = np.asarray(half_lives, float)
    if events.ndim != 3 or half_lives.ndim != 1 or len(half_lives) == 0:
        raise ValueError('Xung hoặc chu kỳ bán rã không hợp lệ.')
    if not np.isfinite(events).all() or not np.isfinite(half_lives).all() or (half_lives <= 0).any():
        raise ValueError('Xung và chu kỳ bán rã phải hữu hạn, chu kỳ phải dương.')
    decay = np.power(.5, 1./half_lives)
    out = np.zeros((len(events), len(half_lives), *events.shape[1:]), float)
    memory = np.zeros(out.shape[1:], float)
    for t, event in enumerate(events):
        memory = decay[:, None, None]*memory + event[None]
        out[t] = memory
    return out


def channels(values, weights):
    """Giữ cả trạng thái nội địa, toàn cầu, chênh lệch và phân tán quốc gia."""
    values = np.asarray(values, float)
    weights = np.asarray(weights, float)
    local = np.einsum('...nf,n->...f', values, weights)
    global_mean = np.mean(values, axis=-2)
    gap = local-global_mean
    dispersion = np.std(values, axis=-2)
    return np.concatenate((local, global_mean, gap, dispersion), axis=-1)


def dynamic_channels(values, weights):
    """Mã hóa cả hướng và cường độ vì biến động có thể tăng với cú sốc hai chiều."""
    values = np.asarray(values, float)
    weights = np.asarray(weights, float)
    local = np.einsum('...nf,n->...f', values, weights)
    global_mean = np.mean(values, axis=-2)
    local_magnitude = np.einsum('...nf,n->...f', np.abs(values), weights)
    global_magnitude = np.mean(np.abs(values), axis=-2)
    return np.concatenate((local, local-global_mean, local_magnitude, global_magnitude), axis=-1)


def association_network(shocks, train, cfg):
    """Ước lượng độ mạnh liên hệ trễ chỉ từ train; không diễn giải là nhân quả."""
    n = shocks.shape[1]
    carried = decayed_impulses(shocks, [cfg.get('network_half_life', 7.)])[:, 0]
    ids = np.flatnonzero(train)
    ids = ids[1:][np.diff(ids) == 1]
    matrix = np.zeros((n, n), float)
    shrink = float(cfg.get('network_shrinkage', .25))
    maximum = float(cfg.get('network_max_weight', .2))
    for receiver in range(n):
        for sender in range(n):
            if receiver == sender:
                continue
            correlations = []
            for feature in range(shocks.shape[2]):
                source = carried[ids-1, sender, feature]
                target = carried[ids, receiver, feature]
                if np.std(source) > 1e-12 and np.std(target) > 1e-12:
                    correlations.append(abs(float(np.corrcoef(source, target)[0, 1])))
            if correlations:
                matrix[receiver, sender] = min(maximum, shrink*float(np.median(correlations)))
    row_sum = matrix.sum(1)
    oversized = row_sum > 1.
    matrix[oversized] /= row_sum[oversized, None]
    return matrix


def features(levels, innovations, train, cfg, weights, *, linked=True):
    n = len(cfg['countries'])
    w = np.asarray(weights, float)
    if w.shape != (n,) or (w < 0).any() or not np.isclose(w.sum(), 1):
        raise ValueError('Trọng số tài sản phải không âm và tổng bằng 1.')
    configured_network = graph(cfg)
    x = levels.reshape(-1, n, len(FEATURES))
    events = innovations.reshape(x.shape)
    mean = np.mean(x[train], axis=0)
    scale = np.std(x[train], axis=0)
    scale = np.where(scale > 1e-8, scale, 1.)
    state = (x-mean)/scale
    # Trước lần công bố đầu tiên, panel có NaN vì chưa biết mức vĩ mô. Điều đó
    # đồng nghĩa chưa có xung mới, không phải một xung bị thiếu cần nội suy.
    # Mức state vẫn giữ NaN để mặt nạ usable loại đúng các dòng chưa đủ dữ liệu.
    shocks = np.where(np.isfinite(events), events/scale, 0.)
    if linked and cfg.get('network_mode', 'fixed') == 'train_association':
        network = association_network(shocks, train, cfg)
        network_method = 'train-only lagged absolute association; not causal'
    elif linked:
        network = configured_network
        network_method = 'fixed external matrix'
    else:
        network = np.zeros((n, n))
        network_method = 'disabled control'
    # Hệ số nhớ được học từ thay đổi đã quan sát trên train, giới hạn để ổn định.
    ids = np.flatnonzero(train)
    consecutive = ids[1:][np.diff(ids) == 1]
    prev, nxt = state[consecutive-1], state[consecutive]
    rho = np.clip(np.sum(prev*nxt, axis=0)/np.maximum(np.sum(prev*prev, axis=0), 1e-12), 0., .98)
    rng = np.random.default_rng(cfg['seed'])
    # Phân tán tham số là phân tích độ nhạy; chưa phải xác suất đã hiệu chỉnh.
    rhos = np.clip(rho[None] + rng.normal(0, .025, (cfg['paths'], n, len(FEATURES))), 0., .995)
    half_lives = cfg.get('impulse_half_lives', [3., 7., 21.])
    carried = decayed_impulses(shocks, half_lives)
    static = channels(state, w)
    dynamic = []
    sensitivity_widths = []
    for impulses in carried:
        scales = []
        for impulse in impulses:
            paths = np.broadcast_to(impulse, rhos.shape).copy()
            total = np.zeros_like(paths)
            for _ in range(cfg['rollout_steps']):
                paths = .8*rhos*paths + .2*np.einsum('ij,pjf->pif', network, paths)
                total += paths
            impact = dynamic_channels(total, w)
            scales.append(np.median(impact, axis=0))
            sensitivity_widths.append(float(np.mean(
                np.quantile(impact,.9,axis=0)-np.quantile(impact,.1,axis=0))))
        dynamic.append(np.concatenate(scales))
    return static, np.asarray(dynamic), {'rho': rho.tolist(), 'paths': cfg['paths'],
             'model': 'causal multiscale observed-state network surrogate',
             'impulse_half_lives': [float(x) for x in half_lives],
             'network': network.tolist(), 'network_method': network_method,
             'static_channels': ['asset_local','global_mean','local_minus_global','country_dispersion'],
             'dynamic_channels': ['signed_asset_local','signed_local_minus_global',
                                  'magnitude_asset_local','magnitude_global'],
             'sensitivity_width_mean': float(np.mean(sensitivity_widths)),
             'dynamic_nonzero_share': float(np.mean(np.abs(dynamic) > 1e-12)),
             'uncertainty': 'parameter sensitivity; not calibrated predictive probability'}
