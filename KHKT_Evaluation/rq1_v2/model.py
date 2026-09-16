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


def select_predict(x, y, b0, train, valid, test, alphas, *, validation_blocks=1,
                   required_validation_wins=1, min_relative_gain=0., shrinkages=(1.,)):
    residual = y - b0
    best = (float(np.mean(np.abs(residual[valid]))), None)
    baseline_loss = best[0]
    candidates = []
    valid_indices = np.flatnonzero(valid)
    blocks = [part for part in np.array_split(valid_indices, validation_blocks) if len(part)]
    for alpha in alphas:
        model = fit(x[train], residual[train], alpha)
        raw_correction = correction(model, x[valid])
        for shrinkage in shrinkages:
            pred = np.maximum(0., b0[valid] + shrinkage * raw_correction)
            loss = float(np.mean(np.abs(y[valid] - pred)))
            block_losses = []
            block_wins = 0
            for indices in blocks:
                local = np.searchsorted(valid_indices, indices)
                candidate_loss = float(np.mean(np.abs(y[indices] - pred[local])))
                local_base = float(np.mean(np.abs(y[indices] - b0[indices])))
                block_losses.append(candidate_loss)
                block_wins += candidate_loss < local_base
            relative_gain = (baseline_loss - loss) / max(baseline_loss, 1e-12)
            stable = (relative_gain >= min_relative_gain and
                      block_wins >= required_validation_wins)
            candidates.append({"alpha": alpha, "shrinkage": shrinkage,
                               "validation_mae": loss, "relative_gain": relative_gain,
                               "block_wins": block_wins, "block_losses": block_losses,
                               "eligible": bool(stable)})
            if stable and loss < best[0]:
                best = (loss, (alpha, shrinkage))
    selected = best[1]
    if selected is None:
        return b0[test].copy(), {"alpha": None, "active": False,
                                "baseline_validation_mae": baseline_loss, "candidates": candidates}
    alpha, shrinkage = selected
    model = fit(x[train | valid], residual[train | valid], alpha)
    pred = np.maximum(0., b0[test] + shrinkage * correction(model, x[test]))
    return pred, {"alpha": alpha, "shrinkage": shrinkage, "active": True,
                  "coefficients": model[2].tolist(),
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
