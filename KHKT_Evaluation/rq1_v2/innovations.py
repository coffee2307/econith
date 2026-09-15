"""Thông tin mới tại ngày công bố; không dùng kỳ vọng khảo sát thị trường."""
import numpy as np
import pandas as pd


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
