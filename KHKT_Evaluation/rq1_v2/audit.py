"""Kiểm tra tệp local trước khi dựng lịch công bố; không suy đoán dữ liệu thiếu."""
import argparse
import json

import numpy as np
import pandas as pd


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("market")
    args = parser.parse_args()
    frame = pd.read_parquet(args.market)
    result = {"rows": len(frame), "columns": list(frame.columns)}
    if "ts_ms" in frame:
        ts = pd.to_datetime(frame.ts_ms, unit="ms", utc=True)
        deltas = ts.diff().dt.total_seconds().dropna()
        result.update(start=str(ts.min()), end=str(ts.max()), duplicates=int(ts.duplicated().sum()),
                      sorted=bool(ts.is_monotonic_increasing),
                      interval_seconds_quantiles=deltas.quantile([0, .5, .95, 1]).to_dict())
    result["macro"] = {}
    for name in frame:
        if name.startswith("macro_"):
            values = pd.to_numeric(frame[name], errors="coerce")
            finite = values[np.isfinite(values)]
            result["macro"][name] = {"valid": len(finite), "unique": int(finite.nunique()),
                                     "min": float(finite.min()) if len(finite) else None,
                                     "max": float(finite.max()) if len(finite) else None}
    result["warning"] = "Ngày quan sát hoặc join_asof không chứng minh thời điểm công bố. Cần nguồn release riêng."
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
