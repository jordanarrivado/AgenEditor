"""Independent permit pools for teams, analysis, rendering, QA and each AI provider (spec §14).

Permits are process-local: one engine service owns a workspace (it holds an OS lock), so an in-memory
pool is authoritative. Leases in SQLite make the *work* durable; permits only bound concurrency.
"""

from __future__ import annotations

import os
import shutil
import threading
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

from ageneditor.contracts import PoolState, ProviderConfig, ResourceConfig, ResourceSnapshot, TaskType

#: Pool each task type needs besides a clip team (team tasks) and any provider permit.
TASK_POOLS: dict[TaskType, str] = {
    TaskType.ANALYZE_SOURCE: "analysis",
    TaskType.DISCOVER_CLIPS: "analysis",
    TaskType.TECHNICAL_QA: "analysis",
    TaskType.RENDER_PREVIEW: "render",
    TaskType.EXPORT: "render",
    TaskType.EDITORIAL_QA: "qa",
}


def _available_memory() -> int | None:
    if os.name == "nt":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
            return int(status.ullAvailPhys)
        return None
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        return None
    return None


class ResourceManager:
    def __init__(self, config: ResourceConfig, workspace: Path | None = None):
        self._lock = threading.Lock()
        self.workspace = workspace
        self.config = config
        self._capacity: dict[str, int] = {}
        self._holders: dict[str, set[str]] = {}
        self._calls: dict[str, deque[datetime]] = {}
        self._cooldown: dict[str, datetime] = {}
        self.configure(config)

    def configure(self, config: ResourceConfig, providers: list[ProviderConfig] | None = None) -> None:
        with self._lock:
            self.config = config
            self._capacity.update(
                {
                    "team": config.team_slots,
                    "analysis": config.analysis_slots,
                    "render": config.render_slots,
                    "qa": config.qa_slots,
                }
            )
            for provider in providers or []:
                self._capacity[f"provider:{provider.id}"] = provider.max_concurrency
            for pool in self._capacity:
                self._holders.setdefault(pool, set())

    def capacity(self, pool: str) -> int:
        return self._capacity.get(pool, 0)

    def in_use(self, pool: str) -> int:
        return len(self._holders.get(pool, ()))

    def try_acquire(self, pools: list[str], holder: str) -> bool:
        """All-or-nothing acquisition of several permits for one attempt."""
        with self._lock:
            for pool in pools:
                if pool not in self._capacity:
                    return False
                holders = self._holders[pool]
                if holder not in holders and len(holders) >= self._capacity[pool]:
                    return False
            for pool in pools:
                self._holders[pool].add(holder)
            return True

    def release(self, holder: str) -> None:
        with self._lock:
            for holders in self._holders.values():
                holders.discard(holder)

    # -------------------------------------------------------- provider rate limits

    def provider_allows(self, provider: ProviderConfig, now: datetime) -> tuple[bool, str]:
        with self._lock:
            until = self._cooldown.get(provider.id)
            if until is not None and until > now:
                return False, f"rate limited until {until.isoformat()}"
            calls = self._calls.setdefault(provider.id, deque())
            while calls and calls[0] <= now - timedelta(minutes=1):
                calls.popleft()
            if len(calls) >= provider.requests_per_minute:
                return False, "requests-per-minute limit reached"
            return True, ""

    def record_call(self, provider_id: str, now: datetime) -> None:
        with self._lock:
            self._calls.setdefault(provider_id, deque()).append(now)

    def cool_down(self, provider_id: str, until: datetime) -> None:
        with self._lock:
            self._cooldown[provider_id] = max(until, self._cooldown.get(provider_id, until))

    # -------------------------------------------------------- telemetry

    def snapshot(self) -> ResourceSnapshot:
        with self._lock:
            pools = {
                name: PoolState(capacity=self._capacity[name], in_use=len(holders), holders=sorted(holders))
                for name, holders in self._holders.items()
            }
        disk = None
        if self.workspace is not None:
            try:
                disk = shutil.disk_usage(self.workspace).free
            except OSError:
                disk = None
        from ageneditor.system import total_memory_bytes

        return ResourceSnapshot(
            pools=pools,
            cpu_count=os.cpu_count(),
            memory_total_bytes=total_memory_bytes(),
            memory_available_bytes=_available_memory(),
            disk_free_bytes=disk,
        )
