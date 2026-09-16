"""Kiểm tra nguồn và dựng bảng tại đúng thời điểm dự báo."""
import numpy as np
import pandas as pd

FEATURES = ('interest_rate', 'inflation', 'unemployment', 'gdp_growth')


def utc(values):
    # Không suy đoán múi giờ cho chuỗi ngày thiếu offset.
    if any(pd.Timestamp(v).tzinfo is None for v in values):
        raise ValueError('Mọi timestamp cần múi giờ rõ ràng.')
    return pd.to_datetime(values, utc=True, errors='raise')


def releases(frame, countries):
    required = {'country', 'feature', 'observation_at', 'available_at', 'value', 'unit', 'source', 'quality'}
    if not required <= set(frame):
        raise ValueError(f'Release thiếu cột {sorted(required-set(frame))}')
    r = frame.copy()
    for key in ('observation_at', 'available_at'):
        r[key] = utc(r[key])
    if r[list(required)].isna().any().any():
        raise ValueError('Release không được thiếu trường bắt buộc.')
    if not r.unit.eq('fraction').all() or not r.quality.eq('observed').all():
        raise ValueError('Đường chạy nghiên cứu chỉ nhận fraction và observed.')
    if r.source.astype(str).str.strip().eq('').any():
        raise ValueError('Thiếu nguồn dữ liệu.')
    if not set(r.country) <= set(countries) or not set(r.feature) <= set(FEATURES):
        raise ValueError('Quốc gia hoặc biến chưa khai báo.')
    if (r.observation_at > r.available_at).any() or not np.isfinite(r.value).all():
        raise ValueError('Ngày công bố hoặc giá trị không hợp lệ.')
    if r.duplicated(['country', 'feature', 'available_at']).any():
        raise ValueError('Công bố trùng thời điểm; hợp nhất tại nguồn trước khi chạy.')
    # Lưu lần đầu mỗi kỳ; bản sửa đổi không được quay ngược vào lịch sử.
    r = r.sort_values('available_at').drop_duplicates(['country', 'feature', 'observation_at'])
    return r.reset_index(drop=True)


def market(frame, asset, horizon):
    r = frame.loc[frame.asset == asset].copy()
    if r.empty:
        raise ValueError(f'Thiếu tài sản {asset}')
    r['time'] = utc(r.time)
    if not r.time.is_monotonic_increasing or r.time.duplicated().any():
        raise ValueError('Market phải tăng dần, không trùng phiên.')
    if not np.isfinite(r.adjusted_close).all() or (r.adjusted_close <= 0).any():
        raise ValueError('Giá điều chỉnh không hợp lệ.')
    # Mỗi dòng phải là một phiên đã kiểm tra theo lịch sàn, không resample ngày lịch.
    r = r.set_index('time')
    returns = np.log(r.adjusted_close).diff()
    squared = returns ** 2
    for window in (1, 5, 22):
        r[f'log_var_{window}'] = np.log(squared.rolling(window).mean().clip(lower=1e-12))
    r['target'] = np.sqrt(squared.rolling(horizon).mean().shift(-horizon))
    r['label_end'] = pd.Series(r.index, index=r.index).shift(-horizon)
    return r


def masks(panel, fold, usable):
    start, validation, test, end = [pd.Timestamp(fold[k]) for k in ('train_start', 'validation_start', 'test_start', 'test_end')]
    if any(x.tzinfo is None for x in (start, validation, test, end)) or not start < validation < test < end:
        raise ValueError('Ranh giới fold không hợp lệ.')
    result = tuple(np.asarray((panel.index >= a) & (panel.index < b) & (panel.label_end < b)) & usable
                   for a, b in ((start, validation), (validation, test), (test, end)))
    if min(map(np.sum, result)) < 32:
        raise ValueError('Mỗi phần cần ít nhất 32 nhãn sau purge.')
    return result


def audit_sessions(prices, sessions, assets):
    """Đối chiếu lịch phiên độc lập; không suy ra lịch từ chính bảng giá."""
    for asset in assets:
        actual = utc(prices.loc[prices.asset == asset, 'time'])
        expected = utc(sessions.loc[sessions.asset == asset, 'time'])
        if len(expected) == 0 or expected.duplicated().any():
            raise ValueError(f'Lịch phiên thiếu hoặc trùng: {asset}')
        if len(actual) != len(expected) or set(actual) != set(expected):
            raise ValueError(f'Giá và lịch phiên không khớp: {asset}; không tự điền giá.')
