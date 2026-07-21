"""DEMO / paper-trading soak checklist (ops gate before REALITY live keys).

Prints a machine-checkable report. Exit 0 when baseline env is safe for a DEMO
soak; exit 1 when critical flags look wrong for paper trading.

Run:
    python -m scripts.paper_soak_check
    python -m scripts.paper_soak_check --require-demo
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _truthy(name: str, default: str = "") -> bool:
    raw = (os.getenv(name) or default).strip().lower()
    return raw in ("1", "true", "yes", "on")


def run_checks(*, require_demo: bool) -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []

    world_default = _truthy("WORLD_SIMULATION_DEFAULT", "false")
    checks.append(
        (
            "WORLD_SIMULATION_DEFAULT=false (weak PC / soak baseline)",
            not world_default,
            f"got {os.getenv('WORLD_SIMULATION_DEFAULT')!r}",
        )
    )

    mock_twap = _truthy("ECONITH_MOCK_TWAP", "false")
    checks.append(
        (
            "ECONITH_MOCK_TWAP=false (no fake TWAP fills)",
            not mock_twap,
            f"got {os.getenv('ECONITH_MOCK_TWAP')!r}",
        )
    )

    hyp_llm = _truthy("HYPOTHESIS_USE_LLM", "false")
    checks.append(
        (
            "HYPOTHESIS_USE_LLM=false recommended for soak stability",
            not hyp_llm,
            f"got {os.getenv('HYPOTHESIS_USE_LLM')!r}",
        )
    )

    exec_env = (os.getenv("BINANCE_EXECUTION_ENV") or "demo").strip().lower()
    demo_ok = exec_env == "demo"
    checks.append(
        (
            "BINANCE_EXECUTION_ENV=demo for paper soak",
            demo_ok if require_demo else True,
            f"got {exec_env!r}",
        )
    )

    from core.system_controller import AUTONOMOUS_LOOP_IMPLEMENTED

    checks.append(
        (
            "FULLY_AUTONOMOUS still flag-only (AUTONOMOUS_LOOP_IMPLEMENTED=False)",
            AUTONOMOUS_LOOP_IMPLEMENTED is False,
            f"got {AUTONOMOUS_LOOP_IMPLEMENTED}",
        )
    )

    models = Path(os.getenv("MODEL_DIR") or "models")
    active = models / "registry" / "active.yaml"
    checks.append(
        (
            "optional: models/registry/active.yaml present (trained desks)",
            True,  # informational only
            "PRESENT" if active.exists() else "MISSING -- heuristic desks OK for DEMO soak",
        )
    )

    return checks


OPS_STEPS = """
Manual soak steps (record results for N days):
  1. npm run dev; open Main Control; mode=SIMULATION or REALITY+demo keys.
  2. Keep World OFF unless testing World->Quant; if ON, use SIMULATION + bridge.
  3. Confirm fills: no silent synthetic on live reject; Event Log shows REJECTED/UNKNOWN.
  4. Watch Sentinel drawdown/VaR/latency; inject /sentinel/inject once and recover.
  5. Log daily PnL / equity from cockpit snapshot.
  6. Only after clean soak: consider live keys + trained active.yaml desks.
  7. Do NOT enable FULLY_AUTONOMOUS until deploy backtest gate + soak pass.
""".strip()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="ECONITH DEMO paper soak checklist")
    p.add_argument(
        "--require-demo",
        action="store_true",
        help="fail if BINANCE_EXECUTION_ENV is not demo",
    )
    args = p.parse_args(argv)

    # Load .env if present (best-effort).
    env_path = _ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    print("=== ECONITH paper / DEMO soak checklist ===\n")
    failed = 0
    for label, ok, detail in run_checks(require_demo=bool(args.require_demo)):
        mark = "PASS" if ok else "FAIL"
        if not ok:
            failed += 1
        print(f"[{mark}] {label} -- {detail}")
    print("\n" + OPS_STEPS)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
