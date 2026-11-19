"""Spec §20 success indicators: metrics come only from engine records, with exact, unextrapolated values."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from production_helpers import Clock

from ageneditor.contracts import (
    AttemptState,
    Batch,
    CostKind,
    ModelUsage,
    Task,
    TaskAttempt,
    TaskStatus,
    TaskType,
)
from ageneditor.production.common import new_id
from ageneditor.production.metrics import batch_metrics, duration_stats
from ageneditor.production.store import ProductionStore
from ageneditor.storage import ProjectStore


def test_duration_stats_use_nearest_rank_and_stay_empty_without_samples() -> None:
    assert duration_stats([]).model_dump() == {
        "samples": 0,
        "mean_ms": None,
        "p50_ms": None,
        "p95_ms": None,
        "max_ms": None,
    }
    stats = duration_stats(range(1, 101))
    assert (stats.samples, stats.mean_ms, stats.p50_ms, stats.p95_ms, stats.max_ms) == (100, 50, 50, 95, 100)
    assert duration_stats([7]).p95_ms == 7


def test_batch_metrics_measure_queue_wait_runs_reuse_and_cost(tmp_path: Path) -> None:
    clock = Clock()
    production = ProductionStore(ProjectStore.create(tmp_path, "metrics"), clock=clock)
    batch = Batch(
        id=new_id("batch"),
        project_id=production.project_id,
        name="Metrics",
        sequence=1,
        policy={},
        context_hash="0" * 64,
        created_at=clock(),
    )

    def task(kind: TaskType) -> Task:
        item = Task(
            id=new_id("task"),
            project_id=production.project_id,
            batch_id=batch.id,
            type=kind,
            created_at=clock(),
            updated_at=clock(),
        )
        with production.transaction() as db:
            production.insert_task(db, item, item.id, [])
        return item

    def move(item: Task, status: TaskStatus, event: str) -> Task:
        with production.transaction() as db:
            done = {"result": {"ok": True}} if status is TaskStatus.SUCCEEDED else {}
            return production.save_task(db, item.model_copy(update={"status": status, **done}), event)

    with production.transaction() as db:
        production.insert_batch(db, batch)
    plan, review, revision, skipped = (
        task(TaskType.PLAN_CLIP),
        task(TaskType.EDITORIAL_QA),
        task(TaskType.REVISION),
        task(TaskType.PLAN_CLIP),
    )

    # plan: ready at t+1 s, leased at t+3 s (2 s wait), runs 10 s and succeeds.
    clock.advance(1)
    plan = move(plan, TaskStatus.READY, "task_status")
    clock.advance(2)
    plan = move(plan, TaskStatus.LEASED, "task_leased")
    attempt = TaskAttempt(
        id=new_id("attempt"),
        task_id=plan.id,
        worker_id="w",
        adapter="engine",
        fencing_token=1,
        lease_expires_at=clock(),
        started_at=clock(),
    )
    with production.transaction() as db:
        production.insert_attempt(db, attempt)
    clock.advance(10)
    with production.transaction() as db:
        production.save_attempt(
            db, attempt.model_copy(update={"state": AttemptState.SUCCEEDED, "ended_at": clock()})
        )
        production.record_usage(
            db,
            attempt.id,
            batch.id,
            "plan_clip",
            ModelUsage(provider_id="anthropic_api", model="m", cost_micros=1500, cost_kind=CostKind.MEASURED),
        )
        production.record_usage(
            db,
            attempt.id,
            batch.id,
            "plan_clip",
            ModelUsage(provider_id="anthropic_api", model="m", cost_micros=900, cost_kind=CostKind.ESTIMATED),
        )
        production.record_usage(
            db,
            attempt.id,
            batch.id,
            "plan_clip",
            ModelUsage(provider_id="claude_code", model="m", cost_micros=None, cost_kind=CostKind.UNKNOWN),
        )
    move(plan, TaskStatus.SUCCEEDED, "task_status")

    # review: ready twice (a retry); only the latest ready → lease gap (4 s) counts. Its attempt expires.
    review = move(review, TaskStatus.READY, "task_status")
    clock.advance(30)
    review = move(review, TaskStatus.RETRY_WAIT, "task_status")
    review = move(review, TaskStatus.READY, "task_status")
    clock.advance(4)
    review = move(review, TaskStatus.LEASED, "task_leased")
    expired = TaskAttempt(
        id=new_id("attempt"),
        task_id=review.id,
        worker_id="w",
        adapter="engine",
        fencing_token=2,
        lease_expires_at=clock(),
        started_at=clock(),
        state=AttemptState.EXPIRED,
    )
    with production.transaction() as db:
        production.insert_attempt(db, expired)
    move(review, TaskStatus.FAILED, "task_failed")

    # revision: completed from a checkpoint, never leased (no queue-wait sample).
    revision = move(revision, TaskStatus.READY, "task_status")
    move(revision, TaskStatus.SUCCEEDED, "task_reused_checkpoint")
    assert skipped.status is TaskStatus.CREATED  # never ready: contributes nothing

    metrics = batch_metrics(production, batch.id)
    assert metrics.queue_wait.samples == 2
    assert (metrics.queue_wait.p50_ms, metrics.queue_wait.max_ms, metrics.queue_wait.mean_ms) == (
        2000,
        4000,
        3000,
    )
    assert (metrics.attempt_run.samples, metrics.attempt_run.max_ms) == (
        1,
        10_000,
    )  # the expired one never ended
    assert (metrics.attempts_total, metrics.attempts_unsuccessful) == (2, 1)
    assert (metrics.tasks_total, metrics.tasks_succeeded, metrics.tasks_failed) == (4, 2, 1)
    assert (metrics.checkpoint_reuses, metrics.revision_tasks) == (1, 1)
    assert metrics.cost.model_dump() == {
        "measured_micros": 1500,
        "provider_reported_micros": 0,
        "estimated_micros": 900,
        "unpriced_requests": 1,
    }
    # No clips were produced, so nothing about review or approval is claimed.
    assert (metrics.clips_total, metrics.clips_approved, metrics.clips_delivered) == (0, 0, 0)
    assert metrics.first_review_ready_ms is None and metrics.last_approval_ms is None
    assert metrics.measured_at == clock()


def test_metrics_ignore_other_batches(tmp_path: Path) -> None:
    clock = Clock()
    production = ProductionStore(ProjectStore.create(tmp_path, "two"), clock=clock)
    batches = [
        Batch(
            id=new_id("batch"),
            project_id=production.project_id,
            name=f"B{n}",
            sequence=n,
            policy={},
            context_hash="0" * 64,
        )
        for n in (1, 2)
    ]
    with production.transaction() as db:
        for batch in batches:
            production.insert_batch(db, batch)
        other = Task(
            id=new_id("task"),
            project_id=production.project_id,
            batch_id=batches[1].id,
            type=TaskType.PLAN_CLIP,
        )
        production.insert_task(db, other, other.id, [])
        production.save_task(db, other.model_copy(update={"status": TaskStatus.READY}), "task_status")
        clock.advance(5)
        production.save_task(db, other.model_copy(update={"status": TaskStatus.LEASED}), "task_leased")
    assert batch_metrics(production, batches[0].id).queue_wait.samples == 0
    assert batch_metrics(production, batches[1].id).queue_wait.samples == 1


def test_cpu_busy_permille_from_tick_deltas() -> None:
    from ageneditor.orchestration.resource_manager import busy_permille

    assert busy_permille(None, (10, 100)) is None
    assert busy_permille((10, 100), (40, 200)) == 700  # 70 of 100 new ticks were busy
    assert busy_permille((10, 100), (10, 100)) is None  # no time passed
    assert busy_permille((50, 100), (40, 200)) is None  # counters went backwards


def test_pressure_summary_and_sample_retention(tmp_path: Path) -> None:
    from ageneditor.contracts import PoolState, ResourceSnapshot
    from ageneditor.production.metrics import pressure_summary
    from ageneditor.production.workspace import WorkspaceRegistry

    assert pressure_summary([]).model_dump()["samples"] == 0
    assert pressure_summary([]).cpu_busy_max_permille is None
    clock = Clock()

    def sample(cpu: int | None, available: int, render: int, minutes: float) -> ResourceSnapshot:
        return ResourceSnapshot(
            pools={"render": PoolState(capacity=1, in_use=render)},
            cpu_busy_permille=cpu,
            memory_total_bytes=1000,
            memory_available_bytes=available,
            disk_free_bytes=5000 - render,
            measured_at=clock.now + timedelta(minutes=minutes),
        )

    samples = [sample(None, 600, 0, 0), sample(300, 400, 1, 1), sample(900, 200, 1, 2)]
    pressure = pressure_summary(samples)
    assert (pressure.samples, pressure.cpu_busy_mean_permille, pressure.cpu_busy_max_permille) == (
        3,
        600,
        900,
    )
    assert (pressure.memory_used_mean_permille, pressure.memory_used_max_permille) == (600, 800)
    assert (pressure.memory_available_min_bytes, pressure.disk_free_min_bytes) == (200, 4999)
    assert pressure.pool_peak_in_use == {"render": 1} and pressure.pool_full_samples == {"render": 2}

    registry = WorkspaceRegistry(tmp_path)
    old = sample(100, 500, 0, -60 * 24 * 15)  # older than the 14-day retention
    registry.record_resource_sample(old)
    for item in samples:
        registry.record_resource_sample(item)
    window = registry.resource_samples(clock.now + timedelta(seconds=30), clock.now + timedelta(hours=1))
    assert [s.cpu_busy_permille for s in window] == [300, 900]
    assert registry.resource_samples(old.measured_at, clock.now) == [samples[0]]  # the old one was pruned


def test_orchestrator_records_samples_on_its_clock(tmp_path: Path) -> None:
    from production_helpers import build

    clock = Clock()
    orchestrator, _ = build(tmp_path, clock=clock)
    first = orchestrator.sample_resources()
    clock.advance(30)
    orchestrator.sample_resources()
    assert first.measured_at == clock.now - timedelta(seconds=30)
    recorded = orchestrator.registry.resource_samples(first.measured_at, clock.now)
    assert len(recorded) == 2 and set(recorded[0].pools) >= {"team", "render"}


def test_change_long_poll_wakes_on_new_state(tmp_path: Path) -> None:
    import threading
    import time

    from production_helpers import build

    from ageneditor.production.desktop_ops import wait_for_changes

    production = ProductionStore(ProjectStore.create(tmp_path, "poll"))
    orchestrator, _ = build(tmp_path)
    first = wait_for_changes(orchestrator, None, 5000)
    assert first["changed"] is False  # no cursor yet: answer at once
    assert wait_for_changes(orchestrator, first["cursor"], 0) == {"cursor": first["cursor"], "changed": False}

    def add_batch() -> None:
        time.sleep(0.3)
        with production.transaction() as db:
            production.insert_batch(
                db,
                Batch(
                    id=new_id("batch"),
                    project_id=production.project_id,
                    name="New",
                    sequence=1,
                    policy={},
                    context_hash="0" * 64,
                ),
            )

    threading.Thread(target=add_batch).start()
    started = time.monotonic()
    woke = wait_for_changes(orchestrator, first["cursor"], 10_000, poll_s=0.05)
    assert woke["changed"] is True and woke["cursor"] != first["cursor"]
    assert time.monotonic() - started < 5
