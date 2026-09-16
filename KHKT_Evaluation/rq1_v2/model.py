"""Học phần sai số của B0; chọn mức phạt trên đoạn kiểm định riêng."""
from __future__ import annotations

import numpy as np


def fit(x, residual, alpha, center_features=True):
    """Khớp Ridge, có thể giữ mốc 0 của các đặc trưng xung sự kiện."""
    center = np.asarray(center_features)
    if center.ndim == 0:
        center = np.full(x.shape[1], bool(center))
    if center.shape != (x.shape[1],):
        raise ValueError("center_features phải có một giá trị cho mỗi cột.")
    mean = np.where(center, x.mean(axis=0), 0.)
    centered = x - mean
    std = x.std(axis=0)
    rms = np.sqrt(np.mean(x ** 2, axis=0))
    scale = np.where(center, std, rms)
    scale = np.where(scale > 1e-12, scale, 1.)
    z = centered / scale
    # Không hệ số chặn. Khi center=False, x=0 luôn cho hiệu chỉnh bằng 0.
    coef = np.linalg.solve(z.T @ z / len(z) + alpha * np.eye(z.shape[1]), z.T @ residual / len(z))
    return mean, scale, coef


def correction(model, x):
    mean, scale, coef = model
    return ((x - mean) / scale) @ coef


def select_predict(x, y, b0, train, valid, test, alphas, *, validation_blocks=1,
                   required_validation_wins=1, min_relative_gain=0., shrinkages=(1.,),
                   center_features=True, selection_metrics=("mae", "rmse")):
    residual = y - b0
    baseline = {
        "mae": float(np.mean(np.abs(residual[valid]))),
        "rmse": float(np.sqrt(np.mean(residual[valid] ** 2))),
    }
    metrics = tuple(selection_metrics)
    if not metrics or any(name not in baseline for name in metrics):
        raise ValueError("selection_metrics chỉ nhận mae và rmse.")
    best = (-np.inf, None)
    candidates = []
    valid_indices = np.flatnonzero(valid)
    blocks = [part for part in np.array_split(valid_indices, validation_blocks) if len(part)]
    for alpha in alphas:
        model = fit(x[train], residual[train], alpha, center_features)
        raw_correction = correction(model, x[valid])
        for shrinkage in shrinkages:
            pred = np.maximum(0., b0[valid] + shrinkage * raw_correction)
            error = y[valid] - pred
            losses = {
                "mae": float(np.mean(np.abs(error))),
                "rmse": float(np.sqrt(np.mean(error ** 2))),
            }
            block_losses = []
            block_wins = 0
            for indices in blocks:
                local = np.searchsorted(valid_indices, indices)
                local_error = y[indices] - pred[local]
                base_error = y[indices] - b0[indices]
                candidate_loss = {
                    "mae": float(np.mean(np.abs(local_error))),
                    "rmse": float(np.sqrt(np.mean(local_error ** 2))),
                }
                local_base = {
                    "mae": float(np.mean(np.abs(base_error))),
                    "rmse": float(np.sqrt(np.mean(base_error ** 2))),
                }
                block_losses.append(candidate_loss)
                block_wins += all(candidate_loss[name] < local_base[name] for name in metrics)
            relative_gains = {
                name: (baseline[name] - losses[name]) / max(baseline[name], 1e-12)
                for name in baseline
            }
            selection_gain = min(relative_gains[name] for name in metrics)
            stable = (selection_gain >= min_relative_gain and
                      block_wins >= required_validation_wins)
            candidates.append({"alpha": alpha, "shrinkage": shrinkage,
                               "validation_mae": losses["mae"],
                               "validation_rmse": losses["rmse"],
                               "relative_gain": selection_gain,
                               "relative_gain_mae": relative_gains["mae"],
                               "relative_gain_rmse": relative_gains["rmse"],
                               "block_wins": block_wins, "block_losses": block_losses,
                               "eligible": bool(stable)})
            if stable and selection_gain > best[0]:
                best = (selection_gain, (alpha, shrinkage))
    selected = best[1]
    if selected is None:
        return b0[test].copy(), {"alpha": None, "active": False,
                                "baseline_validation_mae": baseline["mae"],
                                "baseline_validation_rmse": baseline["rmse"],
                                "selection_metrics": list(metrics), "candidates": candidates}
    alpha, shrinkage = selected
    model = fit(x[train | valid], residual[train | valid], alpha, center_features)
    pred = np.maximum(0., b0[test] + shrinkage * correction(model, x[test]))
    return pred, {"alpha": alpha, "shrinkage": shrinkage, "active": True,
                  "coefficients": model[2].tolist(),
                  "baseline_validation_mae": baseline["mae"],
                  "baseline_validation_rmse": baseline["rmse"],
                  "selection_metrics": list(metrics), "candidates": candidates}


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
