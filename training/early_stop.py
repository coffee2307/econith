"""ECONITH :: training.early_stop  (The Quality Inspector)

Stop apprentices who are memorising instead of learning.

Economic analogy
----------------
Imagine an intern who aces every practice quiz because they secretly memorised
the answer sheet -- but flunks the real, unseen exam. That's **overfitting**: a
model that looks brilliant on training data but loses money on new markets.

This inspector watches the score on the sealed final exam (``quant_holdout``).
As long as that score keeps improving, training continues. The moment it starts
getting WORSE for ``patience`` checks in a row, the inspector blows the whistle
(`EarlyStoppingException`) and we keep the last good version. We would rather ship
a modest, honest model than a flashy one that only memorised the simulation.

Two ways to use it:
  * ``EarlyStopper`` -- a tiny state machine: feed it a validation loss, it tells
    you whether to stop and remembers the best score so far.
  * ``holdout_loss`` -- a ready-made scorer that measures a predictor's error on
    the labeled holdout set (lower == better generalisation).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

logger = logging.getLogger("econith.training.early_stop")


class EarlyStoppingException(Exception):
    """Raised (or signalled) when validation loss stops improving.

    Carries the epoch and the best loss so the caller can log exactly why the
    apprentice was pulled off the floor.
    """

    def __init__(self, epoch: int, best_loss: float, message: str = "") -> None:
        self.epoch = epoch
        self.best_loss = best_loss
        super().__init__(
            message or f"early stop at epoch {epoch}: best holdout loss {best_loss:.6f}"
        )


@dataclass
class EarlyStopper:
    """Patience-based overfitting guard.

    Parameters
    ----------
    patience
        How many consecutive non-improving checks we tolerate before stopping.
    min_delta
        Minimum improvement to count as "better" (ignores meaningless noise).
    mode
        ``"min"`` for losses (lower is better) -- the default. ``"max"`` if you
        feed it a reward/score where higher is better.
    """

    patience: int = 5
    min_delta: float = 1e-4
    mode: str = "min"
    best: float = field(init=False, default=None)  # type: ignore[assignment]
    best_epoch: int = field(init=False, default=0)
    _bad_checks: int = field(init=False, default=0)
    _epoch: int = field(init=False, default=0)

    def _is_better(self, value: float) -> bool:
        if self.best is None:
            return True
        if self.mode == "max":
            return value > self.best + self.min_delta
        return value < self.best - self.min_delta

    def update(self, value: float) -> bool:
        """Record a new validation score. Returns ``True`` if training should stop."""
        self._epoch += 1
        if self._is_better(value):
            self.best = value
            self.best_epoch = self._epoch
            self._bad_checks = 0
            logger.info("epoch %d: holdout improved to %.6f", self._epoch, value)
            return False

        self._bad_checks += 1
        logger.info(
            "epoch %d: no improvement (%.6f vs best %.6f) [%d/%d]",
            self._epoch, value, self.best, self._bad_checks, self.patience,
        )
        return self._bad_checks >= self.patience

    def check(self, value: float) -> None:
        """Like :meth:`update` but raises :class:`EarlyStoppingException` on stop."""
        if self.update(value):
            raise EarlyStoppingException(self._epoch, float(self.best))

    @property
    def stopped_epoch(self) -> int:
        return self._epoch


# ===========================================================================
#  Holdout scoring helpers
# ===========================================================================
def _feature_matrix(df, feature_cols: list[str]):
    """Extract a clean float matrix for the given feature columns."""
    import numpy as np

    mat = df[feature_cols].apply(  # type: ignore[call-overload]
        lambda s: __import__("pandas").to_numeric(s, errors="coerce")
    ).to_numpy(dtype="float64")
    return np.nan_to_num(mat, nan=0.0, posinf=0.0, neginf=0.0)


def holdout_loss(
    predict_fn: Callable[["object"], "object"],
    holdout_path: str,
    feature_cols: list[str],
    target_col: str = "forward_return_1m",
) -> float:
    """Mean-squared error of ``predict_fn`` on the sealed holdout set.

    ``predict_fn`` takes the feature matrix (N x F) and returns N predictions.
    Lower is better -- it means the model's guesses about the near future are
    close to what actually happened on data it never trained on.
    """
    import numpy as np
    import pandas as pd

    path = Path(holdout_path)
    if not path.exists():
        raise FileNotFoundError(f"holdout not found: {holdout_path}")
    df = pd.read_parquet(path, engine="pyarrow")
    if target_col not in df.columns:
        raise KeyError(f"holdout missing target column '{target_col}'")

    x = _feature_matrix(df, feature_cols)
    y = pd.to_numeric(df[target_col], errors="coerce").to_numpy(dtype="float64")
    y = np.nan_to_num(y, nan=0.0)
    preds = np.asarray(predict_fn(x), dtype="float64").reshape(-1)
    preds = np.nan_to_num(preds, nan=0.0)
    return float(np.mean((preds - y) ** 2))
