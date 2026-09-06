"""ECONITH VPS Collector :: reporting

Daily data-volume snapshot + Telegram digest.

Collects on-disk stats for the raw lake (total size, file count, per-desk
breakdown, VPS disk headroom) and computes growth since the previous report.
The scheduler fires once per day at a configurable local time (default 12:00
Asia/Ho_Chi_Minh).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger("econith.vps.reporting")

__all__ = [
    "DataReportConfig",
    "DataStats",
    "collect_data_stats",
    "seconds_until_next_report",
    "format_bytes",
    "load_report_state",
    "save_report_state",
    "build_report_message",
]

_STATE_FILE = Path(".report_state.json")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class DataReportConfig:
    """Daily report schedule and paths."""

    data_root: Path = Path("datasets/raw")
    enabled: bool = True
    hour: int = 12
    minute: int = 0
    timezone: str = "Asia/Ho_Chi_Minh"
    disk_warn_pct: float = 80.0

    @classmethod
    def from_env(cls, data_root: Path | str = "datasets/raw") -> "DataReportConfig":
        return cls(
            data_root=Path(data_root),
            enabled=_env_bool("REPORT_ENABLED", True),
            hour=int(os.getenv("REPORT_HOUR", "12") or 12),
            minute=int(os.getenv("REPORT_MINUTE", "0") or 0),
            timezone=os.getenv("REPORT_TIMEZONE", "Asia/Ho_Chi_Minh"),
            disk_warn_pct=float(os.getenv("REPORT_DISK_WARN_PCT", "80") or 80),
        )


@dataclass(slots=True)
class DataStats:
    total_bytes: int = 0
    file_count: int = 0
    desk_bytes: dict[str, int] | None = None
    disk_total_bytes: int = 0
    disk_used_bytes: int = 0
    disk_free_bytes: int = 0
    disk_used_pct: float = 0.0
    rows_written: int = 0
    messages: int = 0
    ws_failures: int = 0

    def __post_init__(self) -> None:
        if self.desk_bytes is None:
            self.desk_bytes = {}


def format_bytes(num_bytes: int | float) -> str:
    """Human-readable size (binary units)."""
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{value:.2f} TB"


def collect_data_stats(
    data_root: Path,
    *,
    rows_written: int = 0,
    messages: int = 0,
    ws_failures: int = 0,
) -> DataStats:
    """Walk the raw lake and measure partition + disk usage."""
    stats = DataStats(
        rows_written=rows_written,
        messages=messages,
        ws_failures=ws_failures,
    )
    root = Path(data_root)

    if root.exists():
        desk_totals: dict[str, int] = {}
        for parquet in root.rglob("*.parquet"):
            try:
                size = parquet.stat().st_size
            except OSError:
                continue
            stats.total_bytes += size
            stats.file_count += 1
            parts = parquet.relative_to(root).parts
            # market/<desk>/<symbol>/...
            desk = parts[1] if len(parts) >= 2 else "other"
            desk_totals[desk] = desk_totals.get(desk, 0) + size
        stats.desk_bytes = dict(sorted(desk_totals.items()))

    try:
        import shutil

        usage = shutil.disk_usage(root if root.exists() else Path("."))
        stats.disk_total_bytes = usage.total
        stats.disk_used_bytes = usage.used
        stats.disk_free_bytes = usage.free
        stats.disk_used_pct = (usage.used / usage.total * 100.0) if usage.total else 0.0
    except OSError as exc:
        logger.warning("disk_usage failed: %s", exc)

    return stats


def seconds_until_next_report(hour: int, minute: int, timezone: str) -> float:
    """Seconds until the next scheduled report in the given IANA timezone."""
    tz = ZoneInfo(timezone)
    now = datetime.now(tz)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max(1.0, (target - now).total_seconds())


def load_report_state(state_path: Path | None = None) -> dict:
    path = state_path or _STATE_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def save_report_state(
    total_bytes: int,
    report_date: str,
    state_path: Path | None = None,
) -> None:
    path = state_path or _STATE_FILE
    payload = {"last_bytes": total_bytes, "last_report_date": report_date}
    try:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("could not save report state (%s): %s", path, exc)


def build_report_message(
    stats: DataStats,
    *,
    cfg: DataReportConfig,
    previous_bytes: int = 0,
    report_time: datetime | None = None,
) -> tuple[str, dict[str, str | int | float]]:
    """Build Telegram message body + context dict for AlertDispatcher."""
    now = report_time or datetime.now(ZoneInfo(cfg.timezone))
    delta = stats.total_bytes - previous_bytes
    delta_sign = "+" if delta >= 0 else ""

    desk_lines = []
    if stats.desk_bytes:
        for desk, size in stats.desk_bytes.items():
            desk_lines.append(f"  {desk}: {format_bytes(size)}")

    message_lines = [
        f"Báo cáo dung lượng data — {now.strftime('%Y-%m-%d %H:%M')} ({cfg.timezone})",
        "",
        f"Tổng raw lake: {format_bytes(stats.total_bytes)}",
        f"Tăng 24h: {delta_sign}{format_bytes(delta)}",
        f"Số file parquet: {stats.file_count:,}",
        f"Rows ghi (session): {stats.rows_written:,}",
        f"Messages (session): {stats.messages:,}",
        f"WS failures (session): {stats.ws_failures}",
        "",
        f"Disk VPS: {stats.disk_used_pct:.1f}% used ({format_bytes(stats.disk_free_bytes)} free / {format_bytes(stats.disk_total_bytes)})",
    ]
    if desk_lines:
        message_lines.extend(["", "Theo desk:", *desk_lines])

    context = {
        "total": format_bytes(stats.total_bytes),
        "delta_24h": f"{delta_sign}{format_bytes(delta)}",
        "files": stats.file_count,
        "disk_used_pct": round(stats.disk_used_pct, 1),
        "disk_free": format_bytes(stats.disk_free_bytes),
    }
    return "\n".join(message_lines), context
