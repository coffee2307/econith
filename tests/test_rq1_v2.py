"""Dữ liệu giả chỉ kiểm thử phần mềm, không dùng làm bằng chứng H1."""
import copy
import json
from pathlib import Path
import unittest
import subprocess
import sys
import tempfile
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

import numpy as np
import pandas as pd

from KHKT_Evaluation.rq1_v2.data import prepare
from KHKT_Evaluation.rq1_v2.fetch_fred_releases import FredRequestError, normalize, request_series
from KHKT_Evaluation.rq1_v2.model import permute_blocks, select_predict
from KHKT_Evaluation.rq1_v2.run import evaluate, fold_masks
from KHKT_Evaluation.rq1_v2.world import replay


def fixture():
    cfg = json.loads((Path(__file__).resolve().parents[1] / "KHKT_Evaluation/rq1_v2/config.example.json").read_text())
    cfg.update(release_provenance="Synthetic software fixture only", bootstrap_repeats=200)
    dates = pd.date_range("2020-01-01", "2025-01-01", freq="1D", tz="UTC")
    rng = np.random.default_rng(71)
    market = pd.DataFrame({"ts_ms": dates.as_unit("ms").asi8,
                           "price": 100 * np.exp(np.cumsum(rng.normal(0, .02, len(dates))))})
    rows = []
    for i, t in enumerate(dates[::30]):
        for name, base in {"interest_rate": .03, "inflation": .025, "unemployment": .05, "gdp_growth": .02}.items():
            rows.append({"available_at": t.isoformat(), "observation_at": t.isoformat(),
                         "feature": name, "value": base + .005 * np.sin(i), "unit": "fraction", "source": "synthetic-test"})
    return market, pd.DataFrame(rows), cfg


class TestRQ1V2(unittest.TestCase):
    def test_no_release_before_publication(self):
        m, r, c = fixture()
        r["available_at"] = pd.to_datetime(r.available_at) + pd.Timedelta("2d")
        p, _ = prepare(m, r, c)
        self.assertTrue(p.iloc[:2].interest_rate.isna().all())
        self.assertTrue(np.isfinite(p.iloc[2].interest_rate))

    def test_future_changes_do_not_change_past_features(self):
        m, r, c = fixture()
        p, _ = prepare(m, r, c)
        a = replay(p, c)
        changed = r.copy()
        changed.loc[pd.to_datetime(r.available_at) >= pd.Timestamp("2023-01-01", tz="UTC"), "value"] *= 1.1
        q, _ = prepare(m, changed, c)
        b = replay(q, c)
        np.testing.assert_array_equal(a.loc[:"2022"], b.loc[:"2022"])

    def test_state_persists_and_reset_is_repeatable(self):
        m, r, c = fixture()
        p, _ = prepare(m, r, c)
        a, b = replay(p, c), replay(p, c)
        np.testing.assert_array_equal(a, b)
        self.assertNotEqual(a.iloc[4].world_inflation, a.iloc[5].world_inflation)
        self.assertFalse(np.allclose(a.iloc[2:], replay(p, c, stateful=False).iloc[2:]))

    def test_purge_and_common_test_rows(self):
        m, r, c = fixture()
        p, _ = prepare(m, r, c)
        usable = np.isfinite(p[["target", "b0"]]).all(axis=1).to_numpy()
        train, val, test = fold_masks(p, c["folds"][0], usable)
        self.assertTrue((p.label_end[train] < pd.Timestamp(c["folds"][0]["validation_start"])).all())
        self.assertTrue((p.label_end[val] < pd.Timestamp(c["folds"][0]["test_start"])).all())
        self.assertFalse(np.any(train & test))

    def test_shuffle_preserves_partitions_and_column_pairs(self):
        x = np.column_stack([np.arange(120), -np.arange(120)])
        masks = [np.arange(120) // 40 == k for k in range(3)]
        y = permute_blocks(x, masks, 5, np.random.default_rng(3))
        for mask in masks:
            np.testing.assert_array_equal(np.sort(y[mask, 0]), x[mask, 0])
        np.testing.assert_array_equal(y[:, 0], -y[:, 1])
        self.assertFalse(np.array_equal(x, y))

    def test_test_labels_do_not_select_model(self):
        rng = np.random.default_rng(8)
        x = rng.normal(size=(120, 3))
        b0 = np.ones(120)
        y = 1 + .1 * x[:, 0]
        masks = [np.arange(120) // 40 == k for k in range(3)]
        a, params = select_predict(x, y, b0, *masks, [.1, 1.])
        y[masks[2]] = 100
        b, other = select_predict(x, y, b0, *masks, [.1, 1.])
        np.testing.assert_array_equal(a, b)
        self.assertEqual(params, other)
        self.assertTrue((a >= 0).all())

    def test_zero_signal_falls_back(self):
        x, y, b0 = np.zeros((120, 3)), np.ones(120), np.ones(120)
        masks = [np.arange(120) // 40 == k for k in range(3)]
        p, model = select_predict(x, y, b0, *masks, [.1, 1.])
        self.assertFalse(model["active"])
        np.testing.assert_array_equal(p, b0[masks[2]])

    def test_invalid_inputs_rejected(self):
        m, r, c = fixture()
        with self.assertRaises(ValueError):
            prepare(pd.concat([m.iloc[:1], m]), r, c)
        r.loc[0, "unit"] = "percent"
        with self.assertRaises(ValueError):
            prepare(m, r, c)

    def test_late_revision_does_not_replace_newer_observation(self):
        m, r, c = fixture()
        old = r.iloc[[0]].copy()
        old["available_at"] = "2020-02-15T00:00:00Z"
        old["value"] = .1
        revised, _ = prepare(m, pd.concat([r, old], ignore_index=True), c)
        normal, _ = prepare(m, r, c)
        self.assertEqual(revised.loc["2020-02-16"].interest_rate, normal.loc["2020-02-16"].interest_rate)

    def test_revision_of_same_observation_keeps_initial_value(self):
        m, r, c = fixture()
        revision = r.iloc[[0]].copy()
        revision["available_at"] = "2020-01-15T00:00:00Z"
        revision["value"] = .1
        revised, _ = prepare(m, pd.concat([r, revision], ignore_index=True), c)
        self.assertEqual(revised.loc["2020-01-20"].interest_rate, r.iloc[0].value)

    def test_fred_initial_release_normalization(self):
        rows = normalize("inflation", "CPIAUCSL", "year_over_year", [
            {"date": "2019-01-01", "realtime_start": "2019-02-13", "value": "100"},
            {"date": "2020-01-01", "realtime_start": "2020-02-13", "value": "102.5"},
        ], "2020-01-01")
        self.assertEqual(rows[0]["available_at"], "2020-02-14T00:00:00+00:00")
        self.assertEqual(rows[0]["observation_at"], "2020-01-01T00:00:00+00:00")
        self.assertAlmostEqual(rows[0]["value"], .025)
        self.assertEqual(rows[0]["unit"], "fraction")

    def test_fred_gdp_growth_is_computed_from_known_initial_releases(self):
        rows = normalize("gdp_growth", "GDPC1", "annualized_quarter", [
            {"date": "2019-10-01", "realtime_start": "2020-01-30", "value": "100"},
            {"date": "2020-01-01", "realtime_start": "2020-04-29", "value": "101"},
        ], "2020-01-01")
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["value"], 1.01 ** 4 - 1)
        self.assertIn("units=lin", rows[0]["source"])

    def test_fred_error_is_read_without_exposing_key(self):
        key = "a" * 32
        error = HTTPError("https://example.invalid?api_key=" + key, 400, "Bad Request", {},
                          BytesIO(b'{"error_message":"The value for api_key is not registered"}'))
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(FredRequestError) as caught:
                request_series("FEDFUNDS", key, "2019-01-01", "2020-01-01")
        self.assertIn("api_key is not registered", str(caught.exception))
        self.assertNotIn(key, str(caught.exception))

    def test_fred_initial_release_request_uses_linear_units(self):
        response = BytesIO(b'{"observations": []}')
        response.__enter__ = lambda value: value
        response.__exit__ = lambda *args: None
        with patch("urllib.request.urlopen", return_value=response) as opened:
            request_series("CPIAUCSL", "a" * 32, "2018-01-01", "2020-01-01")
        url = opened.call_args.args[0].full_url
        self.assertIn("output_type=4", url)
        self.assertIn("units=lin", url)
        self.assertNotIn("units=pc1", url)

    def test_missing_provenance_blocks_before_output(self):
        m, r, c = fixture()
        c["release_provenance"] = ""
        with self.assertRaises(ValueError):
            evaluate(m, r, c)

    def test_cli_never_overwrites(self):
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run([sys.executable, "-m", "KHKT_Evaluation.rq1_v2.run",
                                     "--market", "missing", "--releases", "missing", "--config", "missing",
                                     "--output", folder], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            # Windows có thể đổi cách biểu diễn tiếng Việt theo bảng mã của
            # cửa sổ lệnh. Điều cần kiểm tra là mã thoát và thư mục còn nguyên.
            self.assertTrue(result.stderr)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_pipeline_repeatability(self):
        m, r, c = fixture()
        a, frame, controls = evaluate(m, r, c)
        b, again, again_controls = evaluate(m, r, copy.deepcopy(c))
        pd.testing.assert_frame_equal(frame, again)
        np.testing.assert_array_equal(controls, again_controls)
        self.assertEqual(a["metrics"], b["metrics"])
        self.assertFalse(a["h1_confirmed"])
        self.assertEqual(controls.shape, (200, len(frame)))
        self.assertTrue(np.isfinite(controls).all())


if __name__ == "__main__":
    unittest.main()
