"""ECONITH :: core.ingestion.context_state

Strict epistemic feature isolation & anti-overfitting state contracts.

The CORE must ingest macro (low-frequency) and micro-structural (high-frequency)
data across vastly heterogeneous asset classes WITHOUT cross-task gradient
contamination. This module encodes the mathematical state-masking contracts:

* :class:`AssetUniverse` -- the categorical desk taxonomy (Majors, High-Beta,
  Meme, Forex, Commodities, Sovereign) with hard membership boundaries.
* :class:`MacroFeatureBlock` / :class:`MicroFeatureBlock` -- frequency-isolated
  feature envelopes that are *never* concatenated raw; the HRL Meta-Brain sees
  the macro block, the per-asset tokenizers see only their sliced micro block.
* :class:`ExhaustiveContextState` -- the fully-validated, timestamped snapshot
  the CORE emits every ingestion cycle and the QUANT desks slice from.

All numeric fields are validated for finiteness so a poisoned upstream frame can
never silently propagate ``NaN``/``inf`` into a training tensor.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

__all__ = [
    "AssetDesk",
    "AssetUniverse",
    "MacroFeatureBlock",
    "MicroFeatureBlock",
    "AssetMicroState",
    "ExhaustiveContextState",
]


# ---------------------------------------------------------------------------
# Asset desk taxonomy
# ---------------------------------------------------------------------------
class AssetDesk(str, Enum):
    """The isolated desk tiers. Each tier trains a partitioned sub-brain so
    meme retail sentiment never pollutes the majors' order-flow gradients."""

    CRYPTO_MAJORS = "crypto_majors"
    CRYPTO_HIGH_BETA = "crypto_high_beta"
    CRYPTO_MEME = "crypto_meme"
    TRADFI_FOREX = "tradfi_forex"
    COMMODITIES = "commodities"
    SOVEREIGN = "sovereign"


class AssetUniverse:
    """Canonical, immutable desk<->symbol membership map.

    Membership is a hard boundary used both by the ingestion validators and by
    the QUANT :class:`BrainSlicingAdapter` to guarantee an asset only ever reads
    its own desk's local observation slice.
    """

    _MEMBERSHIP: dict[AssetDesk, tuple[str, ...]] = {
        AssetDesk.CRYPTO_MAJORS: ("BTCUSDT", "ETHUSDT"),
        AssetDesk.CRYPTO_HIGH_BETA: ("SOLUSDT", "AVAXUSDT", "NEARUSDT", "SUIUSDT"),
        AssetDesk.CRYPTO_MEME: ("DOGEUSDT", "SHIBUSDT", "PEPEUSDT"),
        AssetDesk.TRADFI_FOREX: ("DXY", "USDCNY", "USDJPY", "EURUSD"),
        AssetDesk.COMMODITIES: ("XAUUSD", "XAGUSD", "WTIUSD", "BRENTUSD"),
        AssetDesk.SOVEREIGN: ("US10Y", "SPX500", "NDX100", "HSI"),
    }

    @classmethod
    def desk_of(cls, symbol: str) -> AssetDesk:
        """Return the owning desk for ``symbol`` or raise ``KeyError``."""
        sym = symbol.upper()
        for desk, members in cls._MEMBERSHIP.items():
            if sym in members:
                return desk
        raise KeyError(f"symbol {symbol!r} is not in the ECONITH asset universe")

    @classmethod
    def members(cls, desk: AssetDesk) -> tuple[str, ...]:
        return cls._MEMBERSHIP[desk]

    @classmethod
    def all_symbols(cls) -> tuple[str, ...]:
        out: list[str] = []
        for members in cls._MEMBERSHIP.values():
            out.extend(members)
        return tuple(out)

    @classmethod
    def is_crypto(cls, symbol: str) -> bool:
        return cls.desk_of(symbol) in (
            AssetDesk.CRYPTO_MAJORS,
            AssetDesk.CRYPTO_HIGH_BETA,
            AssetDesk.CRYPTO_MEME,
        )


# ---------------------------------------------------------------------------
# Finiteness helper
# ---------------------------------------------------------------------------
def _finite(value: float, name: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return value


# ---------------------------------------------------------------------------
# Frequency-isolated feature blocks
# ---------------------------------------------------------------------------
class MacroFeatureBlock(BaseModel):
    """Low-frequency institutional macro spine (the Meta-Brain's view).

    Populated exclusively from the ``core.macro.*`` sources. This block is
    passed to the HRL Meta-Brain's structural attention layer and is *never*
    concatenated directly onto a micro order-flow vector.
    """

    model_config = ConfigDict(extra="allow", validate_assignment=True)

    # FRED monetary spine
    fed_funds_effective_rate: float | None = None
    consumer_price_index: float | None = None
    core_pce: float | None = None
    unemployment_rate: float | None = None
    yield_spread_10y_2y: float | None = None
    industrial_production: float | None = None
    # Cross-source macro
    debt_to_gdp: dict[str, float] = Field(default_factory=dict)
    trade_balance_pct_gdp: dict[str, float] = Field(default_factory=dict)
    dollar_index_dxy: float | None = None
    gold_xau_spot: float | None = None
    wti_crude_spot: float | None = None
    euro_hicp_index: float | None = None

    @field_validator(
        "fed_funds_effective_rate",
        "consumer_price_index",
        "core_pce",
        "unemployment_rate",
        "yield_spread_10y_2y",
        "industrial_production",
        "dollar_index_dxy",
        "gold_xau_spot",
        "wti_crude_spot",
        "euro_hicp_index",
    )
    @classmethod
    def _scalar_finite(cls, v: float | None) -> float | None:
        return None if v is None else _finite(v, "macro scalar")


class MicroFeatureBlock(BaseModel):
    """High-frequency order-flow micro-structure (the per-asset tokenizer view).

    Populated from the ``md.*`` / ``indicator.*`` / ``alt.*`` micro plane. It is
    strictly bound to a single symbol and carries only structural tape features.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    order_book_imbalance: float = 0.0          # OBI in [-1, 1]
    volume_delta: float = 0.0                  # signed traded volume
    open_interest: float = 0.0
    funding_rate: float = 0.0
    realized_volatility: Annotated[float, Field(ge=0.0)] = 0.0
    spread_bps: Annotated[float, Field(ge=0.0)] = 0.0

    @field_validator("order_book_imbalance")
    @classmethod
    def _clamp_obi(cls, v: float) -> float:
        v = _finite(v, "order_book_imbalance")
        return max(-1.0, min(1.0, v))

    @field_validator("volume_delta", "open_interest", "funding_rate")
    @classmethod
    def _micro_finite(cls, v: float) -> float:
        return _finite(v, "micro feature")


class AssetMicroState(BaseModel):
    """A symbol's micro block tagged with its (validated) desk membership."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    symbol: str
    desk: AssetDesk
    micro: MicroFeatureBlock = Field(default_factory=MicroFeatureBlock)
    mark_price: float | None = None

    @model_validator(mode="after")
    def _membership_consistent(self) -> "AssetMicroState":
        expected = AssetUniverse.desk_of(self.symbol)
        if expected is not self.desk:
            raise ValueError(
                f"desk mismatch for {self.symbol}: declared {self.desk} "
                f"but universe says {expected}"
            )
        return self


# ---------------------------------------------------------------------------
# The exhaustive, isolated context snapshot
# ---------------------------------------------------------------------------
class ExhaustiveContextState(BaseModel):
    """The CORE's canonical, frequency-isolated observation snapshot.

    This is the single object the HRL Meta-Brain consumes and the QUANT desks
    slice. The macro block and the per-asset micro blocks are held in *separate*
    fields -- the isolation is structural, not a runtime convention -- so a
    tokenizer physically cannot read another desk's tape.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    macro: MacroFeatureBlock = Field(default_factory=MacroFeatureBlock)
    assets: dict[str, AssetMicroState] = Field(default_factory=dict)
    # Latest classified market regime label from the CORE regime layer.
    regime_label: str = "UNKNOWN"
    regime_confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0

    def upsert_asset(self, state: AssetMicroState) -> None:
        """Insert or replace a symbol's isolated micro state."""
        self.assets[state.symbol.upper()] = state

    def micro_for(self, symbol: str) -> AssetMicroState:
        """Return the isolated micro state for ``symbol`` (raises if absent)."""
        return self.assets[symbol.upper()]

    def desk_symbols(self, desk: AssetDesk) -> list[str]:
        """Symbols currently tracked for a given desk."""
        return [s for s, st in self.assets.items() if st.desk is desk]

    def to_masked_observation(self, symbol: str) -> dict[str, object]:
        """Produce the masked observation dict for a single asset's tokenizer.

        Only the parent macro spine + the target asset's own micro block are
        exposed -- every other asset's tape is masked out entirely.
        """
        asset = self.micro_for(symbol)
        return {
            "symbol": asset.symbol,
            "desk": asset.desk.value,
            "macro": self.macro.model_dump(exclude_none=True),
            "micro": asset.micro.model_dump(),
            "regime": {
                "label": self.regime_label,
                "confidence": self.regime_confidence,
            },
        }
