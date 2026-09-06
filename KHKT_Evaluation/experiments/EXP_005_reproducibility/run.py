# -*- coding: utf-8 -*-
"""EXP_005: Re-run EXP_001 twice with the same RQ1 main config; compare equality."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from KHKT_Evaluation.common import io_utils, paths
from KHKT_Evaluation.experiments.EXP_001_baseline_vs_econith import run as exp001

EXP_DIR = Path(__file__).resolve().parent
RESULTS = EXP_DIR / "results"
EXP001_DIR = Path(__file__).resolve().parents[1] / "EXP_001_baseline_vs_econith"


def _fingerprint(payload: dict) -> str:
    # Stable numeric + protocol identity (must match RQ1 main / EXP_001)
    slim = {
        "protocol": payload.get("protocol"),
        "beta": payload.get("beta"),
        "lag": payload.get("lag"),
        "n_train": payload.get("n_train"),
        "n_test": payload.get("n_test"),
        "boundary_purge": payload.get("boundary_purge"),
        "fingerprint": payload.get("fingerprint"),
        "B0": payload.get("metrics", {}).get("B0"),
        "E1": payload.get("metrics", {}).get("E1"),
        "C1": payload.get("metrics", {}).get("C1"),
        "comparison": payload.get("comparison"),
        "vol_multiplier_stats": payload.get("vol_multiplier_stats"),
    }
    raw = json.dumps(slim, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def run() -> dict:
    cfg = io_utils.load_yaml(EXP_DIR / "config.yaml")
    rq1_cfg = io_utils.load_yaml(EXP001_DIR / "config.yaml")
    logger = io_utils.setup_logger("EXP_005", paths.LOGS / "EXP_005.log")
    RESULTS.mkdir(parents=True, exist_ok=True)

    required = {
        "protocol": "oos_beta",
        "train_frac": 0.70,
        "hist_vol_window": 60,
        "fwd_vol_horizon": 60,
        "seed": 42,
    }
    sync_ok = True
    sync_issues = []
    for k, expected in required.items():
        got = rq1_cfg.get(k)
        if got is None and k in ("protocol", "train_frac"):
            # defaults inside evaluate_b0_e1_c1 — still record as aligned if absent
            continue
        if got is not None and got != expected:
            sync_ok = False
            sync_issues.append(f"{k}: expected {expected!r}, got {got!r}")

    runs = []
    fps = []
    for i in range(int(cfg.get("repeats", 2))):
        logger.info("repeat %d/%d (RQ1 config=%s)", i + 1, int(cfg.get("repeats", 2)), rq1_cfg.get("id"))
        out = exp001.run()
        runs.append(
            {
                "repeat": i + 1,
                "status": out.get("status"),
                "protocol": out.get("protocol"),
                "beta": out.get("beta"),
                "lag": out.get("lag"),
                "n_train": out.get("n_train"),
                "n_test": out.get("n_test"),
                "boundary_purge": out.get("boundary_purge"),
                "metrics": out.get("metrics"),
            }
        )
        if out.get("status") == "completed":
            fps.append(_fingerprint(out))
        else:
            fps.append(None)

    reproducible = len(fps) >= 2 and fps[0] is not None and len(set(fps)) == 1
    payload = {
        "experiment": "EXP_005",
        "status": "completed" if all(r["status"] == "completed" for r in runs) else "blocked",
        "rq": "RQ4",
        "fabricated": False,
        "rq1_config": rq1_cfg,
        "rq1_config_sync_ok": sync_ok,
        "rq1_config_sync_issues": sync_issues,
        "repeats": runs,
        "fingerprints": fps,
        "reproducible": reproducible,
        "notes": [
            "Compares SHA256 of EXP_001 metrics across repeats with identical RQ1 main config.",
            "Must use EXP_001 protocol=oos_beta, train_frac=0.70, hist/fwd=60, boundary purge.",
            "World bridge is deterministic given the same macro panel (no RNG in vol_mult path).",
        ],
    }
    io_utils.write_json(RESULTS / "metrics.json", payload)
    io_utils.write_csv(
        RESULTS / "metrics.csv",
        [{"repeat": i + 1, "fingerprint": fp, "reproducible_group": reproducible} for i, fp in enumerate(fps)],
    )
    io_utils.write_markdown(
        EXP_DIR / "report.md",
        f"""# EXP_005 — Reproducibility (synced to RQ1 main)

## RQ1 config sync
- Source: `EXP_001_baseline_vs_econith/config.yaml`
- sync_ok: **{sync_ok}**
- issues: {sync_issues or "none"}
- protocol={rq1_cfg.get('protocol')}, train_frac={rq1_cfg.get('train_frac')}, hist={rq1_cfg.get('hist_vol_window')}, fwd={rq1_cfg.get('fwd_vol_horizon')}

## Kết quả
- Reproducible: **{reproducible}**
- Fingerprints: {fps}

Nếu `reproducible=true`, cùng seed/config/scenario RQ1 chính cho ra cùng metric EXP_001.
""",
    )
    logger.info("reproducible=%s sync_ok=%s", reproducible, sync_ok)
    return payload


if __name__ == "__main__":
    run()
