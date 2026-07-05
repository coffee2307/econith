#!/usr/bin/env python3
"""Send an immediate data-volume report to Telegram (manual test / on-demand)."""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Allow running as: python scripts/send_report_now.py
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from alerts import get_alert_dispatcher
from config import CollectorConfig
from reporting import (
    DataReportConfig,
    build_report_message,
    collect_data_stats,
    load_report_state,
    save_report_state,
)


async def main() -> int:
    cfg = CollectorConfig()
    report_cfg = DataReportConfig.from_env(cfg.data_root)
    stats = collect_data_stats(cfg.data_root)
    state = load_report_state()
    previous_bytes = int(state.get("last_bytes", 0))
    now = datetime.now(ZoneInfo(report_cfg.timezone))
    report_date = now.strftime("%Y-%m-%d")
    message, context = build_report_message(
        stats, cfg=report_cfg, previous_bytes=previous_bytes, report_time=now
    )
    alerts = get_alert_dispatcher()
    ok = await alerts.send_info(f"daily_data_report_{report_date}", message, context=context)
    if ok:
        save_report_state(stats.total_bytes, report_date)
    await alerts.aclose()
    print("sent:", ok)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
