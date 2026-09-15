"""Phát lại trạng thái Hoa Kỳ bằng các mô hình phản ứng có sẵn."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ai.simulator_engine.macro_vectors import WorldState, default_world
from ai.simulator_engine.cross_impact import macro_to_micro
from ai.simulator_engine.reaction_models import CentralBankModel, SentimentModel


FIELDS = {
    "interest_rate": ("monetary", "interest_rate"),
    "inflation": ("monetary", "inflation_cpi"),
    "unemployment": ("labor", "unemployment"),
    "gdp_growth": (None, "gdp_growth"),
}
BOUNDS = {
    "interest_rate": (0, .25), "inflation_cpi": (-.03, .15),
    "unemployment": (0, .45), "gdp_growth": (-.5, .5),
    "fx_spot": (.01, 1e6), "consumer_confidence": (0, 1),
    "business_confidence": (0, 1), "political_stability": (0, 1),
    "social_unrest_index": (0, 1),
}


def replay(panel, cfg, *, stateful=True):
    if set(cfg["macro_features"]) != set(FIELDS):
        raise ValueError("Replay USA v2 yêu cầu đúng bốn biến trong cấu hình mẫu.")
    country = default_world().countries["USA"].model_copy(deep=True)
    world = WorldState(countries={"USA": country})
    models = (CentralBankModel(), SentimentModel())
    last_release = {}
    previous = None
    initialized = False
    output = []
    for time, row in panel.iterrows():
        if row[list(FIELDS)].isna().any():
            output.append([np.nan] * 5)
            continue
        if not stateful:
            country = default_world().countries["USA"].model_copy(deep=True)
            world = WorldState(countries={"USA": country})
            last_release = {}
        if initialized and stateful:
            days = (time - previous).total_seconds() / 86400
            # Cùng nhịp ngày với mô hình gốc; chia nhỏ khoảng dài, không dùng tốc độ giao diện.
            while days > 1e-10:
                step = min(1., days)
                proposals = [a for model in models for a in model.react("USA", world)]
                deltas = {}
                for a in proposals:
                    key = (a.group, a.field)
                    deltas[key] = deltas.get(key, 0.) + a.delta * step * cfg.get("reaction_daily_scale", 1.)
                for (group, field), delta in deltas.items():
                    obj = getattr(country, group)
                    lo, hi = BOUNDS[field]
                    setattr(obj, field, float(np.clip(getattr(obj, field) + delta, lo, hi)))
                days -= step
        # Chỉ ghi đè khi có bản công bố mới; không xóa diễn biến giữa hai lần công bố.
        for name, (group, field) in FIELDS.items():
            stamp = row[name + "__release"]
            if last_release.get(name) != stamp:
                obj = country if group is None else getattr(country, group)
                value = float(row[name])
                lo, hi = BOUNDS[field]
                if not lo <= value <= hi:
                    raise ValueError(f"{name} ngoài miền hợp lệ: {value}; kiểm tra đơn vị fraction.")
                setattr(obj, field, value)
                last_release[name] = stamp
        country.monetary.real_interest_rate = country.monetary.interest_rate - country.monetary.inflation_cpi
        impact = macro_to_micro(world)
        output.append([impact.volatility_multiplier, impact.order_flow_shock,
                       impact.liquidity_drain, country.monetary.inflation_cpi,
                       country.geopolitical.business_confidence])
        previous, initialized = time, True
    features = pd.DataFrame(output, index=panel.index,
                            columns=["world_vol", "world_flow", "world_liquidity", "world_inflation", "world_confidence"])
    # Năm mức và năm thay đổi: tránh thêm spread/regime vốn là tổ hợp của các biến trên.
    change = features.diff().add_suffix("_change")
    return pd.concat([features, change], axis=1)
