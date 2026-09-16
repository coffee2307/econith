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


def refit_har(x, y, development, details):
    """Khớp lại alpha đã khóa trên train+validation trước khi dự báo test."""
    model = fit(x[development], np.log(np.maximum(y[development], 1e-12)), details['alpha'])
    return np.exp(np.clip(predict(model, x), -30, 5))


def augment(x, y, base, train, validation, cfg):
    base_loss = np.array([np.mean(abs(y[validation]-base[validation])),
                          np.sqrt(np.mean((y[validation]-base[validation])**2))])
    best, selected = 0., None
    rows = []
    cap = float(np.quantile(np.abs(y[train]-base[train]), .9))
    blocks = np.array_split(np.flatnonzero(validation), cfg.get('validation_blocks', 3))
    for alpha in cfg['alphas']:
        model = fit(x[train], y[train]-base[train], alpha, intercept=False)
        delta = np.clip(predict(model, x), -cap, cap)
        for weight in cfg['weights']:
            pred = np.maximum(base + weight*delta, 1e-12)
            errors = y[validation]-pred[validation]
            losses = np.array([np.mean(abs(errors)), np.sqrt(np.mean(errors**2))])
            gain = (base_loss-losses)/np.maximum(base_loss, 1e-12)
            block_gains = []
            wins = 0
            for block in blocks:
                base_block = np.array([np.mean(abs(y[block]-base[block])),
                                       np.sqrt(np.mean((y[block]-base[block])**2))])
                pred_block = np.array([np.mean(abs(y[block]-pred[block])),
                                       np.sqrt(np.mean((y[block]-pred[block])**2))])
                block_gain = (base_block-pred_block)/np.maximum(base_block, 1e-12)
                block_gains.append(float(min(block_gain)))
                wins += int(np.all(block_gain > 0))
            worst_block = min(block_gains)
            eligible = (wins >= cfg['validation_wins'] and min(gain) >= cfg['min_gain']
                        and worst_block >= cfg.get('min_block_gain', -1.))
            rows.append({'alpha': alpha, 'weight': weight, 'gain_mae': float(gain[0]),
                         'gain_rmse': float(gain[1]), 'wins': int(wins),
                         'block_gains': block_gains, 'worst_block_gain': worst_block,
                         'eligible': bool(eligible)})
            robust_score = min(gain) + .25*worst_block
            if eligible and robust_score > best:
                best = robust_score
                selected = (pred, alpha, weight)
    details = {'active': selected is not None, 'cap_train': cap, 'candidates': rows}
    if selected is None:
        return base.copy(), details
    pred, alpha, weight = selected
    details.update(alpha=alpha, weight=weight)
    return pred, details


def refit_augment(x, y, base, development, details):
    """Khớp lại tầng đã được validation chọn; tầng bị loại vẫn giữ nguyên base."""
    if not details.get('active'):
        return base.copy()
    cap = float(np.quantile(np.abs(y[development]-base[development]), .9))
    model = fit(x[development], y[development]-base[development], details['alpha'], intercept=False)
    delta = np.clip(predict(model, x), -cap, cap)
    return np.maximum(base + details['weight']*delta, 1e-12)
