"""ECONITH :: training.fit_regime  (PHASE C -- Regime Detector)

Teach the system to recognise "what kind of weather" the market is in.

Economic analogy
----------------
Markets have moods -- calm sideways drift, roaring trends, panicky high-volatility
crashes. A good trader first asks "what regime are we in?" before choosing tactics,
the same way a farmer checks the season before planting. This script studies the
history and clusters every moment into one of four hidden regimes using a Hidden
Markov Model (HMM) -- a tool that assumes there's an invisible "market mood" that
switches over time and colours the numbers we can see.

The HMM is ideal because it also learns the *transition* probabilities: how likely
calm is to tip into a storm. If ``hmmlearn`` isn't available, we fall back to a
Gaussian Mixture (``scikit-learn``) which clusters the moods without the timing --
still useful, just weather snapshots instead of a forecast.

Output: ``models/regime/hmm_4state.pkl`` (a bundle of the model + the scaler that
normalises inputs, so production feeds it data the exact same way).

Run it:
    python training/fit_regime.py --data ./datasets/processed/quant_labeled.parquet \
        --output ./models/regime/hmm_4state.pkl --states 4
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
)
logger = logging.getLogger("econith.training.fit_regime")


def _regime_features(df) -> np.ndarray:
    """Build the small 'weather instruments' the regime model reads.

    We describe each moment by how the market is *behaving*, not its raw price:
      * short return       -- direction/strength of the move
      * rolling volatility -- how stormy it is
      * order-book imbalance -- pressure building on one side
      * volume delta       -- aggression of buyers vs sellers
    """
    import pandas as pd

    price = pd.to_numeric(df.get("price"), errors="coerce")
    if price is None or price.isna().all():
        price = pd.to_numeric(df.get("mid"), errors="coerce")
    ret = price.pct_change().fillna(0.0)
    vol = ret.rolling(32, min_periods=1).std().fillna(0.0)
    obi = pd.to_numeric(df.get("obi"), errors="coerce").fillna(0.0)
    vd = pd.to_numeric(df.get("volume_delta"), errors="coerce").fillna(0.0)

    mat = np.column_stack([
        ret.to_numpy("float64"),
        vol.to_numpy("float64"),
        obi.to_numpy("float64"),
        vd.to_numpy("float64"),
    ])
    return np.nan_to_num(mat, nan=0.0, posinf=0.0, neginf=0.0)


def fit_regime(data_path: str, output: str, states: int = 4, seed: int = 42) -> dict:
    import joblib
    import pandas as pd
    from sklearn.preprocessing import StandardScaler

    p = Path(data_path)
    if not p.exists():
        raise SystemExit(f"training data not found: {data_path} -- run `make data-label` first")
    df = pd.read_parquet(p, engine="pyarrow")
    x = _regime_features(df)
    scaler = StandardScaler().fit(x)
    xs = scaler.transform(x)

    backend = "hmm"
    try:
        from hmmlearn.hmm import GaussianHMM

        model = GaussianHMM(
            n_components=states,
            covariance_type="diag",
            n_iter=100,
            random_state=seed,
        )
        model.fit(xs)
        score = float(model.score(xs))       # log-likelihood: higher == better fit
        labels = model.predict(xs)
    except ImportError:
        # Fallback: cluster the moods without the timing model.
        from sklearn.mixture import GaussianMixture

        backend = "gmm"
        logger.warning("hmmlearn unavailable -- falling back to sklearn GaussianMixture")
        model = GaussianMixture(n_components=states, covariance_type="diag", random_state=seed)
        model.fit(xs)
        score = float(model.score(xs))
        labels = model.predict(xs)

    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    # Bundle model + scaler + metadata so production preprocesses identically.
    bundle = {
        "backend": backend,
        "model": model,
        "scaler": scaler,
        "n_states": states,
        "feature_order": ["return", "volatility", "obi", "volume_delta"],
    }
    joblib.dump(bundle, out)

    # A quick, human-readable read on how the moods split.
    counts = {int(k): int(v) for k, v in zip(*np.unique(labels, return_counts=True))}
    metrics = {
        "backend": backend,
        "states": states,
        "rows": int(len(df)),
        "log_likelihood": score,
        "state_counts": counts,
        "output": str(out),
    }
    (out.parent / f"{out.stem}.metrics.json").write_text(json.dumps(metrics, indent=2))
    logger.info("fit %s regime model (%d states) -> %s | score=%.2f counts=%s",
                backend, states, out, score, counts)
    return metrics


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fit_regime.py", description="ECONITH regime fitter")
    p.add_argument("--data", default="./datasets/processed/quant_labeled.parquet")
    p.add_argument("--output", default="./models/regime/hmm_4state.pkl")
    p.add_argument("--states", type=int, default=4)
    p.add_argument("--seed", type=int, default=42)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    fit_regime(args.data, args.output, args.states, args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
