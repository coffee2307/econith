"""Opt-in bounded source-vs-projector throughput and host-pressure diagnostics.

O(1) per committed event, one small aggregate window per five seconds.
No market frame, source identifier, host path, wall clock or credentials in the
diagnostic output. It is NEVER source/ACK evidence or a continuity attestation.
"""
from __future__ import annotations

import ctypes
import json
import os
import time
from pathlib import Path
from typing import Callable

WINDOW_S = 5
MAX_WINDOWS = 48  # 4 minutes per one finite 120s source + catchup
SUBSECOND_WINDOW_MS = 250
MAX_SUBSECOND_WINDOWS = 960  # four minutes maximum
_FIELDS = ("raw_committed", "cursor_advanced", "eventbus_published",
           "checkpoints", "max_pending_originals", "max_admission_age_ms",
           "max_ingress_fifo_depth", "ingress_fifo_observations",
           "ingress_fifo_90pct_observations", "max_receiver_ack_ms",
           "receiver_ack_observations", "receiver_ack_over_50ms",
           "receiver_ack_over_250ms", "receiver_ack_over_500ms")


def available_physical_memory_bytes() -> int | None:
    """Windows-only, no psutil dependency. Unknown on unsupported hosts."""
    if os.name != "nt":
        return None

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]
    value = MEMORYSTATUSEX()
    value.dwLength = ctypes.sizeof(value)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
        return None
    return int(value.ullAvailPhys)


class BoundedSpoolPressure:
    """Single-event-loop, research-only telemetry; all counters advisory."""

    def __init__(
        self, path: Path, *, clock_ns: Callable[[], int] = time.monotonic_ns,
        cpu_clock_ns: Callable[[], int] = time.process_time_ns,
        memory_probe: Callable[[], int | None] = available_physical_memory_bytes,
    ):
        if (not isinstance(path, Path) or path.name != "spool_pressure.json"
            or not path.parent.is_dir() or path.is_symlink() or path.exists()):
            raise ValueError("pressure diagnostic requires a fresh isolated file")
        self.path = path
        self._clock = clock_ns
        self._cpu_clock = cpu_clock_ns
        self._memory_probe = memory_probe
        self._t0 = self._clock()
        self._cpu0 = self._cpu_clock()
        self._current_index = 0
        self._current = self._new_window()
        self._windows: list[dict] = []
        self._truncated = False
        self._total = {k: 0 for k in _FIELDS[:4]}
        self._peak_backlog = 0
        self._micro_index = 0
        self._micro_current = self._new_micro_window()
        self._micro_windows: list[dict] = []
        self._micro_truncated = False
        self._micro_total = {"enqueued":0,"dequeued":0,"writer_completed":0,"overflow":0}

    @staticmethod
    def _new_window() -> dict:
        return {k: 0 for k in _FIELDS}

    def _roll(self) -> None:
        elapsed = max(0, self._clock() - self._t0)
        target = elapsed // (WINDOW_S * 1_000_000_000)
        while self._current_index < target:
            self._close_window(WINDOW_S)
            self._current_index += 1
            self._current = self._new_window()

    def _close_window(self, seconds: float) -> None:
        if len(self._windows) >= MAX_WINDOWS:
            self._truncated = True
            return
        cpu_now = self._cpu_clock()
        elapsed_cpu_ms = max(0, cpu_now - self._cpu0) / 1_000_000
        self._cpu0 = cpu_now
        sample = dict(self._current)
        sample.update({
            "elapsed_start_s":self._current_index * WINDOW_S,
            "observed_duration_s":round(seconds, 3),
            "raw_originals_per_s":round(sample["raw_committed"] / seconds, 3),
            "cursor_originals_per_s":round(sample["cursor_advanced"] / seconds, 3),
            "process_cpu_time_ms":round(elapsed_cpu_ms, 3),
            "host_available_memory_bytes":self._memory_probe(),
        })
        self._windows.append(sample)

    def _incr(self, key: str) -> None:
        self._roll()
        self._current[key] += 1
        self._total[key] += 1

    def raw(self, backlog: int) -> None:
        if type(backlog) is not int or backlog < 0:
            raise ValueError("invalid post-commit spool backlog")
        self._incr("raw_committed")
        self._current["max_pending_originals"] = max(
            self._current["max_pending_originals"], backlog,
        )
        self._peak_backlog = max(self._peak_backlog, backlog)

    def fifo(self, depth: int, capacity: int) -> None:
        if (type(depth) is not int or type(capacity) is not int
            or capacity <= 0 or not 0 <= depth <= capacity):
            raise ValueError("invalid bounded ingress FIFO depth")
        self._roll()
        self._current["max_ingress_fifo_depth"] = max(
            self._current["max_ingress_fifo_depth"],depth,
        )
        self._current["ingress_fifo_observations"] += 1
        if depth * 10 >= capacity * 9:
            self._current["ingress_fifo_90pct_observations"] += 1

    @staticmethod
    def _new_micro_window() -> dict:
        return {
            "enqueued":0, "dequeued":0, "writer_completed":0,
            "overflow":0, "peak_fifo_depth":0,
            "near_full_observations":0, "max_writer_service_ms":0,
        }

    def _micro_roll(self) -> None:
        elapsed=max(0,self._clock()-self._t0)
        target=elapsed//(SUBSECOND_WINDOW_MS*1_000_000)
        if target<=self._micro_index:
            return
        while (self._micro_index<target and
               len(self._micro_windows)<MAX_SUBSECOND_WINDOWS):
            self._micro_close()
            self._micro_index+=1
            self._micro_current=self._new_micro_window()
        if self._micro_index<target:
            self._micro_truncated=True
            self._micro_index=target
            self._micro_current=self._new_micro_window()

    def _micro_close(self) -> None:
        if len(self._micro_windows)>=MAX_SUBSECOND_WINDOWS:
            self._micro_truncated=True
            return
        sample=dict(self._micro_current)
        sample["elapsed_start_ms"]=self._micro_index*SUBSECOND_WINDOW_MS
        self._micro_windows.append(sample)

    def fifo_event(self, action: str, depth: int, capacity: int,
                   duration_ms: float | None = None) -> None:
        """Only bounded aggregates; no market payload or source identity."""
        import math
        if (action not in ("enqueue","dequeue","overflow","writer_complete")
            or type(depth) is not int or type(capacity) is not int
            or capacity<=0 or not 0<=depth<=capacity):
            raise ValueError("invalid bounded FIFO event")
        if action=="writer_complete":
            if (type(duration_ms) not in (int,float)
                or not math.isfinite(duration_ms) or duration_ms<0):
                raise ValueError("invalid writer service duration")
        elif duration_ms is not None:
            raise ValueError("unexpected FIFO duration")
        self._micro_roll()
        field=({"enqueue":"enqueued","dequeue":"dequeued",
                "overflow":"overflow","writer_complete":"writer_completed"}[action])
        self._micro_total[field]+=1
        self._micro_current[field]+=1
        self._micro_current["peak_fifo_depth"]=max(
            self._micro_current["peak_fifo_depth"],depth)
        if depth*10>=capacity*9:
            self._micro_current["near_full_observations"]+=1
        if duration_ms is not None:
            self._micro_current["max_writer_service_ms"]=max(
                self._micro_current["max_writer_service_ms"],
                int(math.ceil(duration_ms)),
            )

    def receiver_ack(self, duration_ms: float) -> None:
        import math
        if (type(duration_ms) not in (int,float)
            or not math.isfinite(duration_ms) or duration_ms < 0):
            raise ValueError("invalid HMAC receiver round trip")
        self._roll()
        value=int(math.ceil(duration_ms))
        self._current["max_receiver_ack_ms"]=max(
            self._current["max_receiver_ack_ms"],value,
        )
        self._current["receiver_ack_observations"]+=1
        if duration_ms > 50:
            self._current["receiver_ack_over_50ms"]+=1
        if duration_ms > 250:
            self._current["receiver_ack_over_250ms"]+=1
        if duration_ms > 500:
            self._current["receiver_ack_over_500ms"]+=1

    def projected(self) -> None:
        self._incr("cursor_advanced")

    def published(self, admission_age_ms: int | None) -> None:
        self._incr("eventbus_published")
        if admission_age_ms is not None:
            if type(admission_age_ms) is not int or admission_age_ms < 0:
                raise ValueError("invalid admission age diagnostic")
            self._current["max_admission_age_ms"] = max(
                self._current["max_admission_age_ms"], admission_age_ms,
            )

    def checkpoint(self) -> None:
        self._incr("checkpoints")

    def summary(self, *, source_complete: bool) -> dict:
        if not self._windows and not any(self._current.values()):
            # Still expose a zero-observation window after a preflight failure.
            pass
        return {
            "schema":"econith-phase1-spool-pressure-v1",
            "diagnostic_only_not_source_evidence":True,
            "source_complete":bool(source_complete),
            "window_seconds":WINDOW_S,
            "max_windows":MAX_WINDOWS,
            "windows_truncated":self._truncated,
            "counters":dict(self._total),
            "peak_pending_originals":self._peak_backlog,
            "windows":list(self._windows),
            "subsecond_window_ms":SUBSECOND_WINDOW_MS,
            "max_subsecond_windows":MAX_SUBSECOND_WINDOWS,
            "subsecond_windows_truncated":self._micro_truncated,
            "subsecond_fifo_totals":dict(self._micro_total),
            "subsecond_windows":list(self._micro_windows),
            "host_cpu_field_scope":"process_CPU_time_not_system_utilization",
            "source_coverage_certified":False,
            "event_payloads_or_identifiers_recorded":False,
        }

    def write(self, *, source_complete: bool) -> None:
        # Save trailing partial window. No writes during the source session.
        self._roll()
        remaining = (self._clock() - self._t0) / 1_000_000_000
        span = max(0.001, remaining - self._current_index * WINDOW_S)
        self._close_window(span)
        self._micro_roll()
        if len(self._micro_windows)<MAX_SUBSECOND_WINDOWS:
            self._micro_close()
        else:
            self._micro_truncated=True
        with self.path.open("x", encoding="utf-8") as file:
            file.write(json.dumps(self.summary(source_complete=source_complete),
                                  sort_keys=True, indent=2, allow_nan=False) + "\n")
