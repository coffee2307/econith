"""Bounded World calibration smoke; keeps the expensive grid in research CI.

Tests the real calibrate() file/output/metadata contract on deterministic
synthetic data, but deliberately bypasses the 24-path x 2520-step skew search.
See .github/workflows/world-calibration.yml for the full unmodified check.
"""
from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path
from unittest.mock import patch

from scripts import calibrate_world as world


def main() -> int:
    original_generator = world._synthetic_panel
    with tempfile.TemporaryDirectory(prefix="econith-world-smoke-") as work:
        output = Path(work) / "coefficients.json"
        missing_input = Path(work) / "missing_market_panel.parquet"
        with (
            patch.object(world, "_synthetic_panel",
                         side_effect=lambda n=2520, seed=7:
                         original_generator(n=128, seed=seed)),
            patch.object(world, "_refine_jumps_for_skew",
                         side_effect=lambda feature, level, coeffs: coeffs),
        ):
            result = world.calibrate(str(missing_input), str(output))
        payload = json.loads(output.read_text(encoding="utf-8"))
        assert result and len(result) >= 3
        assert payload["features"] == result
        assert payload["meta"]["dt_policy"] == "1/252"
        for vals in result.values():
            for key in ("theta", "mu", "sigma", "jump_intensity",
                        "jump_mean", "jump_std"):
                assert math.isfinite(float(vals[key]))
            assert vals["theta"] > 0 and vals["sigma"] > 0
    print(f"WORLD_CALIBRATION_SMOKE_PASS: {len(result)} features")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
