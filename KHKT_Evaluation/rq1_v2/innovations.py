"""Thông tin mới tại ngày công bố; không dùng kỳ vọng khảo sát thị trường."""
import numpy as np
import pandas as pd

from ai.simulator_engine.cross_impact import macro_to_micro
from ai.simulator_engine.macro_vectors import WorldState, default_world
from ai.simulator_engine.reaction_models import CentralBankModel, SentimentModel
from KHKT_Evaluation.rq1_v2.world import BOUNDS, FIELDS


def release_features(panel, names):
    output = {}
    for name in names:
        stamp = panel[name + "__release"]
        fresh = stamp.notna() & stamp.ne(stamp.shift())
        releases = panel.loc[fresh, name]
        changes = releases.diff()
        # Chuẩn hóa bằng các thay đổi đã thấy, không dùng chính lần công bố mới.
        scale = changes.expanding(min_periods=4).std().shift(1)
        innovation = (changes / scale.clip(lower=1e-6)).clip(-5, 5).fillna(0)
        pulse = innovation.reindex(panel.index, fill_value=0)
        output[name + "_innovation"] = pulse
        output[name + "_change"] = changes.fillna(0).reindex(panel.index, fill_value=0)
        for half_life in (3, 7, 14):
            state, previous, values = 0., None, []
            for time, value in pulse.items():
                if previous is not None:
                    state *= 2 ** (-((time - previous).total_seconds() / 86400) / half_life)
                state += value
                values.append(state)
                previous = time
            output[f"{name}_decay_{half_life}"] = values
    return pd.DataFrame(output, index=panel.index)


def enrich(panel, features, cfg):
    from KHKT_Evaluation.rq1_v2.world import replay

    # Cả hai nhánh có cùng trạng thái ban đầu. Nhánh đối chứng không nhận công bố mới.
    frozen = panel.copy()
    for name in cfg["macro_features"]:
        for column in (name, name + "__release"):
            first = frozen[column].first_valid_index()
            if first is not None:
                frozen.loc[first:, column] = frozen.loc[first, column]
    reference = replay(frozen, cfg)
    delta = (features - reference).add_prefix("counterfactual_")
    return pd.concat([features, delta], axis=1)


def _set_macro(country, values):
    for name, value in values.items():
        group, field = FIELDS[name]
        target = country if group is None else getattr(country, group)
        setattr(target, field, float(value))
    country.monetary.real_interest_rate = (
        country.monetary.interest_rate - country.monetary.inflation_cpi
    )


def _advance(country, days, daily_scale):
    world = WorldState(countries={"USA": country})
    models = (CentralBankModel(), SentimentModel())
    for _ in range(days):
        proposals = [item for model in models for item in model.react("USA", world)]
        deltas = {}
        for item in proposals:
            key = (item.group, item.field)
            deltas[key] = deltas.get(key, 0.0) + item.delta * daily_scale
        for (group, field), delta in deltas.items():
            target = getattr(country, group)
            lo, hi = BOUNDS[field]
            setattr(target, field, float(np.clip(getattr(target, field) + delta, lo, hi)))
        country.monetary.real_interest_rate = (
            country.monetary.interest_rate - country.monetary.inflation_cpi
        )
    impact = macro_to_micro(world)
    return np.array([
        impact.volatility_multiplier,
        impact.order_flow_shock,
        impact.liquidity_drain,
        country.monetary.inflation_cpi,
        country.geopolitical.business_confidence,
    ])


def causal_world_features(panel, cfg):
    """Tác động riêng của từng lần công bố so với việc giữ giá trị trước đó."""
    names = list(cfg["macro_features"])
    previous_values = {}
    previous_stamps = {}
    pulses = []
    horizon = int(cfg.get("impulse_simulation_days", 14))
    daily_scale = float(cfg.get("reaction_daily_scale", 1 / 30))

    for _, row in panel.iterrows():
        current = {name: float(row[name]) for name in names}
        changed = []
        for name in names:
            stamp = row[name + "__release"]
            if pd.notna(stamp) and previous_stamps.get(name) != stamp:
                if name in previous_values:
                    changed.append(name)

        pulse = np.zeros(5)
        if changed:
            actual = default_world().countries["USA"].model_copy(deep=True)
            control = default_world().countries["USA"].model_copy(deep=True)
            _set_macro(actual, current)
            counterfactual = current.copy()
            for name in changed:
                counterfactual[name] = previous_values[name]
            _set_macro(control, counterfactual)
            pulse = _advance(actual, horizon, daily_scale) - _advance(control, horizon, daily_scale)
        pulses.append(pulse)

        for name in names:
            stamp = row[name + "__release"]
            if pd.notna(stamp) and previous_stamps.get(name) != stamp:
                previous_values[name] = current[name]
                previous_stamps[name] = stamp

    columns = ["vol", "flow", "liquidity", "inflation", "confidence"]
    pulse_frame = pd.DataFrame(pulses, index=panel.index, columns=columns)
    half_lives = cfg.get("impulse_half_lives_days")
    if half_lives is None:
        half_lives = [cfg.get("impulse_half_life_days", 14)]
    half_lives = tuple(float(value) for value in half_lives)
    if not half_lives or any(not np.isfinite(value) or value <= 0 for value in half_lives):
        raise ValueError("impulse_half_lives_days phải gồm các số dương.")
    max_age = float(cfg.get("impulse_max_age_days", 42))
    if not np.isfinite(max_age) or max_age <= 0:
        raise ValueError("impulse_max_age_days phải dương.")

    # Tín hiệu có dấu mô tả hướng tác động. Độ lớn giữ riêng vì cả cú sốc tăng
    # lẫn giảm đều có thể làm biến động thị trường tăng. Cắt sau max_age để ngày
    # không còn sự kiện quay đúng về 0 thay vì mang một dư lượng vô hạn.
    event_rows = np.flatnonzero(np.any(np.abs(pulse_frame.to_numpy()) > 1e-15, axis=1))
    features = {}
    for half_life in half_lives:
        signed = np.zeros_like(pulse_frame.to_numpy())
        magnitude = np.zeros_like(signed)
        for position in event_rows:
            ages = np.asarray(
                (pulse_frame.index[position:] - pulse_frame.index[position]) / pd.Timedelta(days=1),
                dtype=float,
            )
            keep = ages <= max_age
            if not np.any(keep):
                continue
            weight = 2 ** (-ages[keep] / half_life)
            value = pulse_frame.iloc[position].to_numpy(dtype=float)
            stop = position + int(keep.sum())
            signed[position:stop] += weight[:, None] * value
            magnitude[position:stop] += weight[:, None] * np.abs(value)
        tag = str(int(half_life)) if half_life.is_integer() else str(half_life).replace(".", "p")
        for index, name in enumerate(columns):
            features[f"world_{name}_signed_hl{tag}"] = signed[:, index]
            features[f"world_{name}_magnitude_hl{tag}"] = magnitude[:, index]
    return pd.DataFrame(features, index=panel.index)
