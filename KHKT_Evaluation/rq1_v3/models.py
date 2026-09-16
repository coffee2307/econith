"""HAR trên bình phương lợi suất ngày và hiệu chỉnh có co trọng số."""
import numpy as np


def fit(x, y, alpha, intercept=True):
    mean = x.mean(0) if intercept else np.zeros(x.shape[1])
    scale = x.std(0) if intercept else np.sqrt(np.mean(x*x, axis=0))
    scale = np.where(scale > 1e-12, scale, 1.)
    z = (x-mean)/scale
    offset = float(np.mean(y)) if intercept else 0.
    coef = np.linalg.solve(z.T@z/len(z) + alpha*np.eye(z.shape[1]), z.T@(y-offset)/len(z))
    return mean, scale, offset, coef


def predict(model, x):
    mean, scale, offset, coef = model
    return offset + ((x-mean)/scale)@coef


def har(x, y, train, validation, alphas):
    # Chọn alpha chỉ bằng validation; không khớp lại để giữ residual train/valid
    # cùng nguồn dự báo. Bản nâng cấp cross-fitting phải được kiểm thử riêng.
    candidates = []
    for alpha in alphas:
        model = fit(x[train], np.log(np.maximum(y[train], 1e-12)), alpha)
        pred = np.exp(np.clip(predict(model, x), -30, 5))
        loss = np.mean(np.abs(y[validation]-pred[validation]))
        candidates.append((loss, alpha, pred))
    _, alpha, pred = min(candidates, key=lambda row: row[0])
    return pred, {'alpha': alpha, 'definition': 'HAR-style daily squared-return proxy; not intraday RV'}


def augment(x, y, base, train, validation, cfg):
    base_loss = np.array([np.mean(abs(y[validation]-base[validation])),
                          np.sqrt(np.mean((y[validation]-base[validation])**2))])
    best, selected = 0., None
    rows = []
    cap = float(np.quantile(np.abs(y[train]-base[train]), .9))
    blocks = np.array_split(np.flatnonzero(validation), 3)
    for alpha in cfg['alphas']:
        model = fit(x[train], y[train]-base[train], alpha, intercept=False)
        delta = np.clip(predict(model, x), -cap, cap)
        for weight in cfg['weights']:
            pred = np.maximum(base + weight*delta, 1e-12)
            errors = y[validation]-pred[validation]
            losses = np.array([np.mean(abs(errors)), np.sqrt(np.mean(errors**2))])
            gain = (base_loss-losses)/np.maximum(base_loss, 1e-12)
            wins = sum(np.mean(abs(y[b]-pred[b])) < np.mean(abs(y[b]-base[b])) and
                       np.mean((y[b]-pred[b])**2) < np.mean((y[b]-base[b])**2) for b in blocks)
            eligible = wins >= cfg['validation_wins'] and min(gain) >= cfg['min_gain']
            rows.append({'alpha': alpha, 'weight': weight, 'gain_mae': float(gain[0]),
                         'gain_rmse': float(gain[1]), 'wins': int(wins), 'eligible': bool(eligible)})
            if eligible and min(gain) > best:
                best = min(gain)
                selected = (pred, alpha, weight)
    details = {'active': selected is not None, 'cap_train': cap, 'candidates': rows}
    if selected is None:
        return base.copy(), details
    pred, alpha, weight = selected
    details.update(alpha=alpha, weight=weight)
    return pred, details
