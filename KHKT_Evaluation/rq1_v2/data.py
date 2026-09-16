"""Đồng bộ theo thời điểm thông tin thực sự được công bố."""
from __future__ import annotations

import numpy as np
import pandas as pd


def prepare(market, releases, cfg):
    market = market.copy()
    required = {"ts_ms", "price"}
    if not required.issubset(market):
        raise ValueError("Market cần ts_ms và price.")
    market["time"] = pd.to_datetime(market.ts_ms, unit="ms", utc=True)
    if market.time.duplicated().any() or not market.time.is_monotonic_increasing:
        raise ValueError("Market phải tăng dần, không trùng thời điểm.")
    if not np.isfinite(market.price).all() or (market.price <= 0).any():
        raise ValueError("Giá thiếu, không hữu hạn hoặc không dương.")
    freq = pd.Timedelta(cfg["frequency"])
    horizon = pd.Timedelta(cfg["horizon"])
    history = pd.Timedelta(cfg["history"])
    if freq <= pd.Timedelta(0) or horizon % freq or history % freq:
        raise ValueError("History và horizon phải là bội số dương của frequency.")
    h, w = int(horizon / freq), int(history / freq)
    if min(h, w) < 2:
        raise ValueError("Cần ít nhất hai lợi suất trong mỗi cửa sổ.")
    price = market.set_index("time").price.resample(freq, closed="right", label="right").last()
    # Không lấp giá qua khoảng trống: nhãn phải có đủ quan sát thực.
    ret = np.log(price).diff()
    panel = pd.DataFrame({"price": price, "b0": ret.rolling(w).std(ddof=0)})
    panel["target"] = ret.rolling(h).std(ddof=0).shift(-h)
    panel["label_end"] = panel.index + horizon
    needed = {"available_at", "observation_at", "feature", "value", "unit", "source"}
    if not needed.issubset(releases):
        raise ValueError(f"Release CSV thiếu: {sorted(needed - set(releases.columns))}")
    releases = releases.copy()
    releases["value"] = pd.to_numeric(releases["value"], errors="raise")
    for col in ("available_at", "observation_at"):
        releases[col] = pd.to_datetime(releases[col], utc=True, errors="raise")
        if releases[col].isna().any():
            raise ValueError(f"Thiếu {col}.")
    if (releases.observation_at > releases.available_at).any():
        raise ValueError("Ngày quan sát sau ngày công bố; kiểm tra lại nguồn.")
    if releases.source.isna().any() or releases.source.astype(str).str.strip().eq("").any():
        raise ValueError("Mỗi giá trị cần có nguồn.")
    allowed = cfg["macro_features"]
    unknown = set(releases.feature) - set(allowed)
    if unknown:
        raise ValueError(f"Biến chưa khai báo: {sorted(unknown)}")
    left = pd.DataFrame({"time": panel.index})
    for name, expected_unit in allowed.items():
        rows = releases.loc[releases.feature == name].sort_values("available_at")
        if rows.empty or not rows.unit.eq(expected_unit).all():
            raise ValueError(f"Thiếu dữ liệu hoặc sai đơn vị của {name}: cần {expected_unit}.")
        if rows.available_at.duplicated().any() or not np.isfinite(rows.value).all():
            raise ValueError(f"Trùng ngày công bố hoặc giá trị lỗi: {name}.")
        # Giữ lần công bố đầu tiên của mỗi kỳ. Một bản sửa đổi về sau không được
        # thay thế dữ liệu mà mô hình đã thực sự nhìn thấy tại thời điểm lịch sử.
        rows = rows.drop_duplicates("observation_at", keep="first")
        # Bỏ bản sửa muộn của kỳ cũ nếu một kỳ mới hơn đã được công bố trước đó.
        rows = rows.loc[rows.observation_at >= rows.observation_at.cummax()]
        merged = pd.merge_asof(left, rows, left_on="time", right_on="available_at", direction="backward")
        panel[name] = merged.value.to_numpy()
        panel[name + "__release"] = merged.available_at.to_numpy()
    return panel, h
