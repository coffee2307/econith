"""Portable fail-closed host resource checks for finite research runners.

No process stops, no system setting changes, no external packages. Linux uses
procfs and cgroup v2 (if limited) to avoid overstating container headroom.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil


def _linux_memory_available_bytes() -> int:
    if os.name != "posix":
        raise RuntimeError("Linux-like resource probe expected")
    try:
        meminfo = Path("/proc/meminfo").read_text(encoding="ascii")
        values = {}
        for line in meminfo.splitlines():
            parts = line.split()
            if parts and parts[0].endswith(":") and len(parts) >= 2:
                values[parts[0][:-1]] = int(parts[1])
        free = values["MemAvailable"] * 1024
        if free <= 0:
            raise ValueError("nonpositive MemAvailable")
        # For cgroup v2 runners, stay below both the host and container limits.
        group = Path("/sys/fs/cgroup")
        limit = group / "memory.max"
        current = group / "memory.current"
        if limit.exists() and current.exists():
            raw = limit.read_text(encoding="ascii").strip()
            if raw != "max":
                capacity = int(raw)
                used = int(current.read_text(encoding="ascii").strip())
                if capacity <= 0 or used < 0:
                    raise ValueError("invalid cgroup memory counters")
                free = min(free, max(0, capacity - used))
        return free
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Linux memory resource measurement unavailable") from exc


def host_snapshot(root: Path) -> dict:
    if not root.is_dir() or root.is_symlink() or root != root.resolve():
        raise RuntimeError("unaliasable operator evidence root")
    if os.name == "nt":
        # Reuse the previously reviewed Windows GlobalMemoryStatusEx probe.
        from ci.phase1_public_spool_timed_watch import _windows_available_memory_bytes
        free_mem = _windows_available_memory_bytes()
    elif os.name == "posix":
        free_mem = _linux_memory_available_bytes()
    else:
        raise RuntimeError("unsupported research runner host")
    disk = shutil.disk_usage(root)
    evidence_bytes = 0
    for item in root.rglob("*"):
        if item.is_symlink():
            raise RuntimeError("symbolic link under operator evidence root")
        if item.is_file():
            evidence_bytes += item.stat().st_size
    return {
        "available_memory_bytes":free_mem,
        "disk_free_bytes":disk.free,
        "evidence_file_bytes":evidence_bytes,
    }
