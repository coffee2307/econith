# -*- coding: utf-8 -*-
"""EXP_006: B0/E1/C1 on historical event windows (pre/during/post) with global OOS beta."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from KHKT_Evaluation.common import data_loader, event_library, io_utils, paths, plotting, vol_eval

EXP_DIR = Path(__file__).resolve().parent
RESULTS = EXP_DIR / "results"
PLOTS = RESULTS / "plots"


def run() -> dict:
    cfg = io_utils.load_yaml(EXP_DIR / "config.yaml")
    logger = io_utils.setup_logger("EXP_006", paths.LOGS / "EXP_006.log")
    RESULTS.mkdir(parents=True, exist_ok=True)
    PLOTS.mkdir(parents=True, exist_ok=True)

    try:
        path = data_loader.resolve_btc_features()
        df = data_loader.load_btc_panel(max_rows=None, stride=int(cfg.get("stride", 2)))
    except FileNotFoundError as exc:
        payload = {"experiment": "EXP_006", "status": "blocked", "reason": str(exc), "fabricated": False}
        io_utils.write_json(RESULTS / "metrics.json", payload)
        return payload

    if "ts_ms" not in df.columns:
        payload = {"experiment": "EXP_006", "status": "blocked", "reason": "ts_ms missing", "fabricated": False}
        io_utils.write_json(RESULTS / "metrics.json", payload)
        return payload

    # Fit beta once on full panel (train 70%); never refit inside event windows.
    global_ev = vol_eval.evaluate_b0_e1_c1(
        df,
        hist_vol_window=int(cfg.get("hist_vol_window", 48)),
        fwd_vol_horizon=int(cfg.get("fwd_vol_horizon", 48)),
        macro_sample_every=int(cfg.get("macro_sample_every", 5)),
        seed=int(cfg.get("seed", 42)),
        protocol=str(cfg.get("protocol", "oos_beta")),
        train_frac=float(cfg.get("train_frac", 0.70)),
    )

    min_rows = int(cfg.get("min_rows_per_event", 80))
    pre_days = int(cfg.get("pre_days", 14))
    post_days = int(cfg.get("post_days", 14))
    per_event = []
    raw_rows = []

    for ev_meta in event_library.event_windows():
        phases = event_library.split_pre_during_post(
            ev_meta["start"],
            ev_meta["end"],
            pre_days=pre_days,
            post_days=post_days,
        )
        phase_scores = vol_eval.score_event_windows(
            df, global_ev, phases, restrict_to_test=False
        )
        # Also during-only aggregate for summary compatibility
        during = next((p for p in phase_scores if p["phase"] == "during"), None)
        entry = {
            "event_id": ev_meta["id"],
            "name": ev_meta["name"],
            "kind": ev_meta["kind"],
            "start": ev_meta["start"],
            "end": ev_meta["end"],
            "phases": phase_scores,
            "beta": global_ev.get("beta"),
            "lag": global_ev.get("lag"),
            "fingerprint": global_ev.get("fingerprint"),
        }
        if during is None or during.get("n", 0) < min_rows:
            entry["status"] = "insufficient_rows"
            entry["rows"] = int(during["n"]) if during else 0
            entry["limitation"] = f"need >= {min_rows} rows in during window"
            per_event.append(entry)
            logger.warning("skip %s during_n=%s", ev_meta["id"], entry["rows"])
            continue

        entry.update(
            {
                "status": "ok",
                "rows": int(during["n"]),
                "metrics": during["metrics"],
                "comparison": during["comparison"],
                "aux": during.get("aux"),
            }
        )
        ts = df["ts_ms"].to_numpy()
        dmask = (ts >= phases[1]["start_ts_ms"]) & (ts <= phases[1]["end_ts_ms"])
        y_e1_leg, y_c1_leg, _ = vol_eval._legacy_mult_forecasts(  # noqa: SLF001
            global_ev["y_b0"], global_ev["vol_mult"], seed=int(cfg.get("seed", 42))
        )
        legacy_during = vol_eval._score_slice(  # noqa: SLF001
            global_ev["y_true"], global_ev["y_b0"], y_e1_leg, y_c1_leg, dmask
        )
        entry["protocol_legacy_during"] = {
            "metrics": legacy_during["metrics"],
            "comparison": legacy_during["comparison"],
        }
        per_event.append(entry)

        for phase in phase_scores:
            for model in ("B0", "E1", "C1"):
                raw_rows.append(
                    {
                        "event_id": ev_meta["id"],
                        "phase": phase["phase"],
                        "model": model,
                        "mae": phase["metrics"][model]["mae"],
                        "rmse": phase["metrics"][model]["rmse"],
                        "directional_accuracy": phase["metrics"][model]["directional_accuracy"],
                        "delta_vol_mae": phase["aux"]["delta_vol_mae"][model],
                        "regime_dir_acc": phase["aux"]["regime_directional_accuracy"][model],
                        "n": phase["metrics"][model]["n"],
                    }
                )
        logger.info(
            "%s during_n=%d e1>b0=%s pass_c1=%s",
            ev_meta["id"],
            during["n"],
            during["comparison"]["e1_better_mae_than_b0"],
            during["comparison"]["passes_random_control"],
        )
    ok_events = [e for e in per_event if e.get("status") == "ok"]
    wins_b0 = sum(1 for e in ok_events if e["comparison"].get("e1_better_mae_than_b0"))
    wins_c1 = sum(1 for e in ok_events if e["comparison"].get("passes_random_control"))
    legacy_wins_b0 = sum(
        1
        for e in ok_events
        if (e.get("protocol_legacy_during") or {}).get("comparison", {}).get("e1_better_mae_than_b0")
    )

    payload = {
        "experiment": "EXP_006",
        "status": "completed" if ok_events else "blocked",
        "rq": "RQ1",
        "fabricated": False,
        "dataset": data_loader.dataset_provenance(df, path),
        "protocol": global_ev.get("protocol"),
        "beta": global_ev.get("beta"),
        "lag": global_ev.get("lag"),
        "fingerprint": global_ev.get("fingerprint"),
        "global_oos_metrics": global_ev.get("metrics"),
        "global_oos_comparison": global_ev.get("comparison"),
        "protocol_legacy_global": global_ev.get("protocol_legacy"),
        "min_rows_per_event": min_rows,
        "pre_days": pre_days,
        "post_days": post_days,
        "events": per_event,
        "summary": {
            "events_ok": len(ok_events),
            "events_total": len(per_event),
            "e1_beats_b0_count": wins_b0,
            "e1_beats_c1_count": wins_c1,
            "legacy_e1_beats_b0_count": legacy_wins_b0,
        },
        "limitations": [
            "Beta fit once on global train; event scores use fixed beta (no per-event refit).",
            "Event labels are calendar windows; not exchange-official tags.",
            "Only events overlapping BTC feature span (~2022-04 to 2026-07) are included.",
            "Offline World overlay proxy — not live EventBus SIMULATION.",
        ],
    }
    if not ok_events:
        payload["reason"] = "no event window had enough rows"
        payload["todo"] = ["Expand dataset coverage or relax min_rows_per_event carefully"]

    # Strip non-JSON arrays from nested global_ev leftovers — already only metrics
    io_utils.write_json(RESULTS / "metrics.json", payload)
    io_utils.write_csv(RESULTS / "raw_results.csv", raw_rows)
    io_utils.write_csv(RESULTS / "metrics.csv", raw_rows)

    labels, values = [], []
    for e in ok_events:
        labels.append(f"{e['event_id'][:12]}_B0")
        values.append(float(e["metrics"]["B0"]["mae"]))
        labels.append(f"{e['event_id'][:12]}_E1")
        values.append(float(e["metrics"]["E1"]["mae"]))
    if labels:
        plotting.save_bar_chart(
            PLOTS / "event_mae.png",
            labels,
            values,
            title="EXP_006 — During-event MAE (B0 vs E1, oos_beta)",
            ylabel="MAE",
        )

    lines = [
        "| Event | Phase | B0 MAE | E1 MAE | C1 MAE | Δvol E1 | RegDir E1 | E1>B0 | E1>C1 |",
        "|---|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for e in ok_events:
        for ph in e.get("phases") or []:
            if ph.get("n", 0) <= 0:
                continue
            lines.append(
                f"| {e['event_id']} | {ph['phase']} | {ph['metrics']['B0']['mae']:.5g} | "
                f"{ph['metrics']['E1']['mae']:.5g} | {ph['metrics']['C1']['mae']:.5g} | "
                f"{ph['aux']['delta_vol_mae']['E1']:.5g} | "
                f"{ph['aux']['regime_directional_accuracy']['E1']:.4f} | "
                f"{ph['comparison']['e1_better_mae_than_b0']} | "
                f"{ph['comparison']['passes_random_control']} |"
            )

    io_utils.write_markdown(
        EXP_DIR / "report.md",
        f"""# EXP_006 — Historical Event Replay (RQ1)

## Mục tiêu
So sánh B0/E1/C1 trong cửa sổ **pre / during / post** sự kiện lịch sử, với β OOS fit toàn cục (không refit trong event).

## Protocol
- oos_beta global: beta={global_ev.get('beta')}, lag={global_ev.get('lag')}, fingerprint=`{global_ev.get('fingerprint')}`
- Legacy multiplicative during-window wins E1>B0: {legacy_wins_b0}

## Tóm tắt (during)
- Events OK: {payload['summary']['events_ok']}/{payload['summary']['events_total']}
- E1 beats B0: {wins_b0}
- E1 beats C1 (random control): {wins_c1}

{chr(10).join(lines)}

## Limitations
{chr(10).join('- ' + x for x in payload['limitations'])}
""",
    )
    return payload


if __name__ == "__main__":
    run()
