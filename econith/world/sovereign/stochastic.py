"""ECONITH :: econith.world.sovereign.stochastic — StochasticEngine.

Injects **mean-reverting Ornstein–Uhlenbeck (OU) noise** plus a **jump-diffusion
(compound Poisson) process** into the hub tensor before commit. This is what
turns the deterministic hub step into a stochastic path generator: no two runs
are identical, tiny state differences amplify over time (a controlled butterfly
effect), and heavy-tailed jumps reproduce real-world crash/gap behaviour.

Design contract
---------------
* **Additive**: consumes a *fresh* hub buffer produced by the hub stepper and
  mutates the targeted feature columns in place (zero extra allocation on the
  hot path). Returns the same (C-contiguous, float64) array for chaining.
* **Vectorized**: every process is a single broadcast NumPy op over
  ``(N_HUBS, k)`` — no Python loop over hubs or features.
* **Bounded**: every perturbed feature is clipped to a physical band so a jump
  can rattle the world without producing NaNs / impossible states.
* **Crisis-aware**: the Poisson jump intensity scales with ``market_stress`` so
  shocks cluster during turmoil (volatility clustering / self-excitation proxy).

SDE (per targeted feature, Euler–Maruyama):

    dX = θ(μ − X)·dt  +  σ·√dt·Z   +  J·dN

    Z  ~ N(0, 1)                      (diffusion)
    dN ~ Bernoulli(λ_eff·dt)          (jump arrival, small-dt Poisson approx)
    J  ~ N(jump_mean, jump_std)       (jump size)
    λ_eff = λ·(1 + crisis_gain·stress)
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from econith.world.sovereign.topology import FEATURE_DIM, FEATURE_NAMES, N_HUBS

__all__ = ["FeatureProcess", "StochasticEngine", "default_stochastic"]


@dataclass(slots=True)
class FeatureProcess:
    """OU + jump parameters for a single named feature column."""

    name: str
    theta: float          # mean-reversion speed (per unit time)
    mu: float             # long-run mean the feature reverts toward
    sigma: float          # diffusion volatility
    jump_intensity: float # Poisson arrival rate λ (jumps per unit time)
    jump_mean: float      # mean jump size
    jump_std: float       # jump size dispersion
    lo: float             # physical lower clamp
    hi: float             # physical upper clamp


# Sensible default processes for the load-bearing macro features. These are
# order-of-magnitude reasonable and are meant to be OVERWRITTEN by
# scripts/calibrate_world.py once real FRED/market moments are available.
_DEFAULT_PROCESSES: tuple[FeatureProcess, ...] = (
    #               name                theta   mu       sigma    λ      jμ       jσ      lo      hi
    FeatureProcess("gdp_growth",          0.15, 0.025,   0.010,  0.02, -0.010,  0.006, -0.20, 0.20),
    FeatureProcess("inflation_cpi",       0.10, 0.020,   0.006,  0.03,  0.008,  0.005, -0.05, 0.60),
    FeatureProcess("interest_rate",       0.08, 0.030,   0.004,  0.01,  0.005,  0.004, -0.01, 0.25),
    FeatureProcess("yield_10y",           0.08, 0.040,   0.006,  0.02,  0.007,  0.005, -0.02, 0.35),
    FeatureProcess("fx_spot",             0.05, 0.000,   0.012,  0.03,  0.000,  0.020,  0.01, 1.0e6),
    FeatureProcess("manufacturing_pmi",   0.20, 50.00,   1.200,  0.02, -2.000,  1.500, 30.0, 70.0),
    FeatureProcess("business_confidence", 0.15, 0.550,   0.020,  0.02, -0.030,  0.020,  0.05, 0.98),
    FeatureProcess("consumer_confidence", 0.15, 0.550,   0.020,  0.02, -0.030,  0.020,  0.05, 0.98),
    FeatureProcess("social_unrest_index", 0.06, 0.200,   0.015,  0.03,  0.040,  0.030,  0.00, 1.0),
    FeatureProcess("geopolitical_risk",   0.05, 0.250,   0.015,  0.03,  0.050,  0.040,  0.00, 1.0),
)

# fx_spot is special: it is a *relative* (multiplicative) mean the engine treats
# as a per-hub level, so we skip the absolute-mu reversion for it (theta≈0 pull
# toward its own current level rather than a single global number).
_RELATIVE_FEATURES: frozenset[str] = frozenset({"fx_spot"})


@dataclass(slots=True)
class StochasticEngine:
    """Vectorized OU + jump-diffusion perturbation over the hub tensor.

    Parameters
    ----------
    processes
        One :class:`FeatureProcess` per feature column to perturb. Unlisted
        features are left untouched (the deterministic hub step owns them).
    dt
        Base time-increment per tick (annualised units). ``scale`` multiplies it
        so speed-ups (1x–20x) integrate proportionally more diffusion.
    crisis_gain
        How strongly ``market_stress`` inflates the Poisson jump intensity.
    seed
        RNG seed. A per-run seed makes every simulation a distinct sample path;
        pin it for reproducible regression runs.
    """

    processes: tuple[FeatureProcess, ...]
    dt: float = 1.0 / 252.0
    crisis_gain: float = 4.0
    seed: int | None = None

    # -- derived / runtime (init=False) --------------------------------------
    _cols: np.ndarray = field(init=False)
    _theta: np.ndarray = field(init=False)
    _mu: np.ndarray = field(init=False)
    _sigma: np.ndarray = field(init=False)
    _lam: np.ndarray = field(init=False)
    _jmu: np.ndarray = field(init=False)
    _jsig: np.ndarray = field(init=False)
    _lo: np.ndarray = field(init=False)
    _hi: np.ndarray = field(init=False)
    _relative: np.ndarray = field(init=False)
    _rng: np.random.Generator = field(init=False)
    last_jumps: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        if not self.processes:
            raise ValueError("StochasticEngine requires at least one FeatureProcess")
        cols, theta, mu, sigma, lam, jmu, jsig, lo, hi, rel = ([] for _ in range(10))
        for p in self.processes:
            try:
                idx = FEATURE_NAMES.index(p.name)
            except ValueError as exc:
                raise KeyError(f"unknown feature '{p.name}'") from exc
            cols.append(idx)
            theta.append(p.theta); mu.append(p.mu); sigma.append(p.sigma)
            lam.append(p.jump_intensity); jmu.append(p.jump_mean); jsig.append(p.jump_std)
            lo.append(p.lo); hi.append(p.hi)
            rel.append(1.0 if p.name in _RELATIVE_FEATURES else 0.0)
        self._cols = np.asarray(cols, dtype=np.intp)
        self._theta = np.asarray(theta, dtype=np.float64)
        self._mu = np.asarray(mu, dtype=np.float64)
        self._sigma = np.asarray(sigma, dtype=np.float64)
        self._lam = np.asarray(lam, dtype=np.float64)
        self._jmu = np.asarray(jmu, dtype=np.float64)
        self._jsig = np.asarray(jsig, dtype=np.float64)
        self._lo = np.asarray(lo, dtype=np.float64)
        self._hi = np.asarray(hi, dtype=np.float64)
        self._relative = np.asarray(rel, dtype=bool)
        self._rng = np.random.default_rng(self.seed)

    # -- hot path -------------------------------------------------------------
    def apply(
        self,
        hubs: np.ndarray,
        *,
        market_stress: float = 0.0,
        scale: float = 1.0,
    ) -> np.ndarray:
        """Perturb the targeted feature columns of ``hubs`` IN PLACE.

        ``hubs`` MUST be a fresh, owned ``(N_HUBS, FEATURE_DIM)`` buffer (the one
        returned by the hub stepper). Returns the same array for chaining.
        """
        if hubs.shape != (N_HUBS, FEATURE_DIM):
            raise ValueError(f"hubs shape {hubs.shape} != {(N_HUBS, FEATURE_DIM)}")

        cols = self._cols
        k = cols.size
        dt = self.dt * float(scale)
        sqrt_dt = float(np.sqrt(dt))
        stress = float(max(0.0, market_stress))

        x = hubs[:, cols]  # view (N_HUBS, k)

        # 1) OU mean reversion. Relative features revert toward their own level
        #    (no pull), absolute features revert toward mu.
        target = np.where(self._relative, x, self._mu)
        drift = self._theta * (target - x) * dt

        # 2) Diffusion.
        diffusion = self._sigma * sqrt_dt * self._rng.standard_normal((N_HUBS, k))

        # 3) Compound-Poisson jumps, intensity inflated by crisis stress.
        lam_eff = self._lam * (1.0 + self.crisis_gain * stress)
        arrival = self._rng.random((N_HUBS, k)) < np.clip(lam_eff * dt, 0.0, 1.0)
        jump_size = self._jmu + self._jsig * self._rng.standard_normal((N_HUBS, k))
        jumps = np.where(arrival, jump_size, 0.0)
        self.last_jumps = int(arrival.sum())

        # 4) Commit + clamp to physical bounds (broadcast over hubs).
        x_new = x + drift + diffusion + jumps
        np.clip(x_new, self._lo, self._hi, out=x_new)
        hubs[:, cols] = x_new
        return hubs

    # -- calibration surface --------------------------------------------------
    @classmethod
    def from_coefficients(
        cls,
        coeffs: dict[str, dict[str, float]],
        *,
        dt: float = 1.0 / 252.0,
        crisis_gain: float = 4.0,
        seed: int | None = None,
    ) -> "StochasticEngine":
        """Build from a ``{feature: {theta, mu, sigma, ...}}`` mapping.

        Falls back to the default band for any parameter a calibration omits, so
        a partial calibration (e.g. only mu/sigma matched) still yields a valid
        engine. Features absent from the mapping keep their defaults.
        """
        base = {p.name: p for p in _DEFAULT_PROCESSES}
        procs: list[FeatureProcess] = []
        for name, d in coeffs.items():
            b = base.get(name)
            procs.append(FeatureProcess(
                name=name,
                theta=float(d.get("theta", b.theta if b else 0.1)),
                mu=float(d.get("mu", b.mu if b else 0.0)),
                sigma=float(d.get("sigma", b.sigma if b else 0.01)),
                jump_intensity=float(d.get("jump_intensity", b.jump_intensity if b else 0.02)),
                jump_mean=float(d.get("jump_mean", b.jump_mean if b else 0.0)),
                jump_std=float(d.get("jump_std", b.jump_std if b else 0.01)),
                lo=float(d.get("lo", b.lo if b else -1e9)),
                hi=float(d.get("hi", b.hi if b else 1e9)),
            ))
        # Preserve any default features the calibration didn't touch.
        for name, b in base.items():
            if name not in coeffs:
                procs.append(b)
        return cls(processes=tuple(procs), dt=dt, crisis_gain=crisis_gain, seed=seed)


def default_stochastic(seed: int | None = None) -> StochasticEngine:
    """Factory: the curated default OU + jump processes for the macro features."""
    return StochasticEngine(processes=_DEFAULT_PROCESSES, seed=seed)
