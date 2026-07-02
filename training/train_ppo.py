"""ECONITH :: training.train_ppo  (PHASE C -- PPO Apprentices)

Train the three trading apprentices with reinforcement learning.

Economic analogy
----------------
A PPO agent is an apprentice trader dropped onto a trading floor (the labeled
history). Each tick it chooses a stance -- **long** (bet up), **short** (bet
down) or **flat** (stay out) -- then the market reveals what happened next and
the anti-greed reward gives it a grade. Over millions of ticks it learns habits
that score well: catch moves, avoid deep losses, don't over-trade.

We train three specialists, because no single trader is good at everything:

  * ``trend``          -- a momentum rider: looks at longer 5m/15m moves, is
                          taxed harder for churning, so it learns to hold winners.
  * ``mean_reversion`` -- a fade-the-extreme contrarian: leans on order-book
                          imbalance, punished more for downside vol (Sortino).
  * ``scalper``        -- a fast in-and-out trader: targets the 1m horizon and is
                          allowed to trade more often (higher turnover budget).

The Quality Inspector (early_stop) watches each apprentice on the sealed exam and
pulls them off the floor the moment they start memorising instead of learning.

Run it:
    python training/train_ppo.py --agent trend \
        --data ./datasets/processed/quant_labeled.parquet \
        --output ./models/agents/trend_ppo.zip \
        --holdout ./datasets/processed/quant_holdout.parquet
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

from ai.agents.agent_loaders import PPO_FEATURE_COLS  # noqa: E402
from ai.reward.reward import RewardConfig, breakdown_reward  # noqa: E402
from training.early_stop import EarlyStopper  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
)
logger = logging.getLogger("econith.training.train_ppo")

# The market-microstructure columns each apprentice reads as its "screen".
# CANONICAL source of truth lives in ai/agents/agent_loaders.py so the live
# trading desk observes inputs in the EXACT same order it was trained on.
FEATURE_COLS = list(PPO_FEATURE_COLS)

# Per-specialist wiring: which future the agent is graded against, how heavily it
# is taxed for trading, and the transaction cost of flipping its position.
AGENT_PROFILES: dict[str, dict] = {
    "trend": {
        "realized_col": "forward_return_5m",   # ride the longer move
        "cost": 0.0004,
        "max_steps": 4096,
        "reward": dict(w_turnover=0.9, turnover_free=0.05, w_sortino=0.35),
    },
    "mean_reversion": {
        "realized_col": "forward_return_1m",
        "cost": 0.0004,
        "max_steps": 4096,
        "reward": dict(w_turnover=0.6, turnover_free=0.10, w_sortino=0.55),
    },
    "scalper": {
        "realized_col": "forward_return_1m",   # fast, near-term
        "cost": 0.0003,
        "max_steps": 2048,
        "reward": dict(w_turnover=0.25, turnover_free=0.25, w_sortino=0.30),
    },
}


# ===========================================================================
#  Trading environment
# ===========================================================================
def _load_frame(path: str):
    import pandas as pd

    p = Path(path)
    if not p.exists():
        raise SystemExit(f"training data not found: {path} -- run `make data-label` first")
    return pd.read_parquet(p, engine="pyarrow")


def _feature_stats(df) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Column means/stds for z-scoring the observation (a stable 'screen')."""
    import pandas as pd

    cols = [c for c in FEATURE_COLS if c in df.columns]
    mat = df[cols].apply(lambda s: pd.to_numeric(s, errors="coerce")).to_numpy("float64")
    mat = np.nan_to_num(mat, nan=0.0, posinf=0.0, neginf=0.0)
    mean = mat.mean(axis=0)
    std = mat.std(axis=0)
    std[std < 1e-8] = 1.0
    return mean, std, cols


def make_env_class():
    """Build the Gymnasium env class lazily (keeps import cheap when unused)."""
    import gymnasium as gym
    from gymnasium import spaces

    class QuantTradingEnv(gym.Env):
        """A one-asset trading floor replayed from labeled history.

        Observation : z-scored microstructure features (+ current position).
        Action      : 0 = short(-1), 1 = flat(0), 2 = long(+1).
        Reward      : anti-greed shaped P&L of holding that position into the
                      realised forward return, minus the cost of changing stance.
        """

        metadata = {"render_modes": []}

        def __init__(self, features, realized, mean, std, cost, max_steps, reward_cfg):
            super().__init__()
            self._f = features.astype("float32")
            self._r = realized.astype("float64")
            self._mean = mean.astype("float32")
            self._std = std.astype("float32")
            self._cost = float(cost)
            self._max_steps = int(max_steps)
            self._cfg = reward_cfg
            n_feat = self._f.shape[1]
            self.observation_space = spaces.Box(
                low=-10.0, high=10.0, shape=(n_feat + 1,), dtype=np.float32
            )
            self.action_space = spaces.Discrete(3)
            self._i = 0
            self._start = 0
            self._pos = 0.0
            self._equity = 1.0
            self._peak = 1.0
            self._window: list[float] = []

        def _obs(self) -> np.ndarray:
            z = (self._f[self._i] - self._mean) / self._std
            z = np.clip(z, -10.0, 10.0)
            return np.append(z, np.float32(self._pos)).astype(np.float32)

        def reset(self, *, seed=None, options=None):
            super().reset(seed=seed)
            n = len(self._f)
            # Start at a random point so episodes see different market regimes.
            hi = max(1, n - self._max_steps - 1)
            self._start = int(self.np_random.integers(0, hi)) if hi > 1 else 0
            self._i = self._start
            self._pos = 0.0
            self._equity = 1.0
            self._peak = 1.0
            self._window = []
            return self._obs(), {}

        def step(self, action: int):
            target = float(action - 1)  # {0,1,2} -> {-1,0,+1}
            turnover = abs(target - self._pos)          # 0, 1 or 2
            realized = float(self._r[self._i])
            if not np.isfinite(realized):
                realized = 0.0

            # P&L of holding the NEW stance into the next move, minus flip cost.
            step_return = target * realized - self._cost * turnover
            self._equity *= 1.0 + step_return
            self._peak = max(self._peak, self._equity)
            drawdown = max(0.0, 1.0 - self._equity / self._peak) if self._peak > 0 else 0.0
            self._window.append(step_return)
            if len(self._window) > 64:
                self._window.pop(0)

            reward = breakdown_reward(
                step_return=step_return,
                max_drawdown=drawdown,
                equity_returns=self._window,
                turnover=turnover / 2.0,        # normalise to [0,1]
                position_concentration=abs(target),
                config=self._cfg,
            ).reward

            self._pos = target
            self._i += 1
            steps_done = self._i - self._start
            terminated = self._equity <= 0.5        # blew up the account -> episode ends
            truncated = (self._i >= len(self._f) - 1) or (steps_done >= self._max_steps)
            return self._obs(), float(reward), bool(terminated), bool(truncated), {}

    return QuantTradingEnv


# ===========================================================================
#  Holdout evaluation callback (wires in the Quality Inspector)
# ===========================================================================
def make_holdout_callback(eval_env, stopper: EarlyStopper, eval_freq: int, verbose: int = 1):
    from stable_baselines3.common.callbacks import BaseCallback

    class HoldoutEarlyStop(BaseCallback):
        """Every ``eval_freq`` steps, sit the apprentice for the sealed exam.

        We roll the *deterministic* policy across the holdout floor and measure
        the average shaped reward. Because the inspector wants a LOSS (lower =
        better), we feed it the negative reward: a falling exam score => rising
        loss => eventually the whistle blows and training halts.
        """

        def __init__(self):
            super().__init__(verbose)
            self._last = 0

        def _run_holdout(self) -> float:
            obs, _ = eval_env.reset()
            total, steps, done = 0.0, 0, False
            # Deterministic walk-through of the exam floor.
            while not done and steps < eval_env.unwrapped._max_steps:
                action, _ = self.model.predict(obs, deterministic=True)
                obs, reward, term, trunc, _ = eval_env.step(int(action))
                total += reward
                steps += 1
                done = term or trunc
            return total / max(1, steps)

        def _on_step(self) -> bool:
            if self.num_timesteps - self._last < eval_freq:
                return True
            self._last = self.num_timesteps
            avg_reward = self._run_holdout()
            should_stop = stopper.update(-avg_reward)  # reward -> loss
            if self.verbose:
                logger.info(
                    "[holdout] step=%d avg_reward=%.5f best_loss=%.5f",
                    self.num_timesteps, avg_reward, stopper.best,
                )
            return not should_stop  # returning False tells SB3 to stop training

    return HoldoutEarlyStop()


# ===========================================================================
#  Training entry point
# ===========================================================================
def train_ppo(
    agent: str,
    data_path: str,
    output: str,
    holdout_path: str | None = None,
    timesteps: int = 200_000,
    patience: int = 5,
    eval_freq: int = 10_000,
    seed: int = 42,
) -> dict:
    if agent not in AGENT_PROFILES:
        raise SystemExit(f"unknown agent '{agent}'; choose from {list(AGENT_PROFILES)}")

    try:
        from stable_baselines3 import PPO
        from stable_baselines3.common.monitor import Monitor
    except ImportError as exc:  # pragma: no cover - only on machines without the GPU stack
        raise SystemExit(
            "stable-baselines3 not installed -- run `make setup-train` "
            f"(on the H200 pod). Original error: {exc}"
        )

    profile = AGENT_PROFILES[agent]
    reward_cfg = RewardConfig(**profile["reward"])

    df = _load_frame(data_path)
    realized_col = profile["realized_col"]
    if realized_col not in df.columns:
        realized_col = "forward_return_1m"
    mean, std, cols = _feature_stats(df)

    import pandas as pd

    feats = df[cols].apply(lambda s: pd.to_numeric(s, errors="coerce")).to_numpy("float64")
    feats = np.nan_to_num(feats, nan=0.0, posinf=0.0, neginf=0.0)
    realized = np.nan_to_num(pd.to_numeric(df[realized_col], errors="coerce").to_numpy(), nan=0.0)

    EnvClass = make_env_class()
    from stable_baselines3.common.monitor import Monitor  # noqa: F811

    def _mk(features, realized_arr):
        return Monitor(
            EnvClass(features, realized_arr, mean, std,
                     profile["cost"], profile["max_steps"], reward_cfg)
        )

    train_env = _mk(feats, realized)

    callbacks = None
    stopper = EarlyStopper(patience=patience, mode="min")
    if holdout_path and Path(holdout_path).exists():
        hdf = _load_frame(holdout_path)
        hfeats = np.nan_to_num(
            hdf[cols].apply(lambda s: pd.to_numeric(s, errors="coerce")).to_numpy("float64"),
            nan=0.0,
        )
        hcol = realized_col if realized_col in hdf.columns else "forward_return_1m"
        hreal = np.nan_to_num(pd.to_numeric(hdf[hcol], errors="coerce").to_numpy(), nan=0.0)
        eval_env = EnvClass(hfeats, hreal, mean, std,
                            profile["cost"], profile["max_steps"], reward_cfg)
        callbacks = make_holdout_callback(eval_env, stopper, eval_freq)
    else:
        logger.warning("no holdout provided -- training without early stopping")

    logger.info(
        "training PPO[%s] on %d rows (target=%s) for %d timesteps",
        agent, len(df), realized_col, timesteps,
    )
    model = PPO(
        "MlpPolicy",
        train_env,
        verbose=0,
        seed=seed,
        n_steps=2048,
        batch_size=256,
        gamma=0.997,          # care about the near future, not the infinite one
        gae_lambda=0.95,
        ent_coef=0.01,        # a little curiosity so it explores stances
        learning_rate=3e-4,
        device="auto",        # uses the H200 GPU when present, CPU otherwise
    )
    model.learn(total_timesteps=timesteps, callback=callbacks, progress_bar=False)

    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(out))

    # Persist the observation normaliser BESIDE the checkpoint. The live desk
    # (ai/agents/agent_loaders.py) reads this to z-score inputs identically --
    # without it, the served model would see raw, unscaled numbers and mispredict.
    norm_path = out.parent / f"{out.stem}.norm.json"
    norm_path.write_text(json.dumps({
        "cols": cols,
        "mean": mean.tolist(),
        "std": std.tolist(),
    }, indent=2))
    logger.info("saved normaliser -> %s", norm_path)

    metrics = {
        "agent": agent,
        "rows": int(len(df)),
        "timesteps": int(model.num_timesteps),
        "target": realized_col,
        "best_holdout_loss": None if stopper.best is None else float(stopper.best),
        "stopped_epoch": stopper.stopped_epoch,
        "output": str(out),
    }
    (out.parent / f"{out.stem}.metrics.json").write_text(json.dumps(metrics, indent=2))
    logger.info("saved PPO[%s] -> %s (best_holdout_loss=%s)",
                agent, out, metrics["best_holdout_loss"])
    return metrics


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="train_ppo.py", description="ECONITH PPO trainer")
    p.add_argument("--agent", required=True, choices=list(AGENT_PROFILES))
    p.add_argument("--data", default="./datasets/processed/quant_labeled.parquet")
    p.add_argument("--output", default=None, help="checkpoint path (.zip)")
    p.add_argument("--holdout", default="./datasets/processed/quant_holdout.parquet")
    p.add_argument("--timesteps", type=int, default=200_000)
    p.add_argument("--patience", type=int, default=5)
    p.add_argument("--eval-freq", type=int, default=10_000)
    p.add_argument("--seed", type=int, default=42)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = args.output or f"./models/agents/{args.agent}_ppo.zip"
    train_ppo(
        agent=args.agent,
        data_path=args.data,
        output=output,
        holdout_path=args.holdout,
        timesteps=args.timesteps,
        patience=args.patience,
        eval_freq=args.eval_freq,
        seed=args.seed,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
