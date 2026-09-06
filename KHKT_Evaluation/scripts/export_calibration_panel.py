# -*- coding: utf-8 -*-
"""Export macro calibration panel from BTC features (aligned with EXP_002)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from KHKT_Evaluation.common import data_loader, macro_series, paths


def main() -> int:
    out_dir = paths.ROOT / "KHKT_Evaluation" / "dataset_info"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "macro_calibration_panel.parquet"
    meta_path = out_dir / "macro_calibration_panel_meta.json"

    src = data_loader.resolve_btc_features()
    df = data_loader.load_btc_panel(max_rows=50_000, stride=3)
    panel = macro_series.calibrator_panel_from_df(df)
    if panel.empty:
        print("ERROR: empty calibration panel — check macro columns")
        return 1
    panel.to_parquet(out_path, index=False)
    meta = {
        "source_features": str(src),
        "max_rows": 50_000,
        "stride": 3,
        **macro_series.panel_provenance(panel, source=str(src)),
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"wrote {out_path} ({len(panel)} rows, cols={list(panel.columns)})")
    print(f"means={meta['means']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
