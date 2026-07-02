"""ECONITH :: ai.simulator_engine.macro_vectors

Massive macroeconomic + geopolitical state schema (master plan, Phase 6).

Each country carries 100+ structural features, grouped into five typed vectors:

    MonetaryPolicy      (~20)  interest rates, CPI/PPI, M1/M2/M3, reserves, FX...
    FiscalTrade         (~22)  taxes, debt, deficit, trade balance, FDI, exports...
    LaborDemographics   (~18)  population, participation, unemployment, wages...
    IndustrialResource  (~22)  energy, PMI, supply-chain, oil / chips / rare earths...
    GeopoliticalSentiment(~18) defense, confidence, stability, alliances, cyber...

World-level matrices (per country-pair) carry the geopolitical coupling:
    tariffs[A][B]    -- tariff rate A imposes on B's imports (fraction)
    alliances[A][B]  -- trust / friction score in [0, 1] (1 == close ally)

The schema is intentionally flat-per-vector (plain floats) so it serialises
cheaply to JSON for the dashboard and is trivial to turn into a numeric tensor
for H200 training later (see ``CountryState.to_vector``).
"""
from __future__ import annotations

from pydantic import BaseModel, Field

# ===========================================================================
#  Vector groups
# ===========================================================================


class MonetaryPolicy(BaseModel):
    interest_rate: float = 0.04
    inflation_target: float = 0.02
    inflation_cpi: float = 0.03
    inflation_ppi: float = 0.028
    core_inflation: float = 0.025
    money_supply_m1: float = 1.0e12
    money_supply_m2: float = 4.0e12
    money_supply_m3: float = 6.0e12
    m2_growth: float = 0.05
    velocity_of_money: float = 1.4
    central_bank_reserves: float = 5.0e11
    reserve_requirement: float = 0.10
    discount_rate: float = 0.045
    qe_intensity: float = 0.0
    credit_growth: float = 0.06
    real_interest_rate: float = 0.01
    yield_2y: float = 0.04
    yield_10y: float = 0.045
    fx_spot: float = 1.0          # local currency per USD (USD == 1.0)
    fx_forward_12m: float = 1.0


class FiscalTrade(BaseModel):
    corporate_tax: float = 0.21
    individual_tax: float = 0.30
    vat: float = 0.10
    capital_gains_tax: float = 0.15
    govt_debt_to_gdp: float = 0.90
    budget_deficit_pct: float = 0.04
    govt_spending_pct: float = 0.38
    trade_balance_pct: float = -0.02
    current_account_pct: float = -0.02
    export_index: float = 100.0
    import_index: float = 100.0
    fdi_inflow: float = 2.0e11
    fdi_outflow: float = 1.5e11
    foreign_reserves: float = 5.0e11
    sovereign_rating: float = 0.85   # 0..1 (1 == AAA)
    avg_import_tariff: float = 0.03
    avg_export_subsidy: float = 0.01
    customs_revenue_pct: float = 0.01
    public_investment_pct: float = 0.04
    tax_revenue_pct: float = 0.25
    informal_economy_pct: float = 0.12
    ease_of_business: float = 0.75   # 0..1
    property_tax: float = 0.01
    healthcare_budget_pct: float = 0.08
    education_budget_pct: float = 0.05


class LaborDemographics(BaseModel):
    population: float = 100_000_000.0
    workforce_participation: float = 0.62
    unemployment: float = 0.05
    underemployment: float = 0.07
    wage_growth: float = 0.03
    productivity_index: float = 100.0
    dependency_ratio: float = 0.55
    birth_rate: float = 0.012
    death_rate: float = 0.008
    net_migration: float = 0.001
    median_age: float = 38.0
    urbanization: float = 0.70
    gini_coefficient: float = 0.38
    literacy_rate: float = 0.95
    tertiary_education: float = 0.45
    healthcare_index: float = 0.75
    labor_cost_index: float = 100.0
    union_density: float = 0.15
    youth_unemployment: float = 0.12
    retirement_age: float = 65.0
    minimum_wage: float = 7.25        # local currency / hour
    immigration_quota: float = 0.002  # share of population / yr


class IndustrialResource(BaseModel):
    manufacturing_pmi: float = 51.0
    services_pmi: float = 52.0
    industrial_production: float = 100.0
    capacity_utilization: float = 0.78
    energy_production: float = 100.0
    energy_consumption: float = 100.0
    energy_independence: float = 0.80   # production / consumption ratio capped
    oil_reserves: float = 5.0e9         # barrels
    gas_reserves: float = 2.0e12
    coal_reserves: float = 1.0e10
    rare_earth_reserves: float = 1.0e6  # tonnes
    semiconductor_capacity: float = 0.10   # share of global
    agricultural_output: float = 100.0
    food_security_index: float = 0.80
    water_stress_index: float = 0.30
    supply_chain_friction: float = 0.20    # 0 (smooth) .. 1 (broken)
    logistics_performance: float = 0.75
    rnd_spending_pct: float = 0.025
    patents_index: float = 100.0
    infrastructure_index: float = 0.75
    carbon_intensity: float = 0.40
    renewable_share: float = 0.25
    clean_energy_subsidy: float = 0.02
    tech_export_controls: float = 0.10
    corporate_rnd_subsidy: float = 0.015


class GeopoliticalSentiment(BaseModel):
    defense_spending_pct: float = 0.025
    military_strength_index: float = 0.50
    political_stability: float = 0.70    # 0..1
    corruption_index: float = 0.55       # 0 (corrupt) .. 1 (clean)
    press_freedom: float = 0.65
    consumer_confidence: float = 0.60
    business_confidence: float = 0.60
    social_unrest_index: float = 0.20    # 0 calm .. 1 unrest
    cyber_capability: float = 0.50
    soft_power_index: float = 0.55
    diplomatic_reach: float = 0.50
    sanctions_exposure: float = 0.10
    energy_security: float = 0.70
    nuclear_capability: float = 0.0
    alliance_cohesion: float = 0.60
    public_approval: float = 0.50
    election_risk: float = 0.20
    geopolitical_risk: float = 0.25


# ===========================================================================
#  Country + World state
# ===========================================================================

_VECTOR_GROUPS = ("monetary", "fiscal", "labor", "industrial", "geopolitical")


class CountryState(BaseModel):
    code: str
    name: str
    continent: str
    gdp: float = 1.0e12            # nominal USD
    gdp_growth: float = 0.025
    gdp_per_capita: float = 10_000.0

    monetary: MonetaryPolicy = Field(default_factory=MonetaryPolicy)
    fiscal: FiscalTrade = Field(default_factory=FiscalTrade)
    labor: LaborDemographics = Field(default_factory=LaborDemographics)
    industrial: IndustrialResource = Field(default_factory=IndustrialResource)
    geopolitical: GeopoliticalSentiment = Field(default_factory=GeopoliticalSentiment)

    # -- grouped access ---------------------------------------------------
    def group(self, name: str) -> BaseModel:
        return getattr(self, name)

    def get_field(self, group: str, field: str) -> float | None:
        g = getattr(self, group, None)
        if g is None or not hasattr(g, field):
            return None
        return float(getattr(g, field))

    def set_field(self, group: str, field: str, value: float) -> bool:
        g = getattr(self, group, None)
        if g is None or not hasattr(g, field):
            return False
        setattr(g, field, float(value))
        return True

    def add_field(self, group: str, field: str, delta: float) -> bool:
        cur = self.get_field(group, field)
        if cur is None:
            return False
        return self.set_field(group, field, cur + delta)

    # -- legacy flat summary (kept for dashboard backward-compat) ---------
    def summary(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "continent": self.continent,
            "gdp": round(self.gdp, 2),
            "gdp_growth": round(self.gdp_growth, 4),
            "gdp_per_capita": round(self.gdp_per_capita, 2),
            "inflation": round(self.monetary.inflation_cpi, 4),
            "interest_rate": round(self.monetary.interest_rate, 4),
            "tax": round(self.fiscal.corporate_tax, 4),
            "population": round(self.labor.population),
            "unemployment": round(self.labor.unemployment, 4),
        }

    def vectors(self) -> dict:
        return {
            "monetary": self.monetary.model_dump(),
            "fiscal": self.fiscal.model_dump(),
            "labor": self.labor.model_dump(),
            "industrial": self.industrial.model_dump(),
            "geopolitical": self.geopolitical.model_dump(),
        }

    def to_dict(self) -> dict:
        return {**self.summary(), "vectors": self.vectors()}

    def to_vector(self) -> list[float]:
        """Flat numeric tensor of every feature (for H200 training pipelines)."""
        out: list[float] = [self.gdp, self.gdp_growth, self.gdp_per_capita]
        for grp in _VECTOR_GROUPS:
            model: BaseModel = getattr(self, grp)
            out.extend(float(v) for v in model.model_dump().values())
        return out

    @staticmethod
    def feature_count_template() -> int:
        return (
            3
            + len(MonetaryPolicy().model_dump())
            + len(FiscalTrade().model_dump())
            + len(LaborDemographics().model_dump())
            + len(IndustrialResource().model_dump())
            + len(GeopoliticalSentiment().model_dump())
        )


class WorldState(BaseModel):
    sim_day: int = 0
    countries: dict[str, CountryState] = Field(default_factory=dict)
    tariffs: dict[str, dict[str, float]] = Field(default_factory=dict)
    alliances: dict[str, dict[str, float]] = Field(default_factory=dict)

    def codes(self) -> list[str]:
        return list(self.countries.keys())

    def tariff(self, src: str, dst: str) -> float:
        return self.tariffs.get(src, {}).get(dst, 0.0)

    def set_tariff(self, src: str, dst: str, value: float) -> None:
        self.tariffs.setdefault(src, {})[dst] = max(0.0, min(1.0, value))

    def alliance(self, a: str, b: str) -> float:
        return self.alliances.get(a, {}).get(b, 0.5)

    def to_dict(self) -> dict:
        return {
            "sim_day": self.sim_day,
            "countries": {c: s.to_dict() for c, s in self.countries.items()},
            "tariffs": self.tariffs,
            "alliances": self.alliances,
        }

    def aggregate(self) -> dict:
        states = list(self.countries.values())
        total_pop = sum(s.labor.population for s in states) or 1.0

        def wavg(getter) -> float:
            return sum(getter(s) * s.labor.population for s in states) / total_pop

        return {
            "gdp_growth": round(wavg(lambda s: s.gdp_growth), 4),
            "inflation": round(wavg(lambda s: s.monetary.inflation_cpi), 4),
            "interest_rate": round(wavg(lambda s: s.monetary.interest_rate), 4),
            "tax": round(wavg(lambda s: s.fiscal.corporate_tax), 4),
            "unemployment": round(wavg(lambda s: s.labor.unemployment), 4),
            "population": round(total_pop),
            "gdp": round(sum(s.gdp for s in states), 2),
            "trade_tension": round(self._trade_tension(), 4),
        }

    def _trade_tension(self) -> float:
        """Global tension proxy: mean tariff across all ordered pairs."""
        vals: list[float] = []
        for src, row in self.tariffs.items():
            for dst, rate in row.items():
                if src != dst:
                    vals.append(rate)
        return sum(vals) / len(vals) if vals else 0.0


# ===========================================================================
#  Default world factory (6 majors incl. VNM per spec)
# ===========================================================================

def _country(
    code: str,
    name: str,
    continent: str,
    gdp: float,
    growth: float,
    per_capita: float,
    population: float,
    monetary: dict | None = None,
    fiscal: dict | None = None,
    labor: dict | None = None,
    industrial: dict | None = None,
    geopolitical: dict | None = None,
) -> CountryState:
    return CountryState(
        code=code,
        name=name,
        continent=continent,
        gdp=gdp,
        gdp_growth=growth,
        gdp_per_capita=per_capita,
        monetary=MonetaryPolicy(**(monetary or {})),
        fiscal=FiscalTrade(**(fiscal or {})),
        labor=LaborDemographics(population=population, **(labor or {})),
        industrial=IndustrialResource(**(industrial or {})),
        geopolitical=GeopoliticalSentiment(**(geopolitical or {})),
    )


def default_world() -> WorldState:
    """Construct a plausible 6-country starting world."""
    countries = {
        "USA": _country(
            "USA", "United States", "North America",
            gdp=27.4e12, growth=0.025, per_capita=82_000, population=335_000_000,
            monetary={"interest_rate": 0.0525, "inflation_cpi": 0.032, "fx_spot": 1.0},
            fiscal={"corporate_tax": 0.21, "govt_debt_to_gdp": 1.22, "trade_balance_pct": -0.035},
            industrial={"semiconductor_capacity": 0.12, "manufacturing_pmi": 49.5},
            geopolitical={"defense_spending_pct": 0.034, "military_strength_index": 0.95,
                          "political_stability": 0.62, "nuclear_capability": 1.0},
        ),
        "CHN": _country(
            "CHN", "China", "Asia",
            gdp=17.8e12, growth=0.048, per_capita=12_600, population=1_412_000_000,
            monetary={"interest_rate": 0.032, "inflation_cpi": 0.018, "fx_spot": 7.2},
            fiscal={"corporate_tax": 0.25, "govt_debt_to_gdp": 0.83, "trade_balance_pct": 0.035,
                    "export_index": 130.0},
            industrial={"semiconductor_capacity": 0.16, "manufacturing_pmi": 50.8,
                        "rare_earth_reserves": 4.4e7},
            geopolitical={"defense_spending_pct": 0.017, "military_strength_index": 0.80,
                          "political_stability": 0.68, "nuclear_capability": 1.0},
        ),
        "VNM": _country(
            "VNM", "Vietnam", "Asia",
            gdp=0.43e12, growth=0.062, per_capita=4_300, population=99_000_000,
            monetary={"interest_rate": 0.045, "inflation_cpi": 0.034, "fx_spot": 24_500.0},
            fiscal={"corporate_tax": 0.20, "govt_debt_to_gdp": 0.37, "trade_balance_pct": 0.02,
                    "export_index": 120.0},
            industrial={"manufacturing_pmi": 51.5, "semiconductor_capacity": 0.01},
            labor={"workforce_participation": 0.74, "wage_growth": 0.06},
            geopolitical={"defense_spending_pct": 0.023, "political_stability": 0.66},
        ),
        "JPN": _country(
            "JPN", "Japan", "Asia",
            gdp=4.2e12, growth=0.011, per_capita=33_800, population=124_000_000,
            monetary={"interest_rate": 0.005, "inflation_cpi": 0.026, "fx_spot": 150.0},
            fiscal={"corporate_tax": 0.297, "govt_debt_to_gdp": 2.60},
            industrial={"semiconductor_capacity": 0.10, "manufacturing_pmi": 49.0},
            labor={"median_age": 49.0, "unemployment": 0.026},
            geopolitical={"defense_spending_pct": 0.011, "political_stability": 0.78},
        ),
        "IND": _country(
            "IND", "India", "Asia",
            gdp=3.7e12, growth=0.068, per_capita=2_600, population=1_429_000_000,
            monetary={"interest_rate": 0.065, "inflation_cpi": 0.051, "fx_spot": 83.0},
            fiscal={"corporate_tax": 0.252, "govt_debt_to_gdp": 0.82},
            industrial={"manufacturing_pmi": 56.0, "semiconductor_capacity": 0.01},
            labor={"median_age": 28.0, "workforce_participation": 0.55},
            geopolitical={"defense_spending_pct": 0.024, "nuclear_capability": 1.0},
        ),
        "DEU": _country(
            "DEU", "Germany", "Europe",
            gdp=4.5e12, growth=0.006, per_capita=53_500, population=84_000_000,
            monetary={"interest_rate": 0.040, "inflation_cpi": 0.029, "fx_spot": 0.92},
            fiscal={"corporate_tax": 0.30, "govt_debt_to_gdp": 0.64, "trade_balance_pct": 0.05},
            industrial={"manufacturing_pmi": 47.0, "semiconductor_capacity": 0.04,
                        "renewable_share": 0.46},
            geopolitical={"defense_spending_pct": 0.015, "political_stability": 0.80},
        ),
    }

    codes = list(countries.keys())
    # Base tariff matrix (~3% baseline, 0 self).
    tariffs = {
        a: {b: (0.0 if a == b else 0.03) for b in codes} for a in codes
    }
    # Alliance / trust matrix (1.0 self). Hand-tuned blocs.
    base_trust = {
        ("USA", "JPN"): 0.9, ("USA", "DEU"): 0.85, ("USA", "IND"): 0.7,
        ("USA", "VNM"): 0.6, ("USA", "CHN"): 0.35,
        ("CHN", "VNM"): 0.55, ("CHN", "IND"): 0.4, ("CHN", "JPN"): 0.45,
        ("CHN", "DEU"): 0.55, ("JPN", "DEU"): 0.8, ("JPN", "IND"): 0.7,
        ("DEU", "IND"): 0.7, ("VNM", "JPN"): 0.7, ("VNM", "IND"): 0.65,
        ("VNM", "DEU"): 0.6, ("IND", "DEU"): 0.7,
    }
    alliances = {a: {b: (1.0 if a == b else 0.5) for b in codes} for a in codes}
    for (a, b), v in base_trust.items():
        alliances[a][b] = v
        alliances[b][a] = v

    return WorldState(sim_day=0, countries=countries, tariffs=tariffs, alliances=alliances)
