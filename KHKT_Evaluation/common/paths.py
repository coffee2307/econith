# -*- coding: utf-8 -*-
"""Shared paths for KHKT_Evaluation."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # f:/econith
KHKT = Path(__file__).resolve().parents[1]

DATASETS = ROOT / "datasets"
FEATURES_BTC = DATASETS / "features" / "BTCUSDT_features.parquet"
FEATURES_BTC_ALT = DATASETS / "features" / "features_BTCUSDT.parquet"
HOLDOUT = DATASETS / "processed" / "quant_holdout.parquet"
MACRO_RAW = DATASETS / "raw" / "macro"
CALIBRATED_COEFFS = ROOT / "models" / "world" / "stochastic_coeffs.json"
ROLLOUTS = ROOT / "data" / "rollouts"

RESULTS = KHKT / "results"
LOGS = KHKT / "logs"
EXPERIMENTS = KHKT / "experiments"

for p in (RESULTS, LOGS):
    p.mkdir(parents=True, exist_ok=True)
