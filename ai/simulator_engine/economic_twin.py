"""ECONITH :: ai.simulator_engine.economic_twin

Economic Digital Twin (master plan, Phase 6).

Models nation-level macro variables (GDP, inflation, interest rate, tax) and
micro/social variables (population, unemployment). Each simulated day the twin
steps each country's state forward with simple, transparent dynamics:

    * higher interest rate cools inflation and dampens GDP growth,
    * higher inflation lifts unemployment slightly (short-run trade-off),
    * population drifts on a slow growth rate.

These are deliberately simple, legible relationships -- a foundation for richer
agent-based behaviour in the World Kernel (Phase 7).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(slots=True)
class Country:
    name: str
    gdp_growth: float = 0.028     # annual, fractional (2.8%)
    inflation: float = 0.034      # 3.4%
    interest_rate: float = 0.0525  # 5.25%
    tax: float = 0.21             # 21%
    population: float = 331_000_000.0
    unemployment: float = 0.041   # 4.1%

    def step(self) -> None:
        """Advance one simulated day with bounded macro dynamics."""
        # Interest rate above a neutral ~2.5% cools inflation.
        rate_gap = self.interest_rate - 0.025
        self.inflation = _clamp(self.inflation - 0.02 * rate_gap, -0.02, 0.30)
        # Tight policy + high inflation dampen growth.
        self.gdp_growth = _clamp(
            self.gdp_growth - 0.05 * rate_gap - 0.03 * max(0.0, self.inflation - 0.02),
            -0.15, 0.15,
        )
        # Okun-style: weak growth lifts unemployment.
        self.unemployment = _clamp(
            self.unemployment - 0.10 * self.gdp_growth + 0.02 * max(0.0, self.inflation - 0.03),
            0.01, 0.40,
        )
        # Slow population drift tied to growth.
        self.population *= 1.0 + 0.0001 * (1.0 + self.gdp_growth)

    def snapshot(self) -> dict:
        d = asdict(self)
        d["population"] = round(self.population)
        for k in ("gdp_growth", "inflation", "interest_rate", "tax", "unemployment"):
            d[k] = round(d[k], 4)
        return d


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


@dataclass
class EconomicTwin:
    """A collection of countries forming the simulated economy."""

    countries: dict[str, Country] = field(default_factory=dict)

    @classmethod
    def default(cls) -> EconomicTwin:
        return cls(
            countries={
                "USA": Country("USA", 0.028, 0.034, 0.0525, 0.21, 331_000_000),
                "CHN": Country("CHN", 0.048, 0.021, 0.0320, 0.25, 1_412_000_000),
                "EUR": Country("EUR", 0.012, 0.029, 0.0400, 0.27, 447_000_000),
            }
        )

    def step(self) -> None:
        for country in self.countries.values():
            country.step()

    def aggregate(self) -> dict:
        """Population-weighted global macro aggregate + per-country detail."""
        countries = list(self.countries.values())
        total_pop = sum(c.population for c in countries) or 1.0

        def wavg(attr: str) -> float:
            return sum(getattr(c, attr) * c.population for c in countries) / total_pop

        return {
            "global": {
                "gdp_growth": round(wavg("gdp_growth"), 4),
                "inflation": round(wavg("inflation"), 4),
                "interest_rate": round(wavg("interest_rate"), 4),
                "tax": round(wavg("tax"), 4),
                "unemployment": round(wavg("unemployment"), 4),
                "population": round(total_pop),
            },
            "countries": {name: c.snapshot() for name, c in self.countries.items()},
        }
