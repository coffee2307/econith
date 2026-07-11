"""ECONITH :: ai.simulator_engine.narrative

Cybernetic Narrative & Global Event-Log Generator.

Every agent decision emits a typed :class:`CausalFact`. The
:class:`NarrativeEngine` renders short, locale-pure news lines (VI or EN)
from structured tags and metrics — no mixed-language string patching.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

__all__ = ["CausalFact", "NarrativeEngine"]


@dataclass(slots=True)
class CausalFact:
    """A structured cause->effect record produced by an agent or the kernel."""

    actor: str                     # "Corporate AI" | "Government AI" | "Societal AI" | "Market"
    country: str                   # ISO-3166 alpha-3 (e.g. USA, CHN)
    action: str                    # internal English trace (not shown verbatim)
    cause: str                     # internal English trace
    effect: str                    # internal English trace
    level: str = "info"            # info | ok | warn | danger
    metrics: dict[str, float] = field(default_factory=dict)
    tags: tuple[str, ...] = ()     # e.g. ("capital_flight", "regime:VOLATILE")


# Display names for nations referenced in agent narratives.
_COUNTRY_EN: dict[str, str] = {
    "USA": "the United States",
    "CHN": "China",
    "VNM": "Vietnam",
    "JPN": "Japan",
    "DEU": "Germany",
    "GBR": "the United Kingdom",
    "IND": "India",
    "FRA": "France",
    "BRA": "Brazil",
    "KOR": "South Korea",
    "AUS": "Australia",
    "CAN": "Canada",
    "MEX": "Mexico",
    "IDN": "Indonesia",
    "SAU": "Saudi Arabia",
    "PAK": "Pakistan",
}
_COUNTRY_VI: dict[str, str] = {
    "USA": "Hoa Kỳ",
    "CHN": "Trung Quốc",
    "VNM": "Việt Nam",
    "JPN": "Nhật Bản",
    "DEU": "Đức",
    "GBR": "Anh",
    "IND": "Ấn Độ",
    "FRA": "Pháp",
    "BRA": "Brazil",
    "KOR": "Hàn Quốc",
    "AUS": "Úc",
    "CAN": "Canada",
    "MEX": "Mexico",
    "IDN": "Indonesia",
    "SAU": "Ả Rập Xê Út",
    "PAK": "Pakistan",
}


class NarrativeEngine:
    """Synthesises :class:`CausalFact`s into concise, locale-pure news lines."""

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)

    def compose(self, fact: CausalFact, *, locale: str = "en") -> str:
        """Render a single causal fact into a short news line."""
        if locale.lower().startswith("vi"):
            return self._compose_vi(fact)
        return self._compose_en(fact)

    def regime_transition(
        self, country_or_market: str, old: str, new: str, driver: str,
        confidence: float,
    ) -> CausalFact:
        """Build the canonical 'regime flipped, here's why' fact."""
        return CausalFact(
            actor="Market",
            country=country_or_market,
            action=f"AI market regime shifted {old} -> {new}",
            cause=driver,
            effect=(
                f"the HMM/GMM classifier re-weighted capital allocation at "
                f"{confidence*100:.0f}% conviction"
            ),
            level="danger" if new == "VOLATILE" else "warn" if new == "TRENDING" else "info",
            metrics={"confidence": round(confidence, 3)},
            tags=(f"regime:{new}", "regime_shift"),
        )

    # ------------------------------------------------------------------
    # English
    # ------------------------------------------------------------------
    def _compose_en(self, fact: CausalFact) -> str:
        place = self._place_en(fact.country)
        tag = self._primary_tag(fact)
        m = fact.metrics

        if fact.actor == "Corporate AI" or tag == "capital_flight":
            usd = m.get("capital_flight_usd", 0.0)
            bps = m.get("yield_shock_bps", 0.0)
            money = self._money_en(usd)
            return (
                f"{place}: firms repatriated {money} amid market stress — "
                f"10-year yields +{bps:.0f} bps, supply chains shifting abroad."
            )

        if fact.actor == "Government AI" or tag == "capital_controls":
            bps = m.get("rate_hike_bps", 0.0)
            defend = m.get("defense_intensity", 0.0)
            if bps > 0:
                return (
                    f"{place}: government tightened capital controls and raised "
                    f"rates +{bps:.0f} bps to slow capital outflows."
                )
            return (
                f"{place}: government imposed capital controls "
                f"(defence intensity {defend:.0%}) to stabilise markets."
            )

        if fact.actor == "Societal AI" or tag == "civil_unrest":
            unrest = m.get("unrest_index", 0.0)
            return (
                f"{place}: social unrest is rising (index {unrest:.0%}) — "
                f"strikes and protests weigh on stability."
            )

        if tag == "regime_shift" or any(t.startswith("regime:") for t in fact.tags):
            regime = next((t.split(":", 1)[1] for t in fact.tags if t.startswith("regime:")), "shift")
            conf = m.get("confidence", 0.0)
            return (
                f"Market regime shifted to {regime} ({conf:.0%} confidence) — "
                f"allocators repricing risk across {place}."
            )

        if tag == "quant_to_macro":
            usd = m.get("capital_flight_usd", 0.0)
            bps = m.get("yield_shock_bps", 0.0)
            return (
                f"{place}: risk-off selling hit the real economy — "
                f"{self._money_en(usd)} left, yields +{bps:.0f} bps."
            )

        return self._generic_en(fact)

    def _generic_en(self, fact: CausalFact) -> str:
        place = self._place_en(fact.country)
        actor = fact.actor
        return f"{place}: {actor} adjusted policy in response to macro pressure."

    # ------------------------------------------------------------------
    # Vietnamese
    # ------------------------------------------------------------------
    def _compose_vi(self, fact: CausalFact) -> str:
        place = self._place_vi(fact.country)
        tag = self._primary_tag(fact)
        m = fact.metrics

        if fact.actor == "Corporate AI" or tag == "capital_flight":
            usd = m.get("capital_flight_usd", 0.0)
            bps = m.get("yield_shock_bps", 0.0)
            money = self._money_vi(usd)
            return (
                f"{place}: doanh nghiệp rút {money} khi thị trường căng thẳng — "
                f"lợi suất 10 năm tăng {bps:.0f} điểm cơ bản, chuỗi cung dịch chuyển."
            )

        if fact.actor == "Government AI" or tag == "capital_controls":
            bps = m.get("rate_hike_bps", 0.0)
            if bps > 0:
                return (
                    f"{place}: chính phủ siết kiểm soát vốn và tăng lãi suất "
                    f"{bps:.0f} điểm cơ bản để hãm dòng vốn chảy ra."
                )
            return f"{place}: chính phủ thắt chặt kiểm soát vốn để ổn định thị trường."

        if fact.actor == "Societal AI" or tag == "civil_unrest":
            unrest = m.get("unrest_index", 0.0)
            return (
                f"{place}: bất ổn xã hội leo thang (chỉ số {unrest:.0%}) — "
                f"đình công và biểu tình gây áp lực lên chính sách."
            )

        if tag == "regime_shift" or any(t.startswith("regime:") for t in fact.tags):
            regime = next((t.split(":", 1)[1] for t in fact.tags if t.startswith("regime:")), "biến động")
            conf = m.get("confidence", 0.0)
            regime_vi = {"VOLATILE": "biến động", "CALM": "ổn định", "TRENDING": "xu hướng",
                         "MEAN_REVERTING": "hồi quy"}.get(regime, regime)
            return (
                f"Chế độ thị trường chuyển sang {regime_vi} (tin cậy {conf:.0%}) — "
                f"nhà đầu tư tái định giá rủi ro tại {place}."
            )

        if tag == "quant_to_macro":
            usd = m.get("capital_flight_usd", 0.0)
            bps = m.get("yield_shock_bps", 0.0)
            return (
                f"{place}: sóng bán tháo lan sang nền kinh tế thực — "
                f"rút {self._money_vi(usd)}, lợi suất +{bps:.0f} điểm cơ bản."
            )

        return self._generic_vi(fact)

    def _generic_vi(self, fact: CausalFact) -> str:
        place = self._place_vi(fact.country)
        actor_vi = {
            "Corporate AI": "AI doanh nghiệp",
            "Government AI": "AI chính phủ",
            "Societal AI": "AI xã hội",
            "Market": "Thị trường",
        }.get(fact.actor, fact.actor)
        return f"{place}: {actor_vi} điều chỉnh chính sách trước áp lực vĩ mô."

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _primary_tag(fact: CausalFact) -> str:
        for tag in fact.tags:
            if tag.startswith("regime:"):
                continue
            return tag
        for tag in fact.tags:
            if tag.startswith("regime:"):
                return "regime_shift"
        return ""

    @staticmethod
    def _place_en(code: str) -> str:
        c = (code or "").upper()
        return _COUNTRY_EN.get(c, c or "the market")

    @staticmethod
    def _place_vi(code: str) -> str:
        c = (code or "").upper()
        return _COUNTRY_VI.get(c, c or "thị trường")

    @staticmethod
    def _money_en(usd: float) -> str:
        if abs(usd) >= 1e9:
            return f"${usd / 1e9:.1f}B"
        if abs(usd) >= 1e6:
            return f"${usd / 1e6:.0f}M"
        if abs(usd) >= 1e3:
            return f"${usd / 1e3:.0f}K"
        return f"${usd:,.0f}"

    @staticmethod
    def _money_vi(usd: float) -> str:
        if abs(usd) >= 1e9:
            return f"{usd / 1e9:.1f} tỷ USD"
        if abs(usd) >= 1e6:
            return f"{usd / 1e6:.0f} triệu USD"
        if abs(usd) >= 1e3:
            return f"{usd / 1e3:.0f} nghìn USD"
        return f"{usd:,.0f} USD"
