"""ECONITH :: training.train_world  (PHASE C -- Neural World Model)

Teach a neural net how the macro-economy pushes markets around.

Economic analogy
----------------
ECONITH World already has an *analytic* rulebook (the Geopolitical Causal Graph):
raise interest rates -> risk assets cool; a war shock -> volatility spikes; an
aging society -> demand deflates. That rulebook is precise but rigid. This script
trains a small neural network to **internalise** that rulebook by example, the way
an economics student eventually develops intuition after seeing thousands of cases
instead of re-deriving every equation.

We generate thousands of hypothetical countries (perturbing the real 6-nation
world with random but plausible shocks -- domain randomisation), run each through
the analytic reaction rule to get the "correct" market response, and train the net
to reproduce it. The result (``neural_reaction.pt``) is a fast, differentiable
stand-in for the causal graph -- useful for the SIMULATION sandbox where α_sim
keeps its influence capped so it never overrides real-market learning.

Input  : the ~113-dim macro state vector (CountryState.to_vector()).
Output : 3 market-reaction signals -> [expected_volatility, directional_bias,
         risk_premium].

Run it:
    python training/train_world.py --output ./models/world/neural_reaction.pt \
        --samples 20000 --epochs 40
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

from ai.simulator_engine.macro_vectors import CountryState, default_world  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
)
logger = logging.getLogger("econith.training.train_world")


# ===========================================================================
#  Analytic teacher: the economic "ground truth" the net learns to imitate
# ===========================================================================
def _analytic_reaction(cs: CountryState) -> np.ndarray:
    """The rulebook's verdict on a country's market impact (3 signals).

    These formulas are deliberately simple, monotone economic relationships --
    the kind of cause->effect a textbook would state -- so the net has a clean,
    learnable surface to imitate:

      * volatility   rises with geopolitical risk, unrest, supply-chain friction
                     and the gap between inflation and its target.
      * bias (drift) rises with real growth (growth minus real rates) and a
                     healthy trade balance; falls when policy is tight.
      * risk premium rises with debt loads and sanctions exposure (investors
                     demand more compensation to hold fragile assets).
    """
    m, f, l, ind, g = cs.monetary, cs.fiscal, cs.labor, cs.industrial, cs.geopolitical

    inflation_gap = abs(m.inflation_cpi - m.inflation_target)
    volatility = (
        0.35 * g.geopolitical_risk
        + 0.25 * g.social_unrest_index
        + 0.20 * ind.supply_chain_friction
        + 0.20 * min(1.0, inflation_gap * 10.0)
    )

    real_rate = m.interest_rate - m.inflation_cpi
    directional_bias = (
        0.5 * np.tanh((cs.gdp_growth - real_rate) * 8.0)
        + 0.3 * np.tanh(f.trade_balance_pct * 5.0)
        - 0.2 * np.tanh(g.geopolitical_risk * 2.0)
    )

    risk_premium = (
        0.5 * np.tanh(f.govt_debt_to_gdp - 1.0)
        + 0.3 * g.sanctions_exposure
        + 0.2 * (1.0 - f.sovereign_rating)
    )
    return np.array([
        float(np.clip(volatility, 0.0, 1.0)),
        float(np.clip(directional_bias, -1.0, 1.0)),
        float(np.clip(risk_premium, -1.0, 1.0)),
    ], dtype="float64")


def _randomize(base: CountryState, rng: np.random.Generator) -> CountryState:
    """Create a plausible alternate-universe version of a country (domain rand.).

    We nudge the levers that matter for market reaction within realistic bands,
    so the net sees the full range of economic 'weather', not just today's."""
    cs = base.model_copy(deep=True)
    cs.gdp_growth = float(np.clip(cs.gdp_growth + rng.normal(0, 0.03), -0.15, 0.15))
    cs.monetary.interest_rate = float(np.clip(cs.monetary.interest_rate + rng.normal(0, 0.02), 0.0, 0.25))
    cs.monetary.inflation_cpi = float(np.clip(cs.monetary.inflation_cpi + rng.normal(0, 0.02), -0.02, 0.30))
    cs.fiscal.trade_balance_pct = float(np.clip(cs.fiscal.trade_balance_pct + rng.normal(0, 0.03), -0.20, 0.20))
    cs.fiscal.govt_debt_to_gdp = float(np.clip(cs.fiscal.govt_debt_to_gdp + rng.normal(0, 0.3), 0.1, 3.5))
    cs.fiscal.sovereign_rating = float(np.clip(cs.fiscal.sovereign_rating + rng.normal(0, 0.1), 0.0, 1.0))
    cs.industrial.supply_chain_friction = float(np.clip(cs.industrial.supply_chain_friction + rng.normal(0, 0.15), 0.0, 1.0))
    cs.geopolitical.geopolitical_risk = float(np.clip(cs.geopolitical.geopolitical_risk + rng.normal(0, 0.15), 0.0, 1.0))
    cs.geopolitical.social_unrest_index = float(np.clip(cs.geopolitical.social_unrest_index + rng.normal(0, 0.15), 0.0, 1.0))
    cs.geopolitical.sanctions_exposure = float(np.clip(cs.geopolitical.sanctions_exposure + rng.normal(0, 0.15), 0.0, 1.0))
    return cs


def _build_dataset(samples: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Generate (macro_vector -> reaction) training pairs from the analytic rule."""
    rng = np.random.default_rng(seed)
    world = default_world()
    bases = list(world.countries.values())
    X, Y = [], []
    for _ in range(samples):
        base = bases[rng.integers(0, len(bases))]
        cs = _randomize(base, rng)
        X.append(cs.to_vector())
        Y.append(_analytic_reaction(cs))
    return np.asarray(X, dtype="float64"), np.asarray(Y, dtype="float64")


# ===========================================================================
#  Neural reaction model
# ===========================================================================
def train_world(output: str, samples: int = 20_000, epochs: int = 40,
                batch_size: int = 256, lr: float = 1e-3, seed: int = 42) -> dict:
    try:
        import torch
        import torch.nn as nn
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "torch not installed -- run `make setup-train` on the H200 pod. "
            f"Original error: {exc}"
        )

    torch.manual_seed(seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info("training neural world model on %s (%d samples)", device.upper(), samples)

    X, Y = _build_dataset(samples, seed)
    in_dim = X.shape[1]
    out_dim = Y.shape[1]

    # Normalise inputs (macro numbers span GDP in trillions down to tiny rates).
    x_mean = X.mean(axis=0)
    x_std = X.std(axis=0)
    x_std[x_std < 1e-8] = 1.0
    Xn = (X - x_mean) / x_std

    # 80/20 split so we can report honest validation error.
    n = len(Xn)
    split = int(n * 0.8)
    xt = torch.tensor(Xn[:split], dtype=torch.float32, device=device)
    yt = torch.tensor(Y[:split], dtype=torch.float32, device=device)
    xv = torch.tensor(Xn[split:], dtype=torch.float32, device=device)
    yv = torch.tensor(Y[split:], dtype=torch.float32, device=device)

    class ReactionNet(nn.Module):
        def __init__(self):
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(in_dim, 128), nn.ReLU(),
                nn.Linear(128, 64), nn.ReLU(),
                nn.Linear(64, out_dim), nn.Tanh(),   # outputs bounded in [-1,1]
            )

        def forward(self, x):
            return self.net(x)

    model = ReactionNet().to(device)
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = nn.MSELoss()

    best_val = float("inf")
    for epoch in range(epochs):
        model.train()
        perm = torch.randperm(xt.shape[0], device=device)
        total = 0.0
        for start in range(0, xt.shape[0], batch_size):
            idx = perm[start:start + batch_size]
            opt.zero_grad()
            pred = model(xt[idx])
            loss = loss_fn(pred, yt[idx])
            loss.backward()
            opt.step()
            total += float(loss) * len(idx)
        model.eval()
        with torch.no_grad():
            val = float(loss_fn(model(xv), yv))
        best_val = min(best_val, val)
        if epoch % 5 == 0 or epoch == epochs - 1:
            logger.info("epoch %2d/%d  train_mse=%.5f  val_mse=%.5f",
                        epoch + 1, epochs, total / xt.shape[0], val)

    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    # Save weights + the normalisation so production reproduces inputs exactly.
    torch.save(
        {
            "state_dict": model.state_dict(),
            "in_dim": in_dim,
            "out_dim": out_dim,
            "x_mean": x_mean.tolist(),
            "x_std": x_std.tolist(),
            "output_names": ["expected_volatility", "directional_bias", "risk_premium"],
        },
        out,
    )
    meta = {
        "samples": samples,
        "epochs": epochs,
        "in_dim": in_dim,
        "out_dim": out_dim,
        "val_mse": best_val,
        "device": device,
        "output": str(out),
    }
    (out.parent / f"{out.stem}.meta.json").write_text(json.dumps(meta, indent=2))
    logger.info("saved neural world model -> %s (val_mse=%.5f)", out, best_val)
    return meta


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="train_world.py", description="ECONITH neural world model")
    p.add_argument("--output", default="./models/world/neural_reaction.pt")
    p.add_argument("--samples", type=int, default=20_000)
    p.add_argument("--epochs", type=int, default=40)
    p.add_argument("--batch-size", type=int, default=256)
    p.add_argument("--lr", type=float, default=1e-3)
    p.add_argument("--seed", type=int, default=42)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    train_world(
        output=args.output, samples=args.samples, epochs=args.epochs,
        batch_size=args.batch_size, lr=args.lr, seed=args.seed,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
