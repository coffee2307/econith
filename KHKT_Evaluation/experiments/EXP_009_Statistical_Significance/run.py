# -*- coding: utf-8 -*-
"""EXP_009: Bootstrap + Diebold-Mariano on OOS E1−B0; reproducibility fingerprints."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from KHKT_Evaluation.common import data_loader, io_utils, metrics, paths, vol_eval

EXP_DIR = Path(__file__).resolve().parent
RESULTS = EXP_DIR / "results"
PLOTS = RESULTS / "plots"


def run() -> dict:
    cfg = io_utils.load_yaml(EXP_DIR / "config.yaml")
    logger = io_utils.setup_logger("EXP_009", paths.LOGS / "EXP_009.log")
    RESULTS.mkdir(parents=True, exist_ok=True)
    PLOTS.mkdir(parents=True, exist_ok=True)

    try:
        path = data_loader.resolve_btc_features()
        df = data_loader.load_btc_panel(
            max_rows=int(cfg.get("max_rows", 25000)),
            stride=int(cfg.get("stride", 5)),
        )
    except FileNotFoundError as exc:
        payload = {"experiment": "EXP_009", "status": "blocked", "reason": str(exc), "fabricated": False}
        io_utils.write_json(RESULTS / "metrics.json", payload)
        return payload

    seed = int(cfg.get("seed", 42))
    fwd = int(cfg.get("fwd_vol_horizon", 60))
    ev = vol_eval.evaluate_b0_e1_c1(
        df,
        hist_vol_window=int(cfg.get("hist_vol_window", 60)),
        fwd_vol_horizon=fwd,
        macro_sample_every=int(cfg.get("macro_sample_every", 10)),
        seed=seed,
        protocol=str(cfg.get("protocol", "oos_beta")),
        train_frac=float(cfg.get("train_frac", 0.70)),
    )
    # Bootstrap / DM only on OOS test mask
    test_mask = ev.get("test_mask")
    y_true, y_b0, y_e1 = ev["y_true"], ev["y_b0"], ev["y_e1"]
    if test_mask is not None:
        mask = test_mask & np.isfinite(y_true) & np.isfinite(y_b0) & np.isfinite(y_e1)
    else:
        mask = np.isfinite(y_true) & np.isfinite(y_b0) & np.isfinite(y_e1)
    yt, yb, ye = y_true[mask], y_b0[mask], y_e1[mask]
    observed = float(metrics.mae(yt, ye) - metrics.mae(yt, yb))  # negative => E1 better

    dm = metrics.diebold_mariano(yt, ye, yb, h=fwd, loss="abs")

    rng = np.random.default_rng(seed)
    B = int(cfg.get("bootstrap_n", 200))
    block = int(cfg.get("block_size", 500))
    n = yt.size
    if n < block * 2:
        block = max(50, n // 10)
    diffs = []
    n_blocks = int(np.ceil(n / block)) if n else 0
    for _ in range(B):
        if n == 0:
            break
        starts = rng.integers(0, max(1, n - block + 1), size=max(1, n_blocks))
        idx = np.concatenate([np.arange(s, min(s + block, n)) for s in starts])[:n]
        d = float(metrics.mae(yt[idx], ye[idx]) - metrics.mae(yt[idx], yb[idx]))
        diffs.append(d)
    diffs = np.asarray(diffs, dtype=float)
    p_e1_not_better = float(np.mean(diffs >= 0.0)) if diffs.size else float("nan")
    ci_low, ci_high = (
        (float(np.quantile(diffs, 0.025)), float(np.quantile(diffs, 0.975))) if diffs.size else (None, None)
    )

    repeats = int(cfg.get("repeats", 5))
    mae_e1_reps = []
    fps = []
    for _i in range(repeats):
        out_i = vol_eval.evaluate_b0_e1_c1(
            df,
            hist_vol_window=int(cfg.get("hist_vol_window", 60)),
            fwd_vol_horizon=fwd,
            macro_sample_every=int(cfg.get("macro_sample_every", 10)),
            seed=seed,
            protocol=str(cfg.get("protocol", "oos_beta")),
            train_frac=float(cfg.get("train_frac", 0.70)),
        )
        mae_e1_reps.append(float(out_i["metrics"]["E1"]["mae"]))
        slim = json.dumps(
            {
                "metrics": out_i["metrics"],
                "beta": out_i.get("beta"),
                "lag": out_i.get("lag"),
                "fingerprint": out_i.get("fingerprint"),
            },
            sort_keys=True,
            default=str,
        )
        fps.append(hashlib.sha256(slim.encode()).hexdigest())

    mae_e1_reps = np.asarray(mae_e1_reps, dtype=float)
    reproducible_rate = float(len(set(fps)) == 1)
    payload = {
        "experiment": "EXP_009",
        "status": "completed",
        "rq": "RQ4",
        "fabricated": False,
        "dataset": data_loader.dataset_provenance(df, path),
        "protocol": ev.get("protocol"),
        "beta": ev.get("beta"),
        "lag": ev.get("lag"),
        "fingerprint": ev.get("fingerprint"),
        "point_estimate": {
            "mae_e1_minus_mae_b0": observed,
            "e1_better_point": bool(observed < 0),
            "metrics": ev["metrics"],
            "comparison": ev["comparison"],
            "vol_multiplier_stats": ev["vol_multiplier_stats"],
            "n_test": int(mask.sum()),
        },
        "diebold_mariano": {
            **dm,
            "note": "dm_stat<0 means E1 (A) has lower abs loss than B0 (B) on OOS test.",
        },
        "bootstrap": {
            "n": B,
            "block_size": block,
            "ci95": [ci_low, ci_high],
            "p_share_diff_ge_0": p_e1_not_better,
            "mean_diff": float(np.mean(diffs)) if diffs.size else None,
            "std_diff": float(np.std(diffs)) if diffs.size else None,
            "scope": "OOS test mask only",
            "interpretation": (
                "observed = MAE(E1)-MAE(B0); negative favors E1. "
                "p_share_diff_ge_0 is the bootstrap share of non-negative diffs."
            ),
        },
        "protocol_legacy": ev.get("protocol_legacy"),
        "reproducibility": {
            "repeats": repeats,
            "unique_fingerprints": len(set(fps)),
            "reproducible_rate": reproducible_rate,
            "mae_e1_mean": float(np.mean(mae_e1_reps)),
            "mae_e1_variance": float(np.var(mae_e1_reps)),
            "mae_e1_std": float(np.std(mae_e1_reps)),
            "output_similarity": "identical" if reproducible_rate == 1.0 else "divergent",
            "vol_eval_fingerprint": ev.get("fingerprint"),
        },
        "limitations": [
            "Block bootstrap on OOS test; DM uses Newey-West with h=fwd_vol_horizon.",
            "Reproducibility covers deterministic offline pipeline with fixed seed.",
        ],
    }
    io_utils.write_json(RESULTS / "metrics.json", payload)
    io_utils.write_csv(
        RESULTS / "raw_results.csv",
        [{"bootstrap_i": i, "mae_e1_minus_b0": float(d)} for i, d in enumerate(diffs)],
    )
    io_utils.write_csv(
        RESULTS / "metrics.csv",
        [
            {
                "observed_mae_diff": observed,
                "ci95_low": ci_low,
                "ci95_high": ci_high,
                "p_share_diff_ge_0": p_e1_not_better,
                "dm_stat": dm.get("dm_stat"),
                "dm_p_two_sided": dm.get("p_value_two_sided"),
                "reproducible_rate": reproducible_rate,
                "mae_e1_variance": float(np.var(mae_e1_reps)),
                "beta": ev.get("beta"),
                "lag": ev.get("lag"),
            }
        ],
    )
    try:
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(7.2, 4.0))
        ax.hist(diffs, bins=30, color="#1F4E79", alpha=0.85)
        ax.axvline(observed, color="black", linestyle="--", label=f"observed={observed:.3g}")
        ax.axvline(0.0, color="red", linestyle=":", label="0 (E1==B0)")
        ax.set_title("EXP_009 — Bootstrap MAE(E1)-MAE(B0) on OOS test")
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(PLOTS / "bootstrap_mae_diff.png", dpi=140)
        plt.close(fig)
    except Exception:  # noqa: BLE001
        pass

    io_utils.write_markdown(
        EXP_DIR / "report.md",
        f"""# EXP_009 — Statistical Significance & Reproducibility (RQ4)

## Protocol
oos_beta — beta={ev.get('beta')}, lag={ev.get('lag')}, fingerprint=`{ev.get('fingerprint')}` (OOS test only)

## Kết quả chính
- Observed MAE(E1)−MAE(B0): **{observed:.6g}** (âm ⇒ E1 tốt hơn)
- Bootstrap 95% CI: **[{ci_low}, {ci_high}]**
- Share of bootstrap diffs ≥ 0: **{p_e1_not_better:.4f}**
- Diebold-Mariano stat: **{dm.get('dm_stat')}** (p≈{dm.get('p_value_two_sided')})
- Reproducible rate (identical fingerprints / {repeats} repeats): **{reproducible_rate}**
- MAE(E1) variance across repeats: **{float(np.var(mae_e1_reps)):.3g}**

## Limitations
{chr(10).join('- ' + x for x in payload['limitations'])}
""",
    )
    logger.info("observed_diff=%.6g dm=%.4g repro=%s", observed, dm.get("dm_stat"), reproducible_rate)
    return payload


if __name__ == "__main__":
    run()
