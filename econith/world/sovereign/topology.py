"""ECONITH :: TITAN world topology — 50 Core Hubs + 100 Proxy Nodes.

Topology is pure data: hub codes, proxy codes, regional clusters, and the
sparse Hub→Proxy correlation weights. Consumed by the tensor runtime and the
dashboard aggregation layer. Deterministic; no I/O.
"""
from __future__ import annotations

from typing import Final

__all__ = [
    "HUB_CODES",
    "PROXY_CODES",
    "ALL_CODES",
    "N_HUBS",
    "N_PROXIES",
    "N_NODES",
    "FEATURE_DIM",
    "FEATURE_NAMES",
    "REGIONAL_CLUSTERS",
    "PROXY_WEIGHTS",
    "hub_index",
    "proxy_index",
]

# ---------------------------------------------------------------------------
# Feature schema — 113 continuous macros (matches CountryState.to_vector())
# 3 top-level + 20 monetary + 25 fiscal + 22 labor + 25 industrial + 18 geo
# ---------------------------------------------------------------------------
FEATURE_DIM: Final[int] = 113

FEATURE_NAMES: Final[tuple[str, ...]] = (
    # top-level
    "gdp", "gdp_growth", "gdp_per_capita",
    # monetary (20)
    "interest_rate", "inflation_target", "inflation_cpi", "inflation_ppi",
    "core_inflation", "money_supply_m1", "money_supply_m2", "money_supply_m3",
    "m2_growth", "velocity_of_money", "central_bank_reserves",
    "reserve_requirement", "discount_rate", "qe_intensity", "credit_growth",
    "real_interest_rate", "yield_2y", "yield_10y", "fx_spot", "fx_forward_12m",
    # fiscal (25)
    "corporate_tax", "individual_tax", "vat", "capital_gains_tax",
    "govt_debt_to_gdp", "budget_deficit_pct", "govt_spending_pct",
    "trade_balance_pct", "current_account_pct", "export_index", "import_index",
    "fdi_inflow", "fdi_outflow", "foreign_reserves", "sovereign_rating",
    "avg_import_tariff", "avg_export_subsidy", "customs_revenue_pct",
    "public_investment_pct", "tax_revenue_pct", "informal_economy_pct",
    "ease_of_business", "property_tax", "healthcare_budget_pct",
    "education_budget_pct",
    # labor (22)
    "population", "workforce_participation", "unemployment", "underemployment",
    "wage_growth", "productivity_index", "dependency_ratio", "birth_rate",
    "death_rate", "net_migration", "median_age", "urbanization",
    "gini_coefficient", "literacy_rate", "tertiary_education",
    "healthcare_index", "labor_cost_index", "union_density",
    "youth_unemployment", "retirement_age", "minimum_wage", "immigration_quota",
    # industrial (25)
    "manufacturing_pmi", "services_pmi", "industrial_production",
    "capacity_utilization", "energy_production", "energy_consumption",
    "energy_independence", "oil_reserves", "gas_reserves", "coal_reserves",
    "rare_earth_reserves", "semiconductor_capacity", "agricultural_output",
    "food_security_index", "water_stress_index", "supply_chain_friction",
    "logistics_performance", "rnd_spending_pct", "patents_index",
    "infrastructure_index", "carbon_intensity", "renewable_share",
    "clean_energy_subsidy", "tech_export_controls", "corporate_rnd_subsidy",
    # geopolitical (18)
    "defense_spending_pct", "military_strength_index", "political_stability",
    "corruption_index", "press_freedom", "consumer_confidence",
    "business_confidence", "social_unrest_index", "cyber_capability",
    "soft_power_index", "diplomatic_reach", "sanctions_exposure",
    "energy_security", "nuclear_capability", "alliance_cohesion",
    "public_approval", "election_risk", "geopolitical_risk",
)
assert len(FEATURE_NAMES) == FEATURE_DIM

# ---------------------------------------------------------------------------
# Tier 1 — 50 Full-Fidelity Core Hubs
# ---------------------------------------------------------------------------
HUB_CODES: Final[tuple[str, ...]] = (
    "USA", "CHN", "DEU", "JPN", "IND", "GBR", "FRA", "BRA", "VNM", "SAU",
    "CAN", "MEX", "ARG", "CHL", "COL",
    "ITA", "ESP", "NLD", "CHE", "SWE", "POL", "BEL", "AUT", "NOR", "DNK",
    "IRL", "FIN", "PRT", "GRC", "CZE",
    "KOR", "AUS", "IDN", "THA", "MYS", "SGP", "PHL", "TWN", "NZL", "HKG",
    "ARE", "TUR", "ISR", "EGY", "ZAF", "NGA", "RUS", "KAZ", "PAK", "BGD",
)
assert len(HUB_CODES) == 50
N_HUBS: Final[int] = 50

# ---------------------------------------------------------------------------
# Tier 2 — 100 Derivative Proxy Nodes
# ---------------------------------------------------------------------------
PROXY_CODES: Final[tuple[str, ...]] = (
    "HUN", "ROU", "BGR", "HRV", "SVK", "SVN", "LTU", "LVA", "EST", "LUX",
    "MLT", "CYP", "ISL", "UKR", "SRB", "BIH", "ALB", "MKD", "MDA", "GEO",
    "KHM", "LAO", "MMR", "BRN", "MNG", "NPL", "LKA", "MDV", "FJI", "PNG",
    "MAC", "BTN", "TLS", "VUT", "WSM", "TON", "SLB", "KIR", "MHL", "PLW",
    "FSM", "NRU", "TUV", "ASM", "GUM",
    "PER", "URY", "PRY", "BOL", "ECU", "VEN", "CRI", "PAN", "GTM", "HND",
    "SLV", "NIC", "DOM", "JAM", "TTO", "BRB", "BHS", "BLZ", "GUY", "SUR",
    "QAT", "KWT", "BHR", "OMN", "JOR", "LBN", "IRQ", "IRN", "YEM", "MAR",
    "TUN", "DZA", "LBY", "SDN", "ETH", "KEN", "TZA", "UGA", "GHA", "CIV",
    "SEN", "CMR", "AGO", "MOZ", "ZWE",
    "BLR", "AZE", "ARM", "UZB", "TKM", "TJK", "KGZ", "AFG", "PRK", "CUB",
)
assert len(PROXY_CODES) == 100
N_PROXIES: Final[int] = 100
N_NODES: Final[int] = N_HUBS + N_PROXIES
ALL_CODES: Final[tuple[str, ...]] = HUB_CODES + PROXY_CODES

REGIONAL_CLUSTERS: Final[dict[str, tuple[str, ...]]] = {
    "NorthAmerica": ("USA", "CAN", "MEX"),
    "SouthAmerica": ("BRA", "ARG", "CHL", "COL", "PER", "URY", "PRY", "BOL", "ECU", "VEN"),
    "WesternEurope": (
        "DEU", "GBR", "FRA", "ITA", "ESP", "NLD", "CHE", "BEL", "AUT", "IRL", "PRT", "LUX",
    ),
    "NorthernEurope": ("SWE", "NOR", "DNK", "FIN", "ISL", "EST", "LVA", "LTU"),
    "CentralEastEurope": (
        "POL", "CZE", "HUN", "ROU", "BGR", "HRV", "SVK", "SVN", "GRC", "UKR", "SRB",
    ),
    "EastAsia": ("CHN", "JPN", "KOR", "TWN", "HKG", "MNG", "MAC", "PRK"),
    "SouthAsia": ("IND", "PAK", "BGD", "LKA", "NPL", "MDV", "BTN", "AFG"),
    "SEAsia": ("VNM", "IDN", "THA", "MYS", "SGP", "PHL", "KHM", "LAO", "MMR", "BRN", "TLS"),
    "Oceania": ("AUS", "NZL", "PNG", "FJI", "VUT", "WSM", "TON", "SLB"),
    "MENA": (
        "SAU", "ARE", "TUR", "ISR", "EGY", "QAT", "KWT", "BHR", "OMN", "JOR",
        "LBN", "IRQ", "IRN", "YEM", "MAR", "TUN", "DZA", "LBY",
    ),
    "SubSaharanAfrica": (
        "ZAF", "NGA", "ETH", "KEN", "TZA", "UGA", "GHA", "CIV", "SEN", "CMR",
        "AGO", "MOZ", "ZWE", "SDN",
    ),
    "Eurasia": ("RUS", "KAZ", "BLR", "AZE", "ARM", "UZB", "TKM", "TJK", "KGZ", "GEO", "MDA"),
    "Caribbean": (
        "DOM", "JAM", "TTO", "BRB", "BHS", "CUB", "CRI", "PAN", "GTM", "HND",
        "SLV", "NIC", "BLZ", "GUY", "SUR",
    ),
}


def _w(*pairs: tuple[str, float]) -> dict[str, float]:
    total = sum(w for _, w in pairs) or 1.0
    return {h: w / total for h, w in pairs}


PROXY_WEIGHTS: Final[dict[str, dict[str, float]]] = {
    "HUN": _w(("DEU", 0.65), ("AUT", 0.20), ("FRA", 0.15)),
    "ROU": _w(("DEU", 0.55), ("ITA", 0.25), ("FRA", 0.20)),
    "BGR": _w(("DEU", 0.45), ("TUR", 0.30), ("GRC", 0.25)),
    "HRV": _w(("DEU", 0.50), ("ITA", 0.30), ("AUT", 0.20)),
    "SVK": _w(("DEU", 0.70), ("CZE", 0.20), ("AUT", 0.10)),
    "SVN": _w(("DEU", 0.55), ("ITA", 0.25), ("AUT", 0.20)),
    "LTU": _w(("DEU", 0.40), ("POL", 0.35), ("SWE", 0.25)),
    "LVA": _w(("DEU", 0.35), ("SWE", 0.35), ("POL", 0.30)),
    "EST": _w(("FIN", 0.45), ("SWE", 0.35), ("DEU", 0.20)),
    "LUX": _w(("DEU", 0.40), ("FRA", 0.35), ("BEL", 0.25)),
    "MLT": _w(("ITA", 0.50), ("GBR", 0.30), ("FRA", 0.20)),
    "CYP": _w(("GRC", 0.45), ("GBR", 0.30), ("TUR", 0.25)),
    "ISL": _w(("NOR", 0.45), ("GBR", 0.35), ("USA", 0.20)),
    "UKR": _w(("DEU", 0.40), ("POL", 0.35), ("USA", 0.25)),
    "SRB": _w(("DEU", 0.40), ("ITA", 0.30), ("TUR", 0.30)),
    "BIH": _w(("DEU", 0.40), ("ITA", 0.35), ("TUR", 0.25)),
    "ALB": _w(("ITA", 0.45), ("GRC", 0.30), ("TUR", 0.25)),
    "MKD": _w(("DEU", 0.40), ("GRC", 0.35), ("TUR", 0.25)),
    "MDA": _w(("DEU", 0.50), ("POL", 0.30), ("TUR", 0.20)),
    "GEO": _w(("TUR", 0.45), ("RUS", 0.25), ("DEU", 0.30)),
    "KHM": _w(("CHN", 0.45), ("VNM", 0.40), ("THA", 0.15)),
    "LAO": _w(("CHN", 0.50), ("VNM", 0.35), ("THA", 0.15)),
    "MMR": _w(("CHN", 0.55), ("THA", 0.25), ("IND", 0.20)),
    "BRN": _w(("SGP", 0.40), ("MYS", 0.35), ("CHN", 0.25)),
    "MNG": _w(("CHN", 0.60), ("RUS", 0.25), ("KOR", 0.15)),
    "NPL": _w(("IND", 0.70), ("CHN", 0.30)),
    "LKA": _w(("IND", 0.55), ("CHN", 0.30), ("SGP", 0.15)),
    "MDV": _w(("IND", 0.60), ("SGP", 0.25), ("CHN", 0.15)),
    "FJI": _w(("AUS", 0.55), ("NZL", 0.30), ("CHN", 0.15)),
    "PNG": _w(("AUS", 0.65), ("CHN", 0.20), ("IDN", 0.15)),
    "MAC": _w(("CHN", 0.80), ("HKG", 0.20)),
    "BTN": _w(("IND", 0.75), ("CHN", 0.25)),
    "TLS": _w(("IDN", 0.50), ("AUS", 0.35), ("CHN", 0.15)),
    "VUT": _w(("AUS", 0.60), ("NZL", 0.25), ("CHN", 0.15)),
    "WSM": _w(("NZL", 0.50), ("AUS", 0.35), ("USA", 0.15)),
    "TON": _w(("NZL", 0.50), ("AUS", 0.35), ("USA", 0.15)),
    "SLB": _w(("AUS", 0.60), ("CHN", 0.25), ("NZL", 0.15)),
    "KIR": _w(("AUS", 0.50), ("USA", 0.30), ("NZL", 0.20)),
    "MHL": _w(("USA", 0.70), ("AUS", 0.30)),
    "PLW": _w(("USA", 0.65), ("JPN", 0.20), ("AUS", 0.15)),
    "FSM": _w(("USA", 0.70), ("AUS", 0.30)),
    "NRU": _w(("AUS", 0.70), ("NZL", 0.30)),
    "TUV": _w(("NZL", 0.55), ("AUS", 0.45)),
    "ASM": _w(("USA", 0.80), ("NZL", 0.20)),
    "GUM": _w(("USA", 0.85), ("JPN", 0.15)),
    "PER": _w(("BRA", 0.45), ("USA", 0.40), ("CHL", 0.15)),
    "URY": _w(("BRA", 0.55), ("ARG", 0.30), ("USA", 0.15)),
    "PRY": _w(("BRA", 0.60), ("ARG", 0.30), ("USA", 0.10)),
    "BOL": _w(("BRA", 0.50), ("ARG", 0.25), ("CHN", 0.25)),
    "ECU": _w(("USA", 0.50), ("BRA", 0.30), ("CHN", 0.20)),
    "VEN": _w(("BRA", 0.35), ("CHN", 0.35), ("USA", 0.30)),
    "CRI": _w(("USA", 0.70), ("MEX", 0.20), ("BRA", 0.10)),
    "PAN": _w(("USA", 0.65), ("CHN", 0.20), ("MEX", 0.15)),
    "GTM": _w(("USA", 0.70), ("MEX", 0.30)),
    "HND": _w(("USA", 0.75), ("MEX", 0.25)),
    "SLV": _w(("USA", 0.75), ("MEX", 0.25)),
    "NIC": _w(("USA", 0.55), ("MEX", 0.25), ("CHN", 0.20)),
    "DOM": _w(("USA", 0.80), ("BRA", 0.20)),
    "JAM": _w(("USA", 0.75), ("GBR", 0.25)),
    "TTO": _w(("USA", 0.60), ("GBR", 0.25), ("BRA", 0.15)),
    "BRB": _w(("USA", 0.55), ("GBR", 0.45)),
    "BHS": _w(("USA", 0.85), ("GBR", 0.15)),
    "BLZ": _w(("USA", 0.70), ("MEX", 0.30)),
    "GUY": _w(("USA", 0.45), ("BRA", 0.35), ("GBR", 0.20)),
    "SUR": _w(("BRA", 0.45), ("NLD", 0.35), ("USA", 0.20)),
    "QAT": _w(("SAU", 0.70), ("ARE", 0.20), ("USA", 0.10)),
    "KWT": _w(("SAU", 0.65), ("USA", 0.25), ("ARE", 0.10)),
    "BHR": _w(("SAU", 0.70), ("USA", 0.20), ("ARE", 0.10)),
    "OMN": _w(("SAU", 0.55), ("ARE", 0.30), ("IND", 0.15)),
    "JOR": _w(("SAU", 0.45), ("USA", 0.35), ("TUR", 0.20)),
    "LBN": _w(("SAU", 0.35), ("FRA", 0.35), ("TUR", 0.30)),
    "IRQ": _w(("SAU", 0.40), ("TUR", 0.30), ("USA", 0.30)),
    "IRN": _w(("CHN", 0.45), ("RUS", 0.30), ("TUR", 0.25)),
    "YEM": _w(("SAU", 0.70), ("ARE", 0.20), ("TUR", 0.10)),
    "MAR": _w(("FRA", 0.45), ("ESP", 0.30), ("SAU", 0.25)),
    "TUN": _w(("FRA", 0.50), ("ITA", 0.30), ("SAU", 0.20)),
    "DZA": _w(("FRA", 0.40), ("ITA", 0.30), ("SAU", 0.30)),
    "LBY": _w(("ITA", 0.45), ("TUR", 0.30), ("EGY", 0.25)),
    "SDN": _w(("EGY", 0.45), ("SAU", 0.30), ("CHN", 0.25)),
    "ETH": _w(("CHN", 0.45), ("USA", 0.30), ("SAU", 0.25)),
    "KEN": _w(("CHN", 0.35), ("GBR", 0.35), ("USA", 0.30)),
    "TZA": _w(("CHN", 0.40), ("GBR", 0.30), ("ZAF", 0.30)),
    "UGA": _w(("CHN", 0.40), ("GBR", 0.30), ("ZAF", 0.30)),
    "GHA": _w(("GBR", 0.40), ("CHN", 0.35), ("NGA", 0.25)),
    "CIV": _w(("FRA", 0.50), ("NGA", 0.25), ("CHN", 0.25)),
    "SEN": _w(("FRA", 0.55), ("NGA", 0.25), ("CHN", 0.20)),
    "CMR": _w(("FRA", 0.45), ("NGA", 0.30), ("CHN", 0.25)),
    "AGO": _w(("CHN", 0.50), ("PRT", 0.25), ("ZAF", 0.25)),
    "MOZ": _w(("ZAF", 0.45), ("CHN", 0.35), ("PRT", 0.20)),
    "ZWE": _w(("ZAF", 0.55), ("CHN", 0.30), ("GBR", 0.15)),
    "BLR": _w(("RUS", 0.70), ("DEU", 0.20), ("POL", 0.10)),
    "AZE": _w(("TUR", 0.45), ("RUS", 0.30), ("CHN", 0.25)),
    "ARM": _w(("RUS", 0.45), ("FRA", 0.30), ("TUR", 0.25)),
    "UZB": _w(("CHN", 0.40), ("RUS", 0.35), ("KAZ", 0.25)),
    "TKM": _w(("CHN", 0.45), ("RUS", 0.30), ("TUR", 0.25)),
    "TJK": _w(("CHN", 0.40), ("RUS", 0.35), ("KAZ", 0.25)),
    "KGZ": _w(("CHN", 0.45), ("RUS", 0.30), ("KAZ", 0.25)),
    "AFG": _w(("PAK", 0.40), ("CHN", 0.35), ("IND", 0.25)),
    "PRK": _w(("CHN", 0.75), ("RUS", 0.25)),
    "CUB": _w(("CHN", 0.40), ("RUS", 0.30), ("MEX", 0.30)),
}

assert set(PROXY_WEIGHTS) == set(PROXY_CODES), "PROXY_WEIGHTS must cover every proxy"

_HUB_INDEX = {c: i for i, c in enumerate(HUB_CODES)}
_PROXY_INDEX = {c: i for i, c in enumerate(PROXY_CODES)}


def hub_index(code: str) -> int:
    return _HUB_INDEX[code]


def proxy_index(code: str) -> int:
    return _PROXY_INDEX[code]
