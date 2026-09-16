"""Chạy RQ1 v2 và lưu kết quả riêng, không sửa báo cáo hiện hành."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from KHKT_Evaluation.common.metrics import diebold_mariano
from KHKT_Evaluation.rq1_v2.data import prepare
from KHKT_Evaluation.rq1_v2.model import permute_blocks, select_predict
from KHKT_Evaluation.rq1_v2.world import replay
from KHKT_Evaluation.rq1_v2.innovations import release_features, enrich, causal_world_features


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def score(y, pred):
    return {"mae": float(np.mean(np.abs(y - pred))),
            "rmse": float(np.sqrt(np.mean((y - pred) ** 2)))}


def validate_config(cfg):
    scale = cfg.get("reaction_daily_scale", 1.)
    if not np.isfinite(scale) or not 0 < scale <= 1:
        raise ValueError("reaction_daily_scale phải thuộc (0, 1].")
    if cfg["mode"] not in ("exploratory", "confirmatory"):
        raise ValueError("mode phải là exploratory hoặc confirmatory.")
    if cfg["mode"] == "confirmatory" and not cfg.get("unseen_data_declaration"):
        raise ValueError("Cần xác nhận tập kiểm tra chưa được dùng để chọn thiết kế.")
    if not cfg.get("release_provenance"):
        raise ValueError("Cần mô tả nguồn ngày công bố và phiên bản dữ liệu.")
    if cfg["control_repeats"] < 200 or cfg["bootstrap_repeats"] < 200:
        raise ValueError("Cần ít nhất 200 đối chứng và 200 lần lấy mẫu lặp.")
    if not cfg["alphas"] or any(not np.isfinite(a) or a <= 0 for a in cfg["alphas"]):
        raise ValueError("Hệ số Ridge phải hữu hạn và dương.")
    freq = pd.Timedelta(cfg["frequency"])
    if pd.Timedelta(cfg["control_block"]) % freq or pd.Timedelta(cfg["control_block"]) < pd.Timedelta(cfg["horizon"]):
        raise ValueError("Khối đối chứng phải là bội frequency và không ngắn hơn horizon.")
    if len(cfg["folds"]) < 2:
        raise ValueError("Cần ít nhất hai cửa sổ ngoài mẫu.")
    blocks = int(cfg.get("validation_blocks", 1))
    wins = int(cfg.get("required_validation_wins", 1))
    if blocks < 1 or not 1 <= wins <= blocks:
        raise ValueError("Số đoạn thắng kiểm định phải thuộc [1, validation_blocks].")
    selection_metrics = tuple(cfg.get("selection_metrics", ("mae", "rmse")))
    if not selection_metrics or any(name not in ("mae", "rmse") for name in selection_metrics):
        raise ValueError("selection_metrics chỉ nhận mae và rmse.")


def fold_masks(panel, fold, valid_rows):
    start, val, test, end = [pd.Timestamp(fold[k]) for k in ("train_start", "validation_start", "test_start", "test_end")]
    if any(t.tzinfo is None for t in (start, val, test, end)) or not start < val < test < end:
        raise ValueError("Mốc thời gian phải có múi giờ và tăng dần.")
    time = panel.index
    train = np.asarray((time >= start) & (time < val) & (panel.label_end < val)) & valid_rows
    validation = np.asarray((time >= val) & (time < test) & (panel.label_end < test)) & valid_rows
    testing = np.asarray((time >= test) & (time < end) & (panel.label_end < end)) & valid_rows
    if min(train.sum(), validation.sum(), testing.sum()) < 32:
        raise ValueError("Mỗi đoạn cần ít nhất 32 nhãn hợp lệ sau loại vùng giao nhau.")
    return train, validation, testing


def uncertainty(frame, cfg, horizon):
    rng = np.random.default_rng(cfg["seed"] + 100000)
    groups = [part for _, part in frame.groupby("fold", sort=False)]
    block = int(pd.Timedelta(cfg["control_block"]) / pd.Timedelta(cfg["frequency"]))
    diffs = []
    for _ in range(cfg["bootstrap_repeats"]):
        sampled = []
        for part in groups:
            n = len(part)
            # Circular block bootstrap, riêng từng fold; không ghép qua khoảng trống.
            starts = rng.integers(0, n, size=int(np.ceil(n / block)))
            idx = np.concatenate([(s + np.arange(block)) % n for s in starts])[:n]
            sampled.append(part.iloc[idx])
        sample = pd.concat(sampled)
        a, b = score(sample.target, sample.E1), score(sample.target, sample.B0)
        diffs.append([a[k] - b[k] for k in ("mae", "rmse")])
    ci = np.quantile(diffs, [.025, .975], axis=0)
    return {
        "ci95_difference_E1_minus_B0": {k: ci[:, i].tolist() for i, k in enumerate(("mae", "rmse"))},
        "dm_by_fold": {str(i): diebold_mariano(p.target.to_numpy(), p.E1.to_numpy(), p.B0.to_numpy(), h=horizon, loss="abs")
                       for i, p in frame.groupby("fold")},
        "note": "Khoảng tin cậy có điều kiện trên dự báo đã khớp; không bao phủ mọi lựa chọn thiết kế trước đây.",
    }


def evaluate(market, releases, cfg):
    validate_config(cfg)
    panel, horizon = prepare(market, releases, cfg)
    macro = list(cfg["macro_features"])
    predictions, controls, diagnostics = [], [], []
    block = int(pd.Timedelta(cfg["control_block"]) / pd.Timedelta(cfg["frequency"]))
    previous_end = None
    for number, fold in enumerate(cfg["folds"]):
        end, test_start = pd.Timestamp(fold["test_end"]), pd.Timestamp(fold["test_start"])
        if previous_end is not None and test_start < previous_end:
            raise ValueError("Các khoảng kiểm tra không được chồng lấn.")
        previous_end = end
        sub = panel.loc[(panel.index >= pd.Timestamp(fold["train_start"])) & (panel.index < end)].copy()
        dynamic = replay(sub, cfg)
        static = replay(sub, cfg, stateful=False)
        causal_mode = cfg.get("causal_world_impulses", False)
        if causal_mode:
            dynamic = causal_world_features(sub, cfg)
        elif cfg.get("release_innovations", False):
            dynamic = enrich(sub, dynamic, cfg)
        x = dynamic.to_numpy(dtype=float)
        feature_names = list(dynamic.columns)
        raw = sub[macro].to_numpy(dtype=float)
        if causal_mode:
            event_frame = release_features(sub, macro)
            event_frame = event_frame[[name + "_innovation" for name in macro]]
            raw = np.column_stack([raw, event_frame.to_numpy(dtype=float)])
        elif cfg.get("release_innovations", False):
            event_frame = release_features(sub, macro)
            events = event_frame.to_numpy(dtype=float)
            feature_names.extend(event_frame.columns)
            raw = np.column_stack([raw, events])
            # B1 cũng nhận thông tin công bố; không gán lợi ích của biến mới cho World.
            x = np.column_stack([x, events])
        xs = static.to_numpy(dtype=float)
        y, b0 = sub.target.to_numpy(), sub.b0.to_numpy()
        usable = np.isfinite(np.column_stack([x, raw, xs, y, b0])).all(axis=1)
        train, val, test = fold_masks(sub, fold, usable)
        if not np.any(x[train].std(axis=0) > 1e-12):
            raise ValueError("Toàn bộ đặc trưng World bất biến trên train.")
        # Thiếu giá trong vùng kiểm tra không được âm thầm nén thành chuỗi liên tục.
        test_positions = np.flatnonzero(test)
        if np.any(np.diff(test_positions) != 1):
            raise ValueError("Tập kiểm tra có khoảng thiếu dữ liệu; tách fold hoặc sửa nguồn.")
        frame = pd.DataFrame({"time": sub.index[test], "fold": number, "target": y[test], "B0": b0[test]})
        selected = {}
        # Xung World dùng mốc 0 có ý nghĩa: 0 là không còn tác động sự kiện.
        # Các biến mức vẫn được trừ trung bình như trước.
        world_center = not causal_mode
        variants = {
            "B1": (raw, True),
            "E1": (x, world_center),
            "E1_plus": (
                np.column_stack([raw, x]),
                np.r_[np.ones(raw.shape[1], dtype=bool),
                      np.full(x.shape[1], world_center, dtype=bool)],
            ),
            "E_static": (xs, True),
        }
        selection = {
            "validation_blocks": int(cfg.get("validation_blocks", 1)),
            "required_validation_wins": int(cfg.get("required_validation_wins", 1)),
            "min_relative_gain": float(cfg.get("min_validation_improvement", 0.)),
            "shrinkages": tuple(cfg.get("shrinkages", [1.])),
            "selection_metrics": tuple(cfg.get("selection_metrics", ("mae", "rmse"))),
        }
        for name, (features, center_features) in variants.items():
            frame[name], selected[name] = select_predict(
                features, y, b0, train, val, test, cfg["alphas"],
                center_features=center_features, **selection)
        fold_controls = []
        for repeat in range(cfg["control_repeats"]):
            rng = np.random.default_rng(np.random.SeedSequence([cfg["seed"], number, repeat]))
            shuffled = permute_blocks(x, (train, val, test), block, rng)
            pred, _ = select_predict(
                shuffled, y, b0, train, val, test, cfg["alphas"],
                center_features=world_center, **selection)
            fold_controls.append(pred)
        predictions.append(frame)
        controls.append(np.asarray(fold_controls))
        diagnostics.append({"fold": number, "config": fold, "n_train": int(train.sum()),
                            "n_validation": int(val.sum()), "n_test": int(test.sum()),
                            "selected": selected, "world_features": feature_names,
                            "constant_train_features": list(np.array(feature_names)[x[train].std(axis=0) <= 1e-12]),
                            "metrics": {name: score(frame.target, frame[name]) for name in ("B0", *variants)}})
    frame = pd.concat(predictions, ignore_index=True)
    shuffled_predictions = np.concatenate(controls, axis=1)
    observed = {name: score(frame.target, frame[name]) for name in ("B0", "B1", "E1", "E1_plus", "E_static")}
    control_scores = [score(frame.target.to_numpy(), p) for p in shuffled_predictions]
    random_summary = {key: {"median": float(np.median([r[key] for r in control_scores])),
                            "interval95": np.quantile([r[key] for r in control_scores], [.025, .975]).tolist(),
                            "share_C1_not_worse_than_E1": float(np.mean([r[key] <= observed["E1"][key] for r in control_scores]))}
                      for key in ("mae", "rmse")}
    stats = uncertainty(frame, cfg, horizon)
    better = all(observed["E1"][k] < observed["B0"][k] and
                 random_summary[k]["share_C1_not_worse_than_E1"] <= .05 and
                 stats["ci95_difference_E1_minus_B0"][k][1] < 0 for k in ("mae", "rmse"))
    result = {"protocol": "rq1_v2", "mode": cfg["mode"], "status": "completed",
              "metrics": observed, "C1": random_summary, "uncertainty": stats, "folds": diagnostics,
              "meets_prespecified_numeric_criteria": bool(better),
              "h1_confirmed": False,
              "interpretation": "Chưa tự động kết luận H1; cần rà nguồn dữ liệu, độ ổn định giữa fold và lịch sử chọn mô hình.",
              "limitations": ["Replay USA rút gọn: CentralBankModel và SentimentModel; không phải toàn bộ World 150 quốc gia.",
                              "Tham số phản ứng giữ nguyên từ mã gốc, chưa được hiệu chỉnh lại trong V2.",
                              "Dữ liệu đã xem chỉ dùng thăm dò, không tạo lại tập kiểm tra chưa từng mở.",
                              "C1 là phép thử phá cấu trúc theo khối, không phải mô hình dự báo nhân quả có thể triển khai.",
                              "Phạm vi USA không kiểm tra lan truyền thương mại toàn cầu."]}
    event_rows = []
    for event in cfg.get("events", []):
        start, end = pd.Timestamp(event["start"]), pd.Timestamp(event["end"])
        part = frame.loc[(frame.time >= start) & (frame.time < end)]
        event_rows.append({"name": event["name"], "n": len(part),
                           "metrics": {name: score(part.target, part[name]) for name in observed} if len(part) else None})
    result["events_oos_only"] = event_rows
    return result, frame, shuffled_predictions


def clean_json(value):
    if isinstance(value, dict):
        return {k: clean_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean_json(v) for v in value]
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", required=True, type=Path)
    parser.add_argument("--releases", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--innovation-preset", action="store_true",
                        help="Thăm dò tín hiệu công bố và World phản thực tế; giữ giao thức cũ nếu bỏ cờ.")
    parser.add_argument("--horizon-days", type=int, choices=(3, 7, 14))
    parser.add_argument("--causal-world-preset", action="store_true",
                        help="World theo tác động riêng của từng công bố và chọn mô hình ổn định.")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output đã tồn tại; dùng thư mục mới để giữ nguyên kết quả cũ.")
    try:
        cfg = json.loads(args.config.read_text(encoding="utf-8"))
        if args.innovation_preset:
            cfg.update(mode="exploratory", release_innovations=True,
                       reaction_daily_scale=1 / 30)
        if args.causal_world_preset:
            cfg.update(mode="exploratory", causal_world_impulses=True,
                       release_innovations=False, reaction_daily_scale=1 / 30,
                       impulse_simulation_days=14,
                       impulse_half_lives_days=[3, 7, 14],
                       impulse_max_age_days=42,
                       validation_blocks=3, required_validation_wins=2,
                       min_validation_improvement=.001,
                       selection_metrics=["mae", "rmse"],
                       shrinkages=[.25, .5, 1.])
        if args.horizon_days:
            cfg.update(mode="exploratory", horizon=f"{args.horizon_days}d")
            cfg["control_block"] = f"{max(14, args.horizon_days)}d"
        market = pd.read_parquet(args.market) if args.market.suffix == ".parquet" else pd.read_csv(args.market)
        releases = pd.read_csv(args.releases)
        result, frame, controls = evaluate(market, releases, cfg)
    except (ValueError, FileNotFoundError, KeyError) as exc:
        parser.exit(2, f"RQ1 v2 bị chặn: {exc}\nKhông tạo kết quả nghiên cứu.\n")
    result["provenance"] = {"market_sha256": digest(args.market), "releases_sha256": digest(args.releases),
                            "config_sha256": digest(args.config), "config": cfg,
                            "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                            "git_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()),
                            "numpy": np.__version__, "pandas": pd.__version__}
    args.output.mkdir(parents=True, exist_ok=False)
    frame.to_csv(args.output / "predictions.csv", index=False)
    np.savez_compressed(args.output / "controls.npz", predictions=controls)
    result["prediction_sha256"] = digest(args.output / "predictions.csv")
    result["controls_sha256"] = digest(args.output / "controls.npz")
    (args.output / "metrics.json").write_text(json.dumps(clean_json(result), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(f"Hoàn tất {cfg['mode']}: {args.output}. Chưa thay đổi báo cáo KHKT.")


if __name__ == "__main__":
    main()
