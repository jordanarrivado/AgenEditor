"""Measured production metrics for one batch (spec §20 success indicators).

Everything is derived from engine records: the event log, attempts, approvals, manifests and model
usage. A metric the engine did not observe stays empty; nothing is extrapolated or estimated here.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta

from ageneditor.contracts import (
    AttemptState,
    ClipStatus,
    CostByKind,
    DurationStats,
    ProductionMetrics,
    ResourcePressure,
    ResourceSnapshot,
    TaskStatus,
    TaskType,
)

from .common import parse_ts
from .store import ProductionStore

#: Clip states that mean the clip reached a person's review at some point.
_REVIEWED = {
    ClipStatus.REVIEW_READY,
    ClipStatus.HUMAN_APPROVED,
    ClipStatus.EXPORTED,
    ClipStatus.DELIVERED,
}
_UNSUCCESSFUL = {AttemptState.FAILED, AttemptState.EXPIRED, AttemptState.ABANDONED, AttemptState.CANCELLED}


def _ms(start: datetime, end: datetime) -> int:
    return max(0, (end - start) // timedelta(milliseconds=1))


def duration_stats(samples: Iterable[int]) -> DurationStats:
    """Mean and nearest-rank percentiles of integer millisecond samples."""
    values = sorted(samples)
    if not values:
        return DurationStats(samples=0)

    def rank(p: int) -> int:
        # Nearest-rank: the smallest value with at least p% of samples at or below it.
        return values[max(0, -(-p * len(values) // 100) - 1)]

    return DurationStats(
        samples=len(values),
        mean_ms=sum(values) // len(values),
        p50_ms=rank(50),
        p95_ms=rank(95),
        max_ms=values[-1],
    )


def pressure_summary(samples: Sequence[ResourceSnapshot]) -> ResourcePressure:
    """Summarize machine-pressure samples. Fields stay empty when no sample measured them."""
    cpu = [s.cpu_busy_permille for s in samples if s.cpu_busy_permille is not None]
    memory = [
        (s.memory_total_bytes - s.memory_available_bytes) * 1000 // s.memory_total_bytes
        for s in samples
        if s.memory_total_bytes and s.memory_available_bytes is not None
    ]
    available = [s.memory_available_bytes for s in samples if s.memory_available_bytes is not None]
    disk = [s.disk_free_bytes for s in samples if s.disk_free_bytes is not None]
    peaks: dict[str, int] = {}
    full: dict[str, int] = {}
    for sample in samples:
        for name, pool in sample.pools.items():
            peaks[name] = max(peaks.get(name, 0), pool.in_use)
            full[name] = full.get(name, 0) + (1 if pool.capacity and pool.in_use >= pool.capacity else 0)
    return ResourcePressure(
        samples=len(samples),
        cpu_busy_mean_permille=sum(cpu) // len(cpu) if cpu else None,
        cpu_busy_max_permille=max(cpu, default=None),
        memory_used_mean_permille=max(0, sum(memory) // len(memory)) if memory else None,
        memory_used_max_permille=max(memory, default=None),
        memory_available_min_bytes=min(available, default=None),
        disk_free_min_bytes=min(disk, default=None),
        pool_peak_in_use=peaks,
        pool_full_samples=full,
    )


def batch_metrics(
    production: ProductionStore, batch_id: str, samples: Sequence[ResourceSnapshot] = ()
) -> ProductionMetrics:
    """Metrics for one batch; ``samples`` are the machine-pressure samples taken during it."""
    batch = production.batch(batch_id)
    now = production.now()
    clips = production.clips(batch_id)
    clip_ids = {clip.id for clip in clips}
    tasks = production.tasks(batch_id=batch_id)
    task_ids = {task.id for task in tasks}

    ready_at: dict[str, datetime] = {}
    waits: list[int] = []
    reuses = 0
    first_review: datetime | None = None
    reviewed: set[str] = {clip.id for clip in clips if clip.status in _REVIEWED}
    with production.connect() as db:
        rows = db.execute(
            "SELECT event_type, aggregate_type, aggregate_id, payload, created_at FROM production_events "
            "WHERE aggregate_type IN ('task', 'clip') ORDER BY seq"
        ).fetchall()

    for row in rows:
        kind, aggregate, subject = row["event_type"], row["aggregate_type"], row["aggregate_id"]
        at = parse_ts(row["created_at"])
        if aggregate == "task" and subject in task_ids:
            payload = json.loads(row["payload"])
            if kind == "task_status" and payload.get("status") == TaskStatus.READY.value:
                ready_at[subject] = at
            elif kind == "task_leased" and subject in ready_at:
                waits.append(_ms(ready_at.pop(subject), at))
            elif kind == "task_reused_checkpoint":
                reuses += 1
        elif aggregate == "clip" and subject in clip_ids and kind == "clip_status":
            status = json.loads(row["payload"]).get("status")
            if status == ClipStatus.REVIEW_READY.value:
                reviewed.add(subject)
                if first_review is None:
                    first_review = at

    runs: list[int] = []
    attempts_total = unsuccessful = 0
    for task in tasks:
        for attempt in production.attempts(task.id):
            attempts_total += 1
            if attempt.state in _UNSUCCESSFUL:
                unsuccessful += 1
            if attempt.ended_at is not None:
                runs.append(_ms(attempt.started_at, attempt.ended_at))

    approvals = [a for a in (production.active_approval(clip.id) for clip in clips) if a is not None]
    last_approval = max((a.approved_at for a in approvals), default=None)
    delivered = {item.clip_id for manifest in production.manifests(batch_id) for item in manifest.clips}

    spend = {"measured": 0, "provider_reported": 0, "estimated": 0}
    unpriced = 0
    for usage in production.usage(batch_id):
        micros, kind = usage.get("cost_micros"), usage.get("cost_kind", "unknown")
        if micros is None or kind not in spend:
            unpriced += 1
        else:
            spend[kind] += int(micros)
    cost = CostByKind(
        measured_micros=spend["measured"],
        provider_reported_micros=spend["provider_reported"],
        estimated_micros=spend["estimated"],
        unpriced_requests=unpriced,
    )

    return ProductionMetrics(
        batch_id=batch_id,
        measured_at=now,
        batch_created_at=batch.created_at,
        clips_total=len(clips),
        clips_reached_review=len(reviewed),
        clips_approved=len(approvals),
        clips_delivered=len(delivered & clip_ids),
        first_review_ready_ms=_ms(batch.created_at, first_review) if first_review else None,
        last_approval_ms=_ms(batch.created_at, last_approval) if last_approval else None,
        queue_wait=duration_stats(waits),
        attempt_run=duration_stats(runs),
        attempts_total=attempts_total,
        attempts_unsuccessful=unsuccessful,
        tasks_total=len(tasks),
        tasks_succeeded=sum(task.status is TaskStatus.SUCCEEDED for task in tasks),
        tasks_failed=sum(task.status is TaskStatus.FAILED for task in tasks),
        checkpoint_reuses=reuses,
        revision_tasks=sum(task.type is TaskType.REVISION for task in tasks),
        cost=cost,
        pressure=pressure_summary(samples),
    )
