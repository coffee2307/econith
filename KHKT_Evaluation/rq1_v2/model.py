"""Học phần sai số của B0; chọn mức phạt trên đoạn kiểm định riêng."""
from __future__ import annotations

import numpy as np


def fit(x, residual, alpha):
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale = np.where(scale > 1e-12, scale, 1.)
    z = (x - mean) / scale
    # Không hệ số chặn: nhánh bổ sung không được tự sửa B0 khi không có tín hiệu.
    coef = np.linalg.solve(z.T @ z / len(z) + alpha * np.eye(z.shape[1]), z.T @ residual / len(z))
    return mean, scale, coef


def correction(model, x):
    mean, scale, coef = model
    return ((x - mean) / scale) @ coef


def select_predict(x, y, b0, train, valid, test, alphas):
    residual = y - b0
    best = (float(np.mean(np.abs(residual[valid]))), None)
    baseline_loss = best[0]
    candidates = []
    for alpha in alphas:
        model = fit(x[train], residual[train], alpha)
        pred = np.maximum(0., b0[valid] + correction(model, x[valid]))
        loss = float(np.mean(np.abs(y[valid] - pred)))
        candidates.append({"alpha": alpha, "validation_mae": loss})
        if loss < best[0]:
            best = (loss, alpha)
    alpha = best[1]
    if alpha is None:
        return b0[test].copy(), {"alpha": None, "active": False,
                                "baseline_validation_mae": baseline_loss, "candidates": candidates}
    model = fit(x[train | valid], residual[train | valid], alpha)
    pred = np.maximum(0., b0[test] + correction(model, x[test]))
    return pred, {"alpha": alpha, "active": True, "coefficients": model[2].tolist(),
                  "baseline_validation_mae": baseline_loss, "candidates": candidates}


def permute_blocks(x, masks, block, rng):
    out = x.copy()
    # Không chuyển dữ liệu qua ranh giới train/validation/test; giữ các cột cùng hàng.
    for mask in masks:
        idx = np.flatnonzero(mask)
        chunks = [idx[i:i + block] for i in range(0, len(idx), block)]
        if len(chunks) < 4:
            raise ValueError("Mỗi đoạn C1 cần ít nhất bốn khối thời gian.")
        order = rng.permutation(len(chunks))
        out[idx] = x[np.concatenate([chunks[i] for i in order])]
    return out
