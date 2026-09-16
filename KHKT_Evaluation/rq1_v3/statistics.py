"""So sánh ghép cặp; không xem các phiên chồng lấn là độc lập."""
import numpy as np


def score(y, pred):
    error = y-pred
    ratio = np.maximum(y*y, 1e-16)/np.maximum(pred*pred, 1e-16)
    return {'mae': float(np.mean(abs(error))), 'rmse': float(np.sqrt(np.mean(error**2))),
            'qlike': float(np.mean(ratio-np.log(ratio)-1))}


def interval(groups, repeats, block, seed):
    rng = np.random.default_rng(seed)
    diffs = []
    for _ in range(repeats):
        pieces = []
        for group in groups:
            n = len(group)
            starts = rng.integers(n, size=int(np.ceil(n/block)))
            indices = np.concatenate([(s+np.arange(block)) % n for s in starts])[:n]
            pieces.append(group[indices])
        y, b, e = np.concatenate(pieces).T
        baseline, augmented = score(y,b), score(y,e)
        diffs.append([augmented[k]-baseline[k] for k in ('mae','rmse')])
    return dict(zip(('mae','rmse'), np.quantile(diffs,[.025,.975],axis=0).T.tolist()))


def holm(values):
    a = np.asarray(values,float)
    order = np.argsort(a)
    adjusted = np.minimum(1., np.maximum.accumulate(a[order]*(len(a)-np.arange(len(a)))))
    out = np.empty_like(a)
    out[order] = adjusted
    return out.tolist()


def shuffle(x, masks, block, rng):
    out = x.copy()
    for mask in masks:
        ids = np.flatnonzero(mask)
        chunks = [ids[i:i+block] for i in range(0,len(ids),block)]
        if len(chunks) < 4:
            raise ValueError('C1 cần ít nhất bốn khối mỗi phần.')
        order = rng.permutation(len(chunks))
        out[ids] = x[np.concatenate([chunks[i] for i in order])]
    return out
