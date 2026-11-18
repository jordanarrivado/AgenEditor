"""Durable, dependency-aware scheduling primitives (spec §7, §13).

All state changes are SQLite transactions on the project database. The scheduler is deterministic: AI
proposes content, never task state transitions.

* readiness: a task is READY only when every dependency succeeded, the batch is running, the clip's
  requested assets are ready and (for export) a matching human approval exists;
* leases: expiring, transactional, with a monotonically increasing fencing token (per clip for timeline
  writers), so a stale worker can never commit;
* failures: transient errors retry with bounded exponential backoff and jitter; permanent ones fail;
* recovery: on restart every live attempt is abandoned and its task safely re-queued after reconciling
  work that was already committed.
"""

from __future__ import annotations

import random
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ageneditor.contracts import (
    AttemptState,
    BatchStatus,
    BlockReason,
    CheckpointDescriptor,
    EngineError,
    EngineException,
    ErrorCode,
    ModelUsage,
    Task,
    TaskAttempt,
    TaskStatus,
    TaskType,
)
from ageneditor.orchestration.capabilities import extend_tokens, issue_token, revoke_tokens
from ageneditor.production.common import new_id
from ageneditor.production.store import ProductionStore

TRANSIENT_ERRORS = frozenset(
    {
        ErrorCode.RENDER_FAILED,
        ErrorCode.TRANSCRIPTION_FAILED,
        ErrorCode.RATE_LIMITED,
        ErrorCode.PROVIDER_UNAVAILABLE,
        ErrorCode.TASK_TIMEOUT,
        ErrorCode.ADAPTER_FAILED,
        ErrorCode.LEASE_EXPIRED,
        ErrorCode.STALE_FENCE,
        ErrorCode.DOWNLOAD_FAILED,
        ErrorCode.REVISION_CONFLICT,
        ErrorCode.QA_FAILED,
    }
)
#: Errors a person must resolve (not retried, not a hard failure either).
HUMAN_ERRORS = frozenset(
    {
        ErrorCode.NO_CAPABLE_WORKER,
        ErrorCode.INSUFFICIENT_DISK,
        ErrorCode.SOURCE_INSUFFICIENT,
        ErrorCode.APPROVAL_REQUIRED,
        ErrorCode.HUMAN_DECISION_REQUIRED,
    }
)
#: Blocks that routing or team allocation lift at dispatch time without any task changing.
SOFT_BLOCKS = frozenset(
    {
        BlockReason.NO_CAPABLE_WORKER,
        BlockReason.BUDGET,
        BlockReason.POLICY,
        BlockReason.RATE_LIMITED,
        BlockReason.WAITING_FOR_TEAM,
    }
)
BACKOFF_BASE_S = 5
BACKOFF_CAP_S = 300
WAITING = (TaskStatus.CREATED, TaskStatus.BLOCKED, TaskStatus.RETRY_WAIT, TaskStatus.READY)


def is_transient(code: ErrorCode) -> bool:
    return code in TRANSIENT_ERRORS


def backoff_seconds(attempt: int, rng: random.Random) -> float:
    base = min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** max(0, attempt - 1))
    return base * rng.uniform(0.8, 1.2)


@dataclass
class Lease:
    task: Task
    attempt: TaskAttempt
    token: str


class Scheduler:
    def __init__(self, production: ProductionStore, rng: random.Random | None = None):
        self.production = production
        self.rng = rng or random.Random()  # noqa: S311 — jitter, not security

    @property
    def now(self) -> Callable[[], datetime]:
        return self.production.now

    # ------------------------------------------------------------ readiness

    def _blocked(self, db: sqlite3.Connection, task: Task) -> tuple[TaskStatus, BlockReason | None, str]:
        batch = self.production.batch(task.batch_id, db)
        if batch.status is BatchStatus.CANCELLED:
            return TaskStatus.CANCELLED, None, "batch was cancelled"
        if batch.paused:
            return TaskStatus.BLOCKED, BlockReason.PAUSED, "batch is paused"
        dependencies = self.production.dependencies(db, task.id)
        if any(dep.status is TaskStatus.CANCELLED for dep in dependencies):
            return TaskStatus.CANCELLED, None, "a dependency was cancelled"
        if any(dep.status is not TaskStatus.SUCCEEDED for dep in dependencies):
            return TaskStatus.BLOCKED, BlockReason.DEPENDENCIES, "waiting for earlier tasks"
        if task.clip_id is not None and (task.writer or task.type is TaskType.ASSEMBLE_DRAFT):
            clip = self.production.clip(task.clip_id, db)
            if clip.plan_id is not None:
                pending = self.production.pending_plan_assets(self.production.plan(clip.plan_id, db))
                if pending:
                    return (
                        TaskStatus.BLOCKED,
                        BlockReason.WAITING_FOR_ASSETS,
                        "waiting for: " + ", ".join(pending[:5]),
                    )
        if task.type is TaskType.EXPORT:
            approval = self.production.active_approval(str(task.clip_id), db)
            if approval is None or approval.id != task.params.get("approval_id"):
                return (
                    TaskStatus.BLOCKED,
                    BlockReason.WAITING_FOR_APPROVAL,
                    "approval is missing or was revoked",
                )
        if task.status is TaskStatus.RETRY_WAIT and task.not_before and task.not_before > self.now():
            return TaskStatus.RETRY_WAIT, None, "waiting to retry"
        return TaskStatus.READY, None, ""

    def refresh(self) -> list[Task]:
        """Re-evaluate every waiting task; return the tasks that changed status."""
        changed: list[Task] = []
        with self.production.transaction() as db:
            for task in self.production.tasks(statuses=list(WAITING), db=db):
                status, reason, message = self._blocked(db, task)
                # Routing/team blocks are lifted by the dispatcher itself, not by readiness checks.
                if (
                    status is TaskStatus.READY
                    and task.status is TaskStatus.BLOCKED
                    and task.block_reason in SOFT_BLOCKS
                ):
                    continue
                if status == task.status and reason == task.block_reason:
                    continue
                update: dict[str, Any] = {"status": status, "block_reason": reason}
                if status is TaskStatus.CANCELLED:
                    update["error"] = EngineError(code=ErrorCode.JOB_CANCELED, message=message)
                saved = self.production.save_task(
                    db, task.model_copy(update=update), "task_status", reason=message[:300]
                )
                changed.append(saved)
        return changed

    def block(self, task_id: str, reason: BlockReason, message: str) -> Task:
        with self.production.transaction() as db:
            task = self.production.task(task_id, db)
            if task.status not in WAITING:
                return task
            if task.status is TaskStatus.BLOCKED and task.block_reason is reason:
                return task
            return self.production.save_task(
                db,
                task.model_copy(update={"status": TaskStatus.BLOCKED, "block_reason": reason}),
                "task_status",
                reason=message[:300],
            )

    def ready_tasks(self) -> list[Task]:
        return self.production.tasks(statuses=[TaskStatus.READY])

    def effective_priority(self, task: Task, dependents: int = 0) -> float:
        """Higher runs first: explicit priority, deadline risk, age (anti-starvation), criticality."""
        batch = self.production.batch(task.batch_id)
        now = self.now()
        score = task.priority * 10.0
        age_minutes = max(0.0, (now - task.created_at).total_seconds() / 60)
        score += min(age_minutes, 300.0)
        if batch.policy.deadline is not None:
            hours_left = (batch.policy.deadline - now).total_seconds() / 3600
            score += 500.0 if hours_left <= 0 else max(0.0, 300.0 - hours_left * 10)
        score += dependents * 5.0
        # Finish clips that are close to review before starting new work (throughput of accepted clips).
        score += {
            TaskType.EXPORT: 40,
            TaskType.EDITORIAL_QA: 30,
            TaskType.TECHNICAL_QA: 30,
            TaskType.RENDER_PREVIEW: 20,
            TaskType.REVISION: 25,
        }.get(task.type, 0)
        return score

    # ------------------------------------------------------------ checkpoints

    def complete_from_checkpoint(self, task: Task, checkpoint: CheckpointDescriptor) -> Task:
        with self.production.transaction() as db:
            current = self.production.task(task.id, db)
            if current.status is not TaskStatus.READY:
                return current
            return self.production.save_task(
                db,
                current.model_copy(
                    update={
                        "status": TaskStatus.SUCCEEDED,
                        "result": {**checkpoint.result, "reused_checkpoint": checkpoint.id},
                        "checkpoint_id": checkpoint.id,
                        "input_fingerprint": checkpoint.input_fingerprint,
                        "block_reason": None,
                        "error": None,
                    }
                ),
                "task_reused_checkpoint",
                checkpoint_id=checkpoint.id,
            )

    # ------------------------------------------------------------ leases

    def lease(
        self,
        task_id: str,
        worker_id: str,
        adapter: str,
        lease_seconds: int,
        model: str | None = None,
        fingerprint: str | None = None,
        reserve_micros: int = 0,
    ) -> Lease:
        with self.production.transaction() as db:
            task = self.production.task(task_id, db)
            if task.status is not TaskStatus.READY:
                raise EngineException(ErrorCode.INVALID_STATE_TRANSITION, f"task is {task.status.value}")
            if task.writer:
                if task.clip_id is None:
                    raise EngineException(ErrorCode.INVALID_ARGUMENT, "writer task without a clip")
                if self.production.live_writer(db, task.clip_id) is not None:
                    raise EngineException(ErrorCode.REVISION_CONFLICT, "another writer holds this clip")
                db.execute("UPDATE clips SET writer_fence = writer_fence + 1 WHERE id=?", (task.clip_id,))
                fence = int(
                    db.execute("SELECT writer_fence FROM clips WHERE id=?", (task.clip_id,)).fetchone()[0]
                )
            else:
                fence = task.attempt_count + 1
            expires = self.now() + timedelta(seconds=lease_seconds)
            attempt = TaskAttempt(
                id=new_id("attempt"),
                task_id=task.id,
                worker_id=worker_id[:100],
                adapter=adapter,
                model=model,
                fencing_token=fence,
                lease_expires_at=expires,
                started_at=self.now(),
            )
            self.production.insert_attempt(db, attempt)
            token, _ = issue_token(self.production, db, task, attempt.id, expires)
            if reserve_micros:
                self.production.reserve_cost(db, attempt.id, task.batch_id, reserve_micros)
            task = self.production.save_task(
                db,
                task.model_copy(
                    update={
                        "status": TaskStatus.LEASED,
                        "attempt_count": task.attempt_count + 1,
                        "input_fingerprint": fingerprint,
                        "block_reason": None,
                    }
                ),
                "task_leased",
                attempt_id=attempt.id,
                worker_id=worker_id,
                adapter=adapter,
                model=model,
                fencing_token=fence,
            )
        return Lease(task=task, attempt=attempt, token=token)

    def mark_running(self, attempt_id: str) -> None:
        with self.production.transaction() as db:
            attempt = self.production.check_live_attempt(db, attempt_id)
            self.production.save_attempt(db, attempt.model_copy(update={"state": AttemptState.RUNNING}))
            task = self.production.task(attempt.task_id, db)
            self.production.save_task(
                db, task.model_copy(update={"status": TaskStatus.RUNNING}), "task_running"
            )

    def heartbeat(self, attempt_id: str, lease_seconds: int) -> bool:
        """Extend a live lease. Returns False when the attempt is no longer live."""
        with self.production.transaction() as db:
            try:
                attempt = self.production.check_live_attempt(db, attempt_id)
            except EngineException:
                return False
            expires = self.now() + timedelta(seconds=lease_seconds)
            self.production.save_attempt(db, attempt.model_copy(update={"lease_expires_at": expires}))
            extend_tokens(db, attempt_id, expires)
        return True

    # ------------------------------------------------------------ outcomes

    def complete(
        self,
        attempt_id: str,
        result: dict[str, Any],
        checkpoint: CheckpointDescriptor | None = None,
        usage: ModelUsage | None = None,
    ) -> Task:
        """Record a verified success. Refused when the lease is no longer held (stale worker)."""
        with self.production.transaction() as db:
            attempt = self.production.check_live_attempt(db, attempt_id)
            task = self.production.task(attempt.task_id, db)
            if task.writer:
                self.production.check_live_attempt(
                    db, attempt_id, attempt.fencing_token, writer_clip=task.clip_id
                )
            self.production.save_attempt(
                db, attempt.model_copy(update={"state": AttemptState.SUCCEEDED, "ended_at": self.now()})
            )
            revoke_tokens(db, attempt_id, self.now())
            if checkpoint is not None:
                self.production.save_checkpoint(db, checkpoint)
            if usage is not None:
                self.production.record_usage(db, attempt_id, task.batch_id, task.type.value, usage)
            self.production.settle_reservation(db, attempt_id, "reconciled" if usage else "released")
            return self.production.save_task(
                db,
                task.model_copy(
                    update={
                        "status": TaskStatus.SUCCEEDED,
                        "result": result,
                        "error": None,
                        "block_reason": None,
                        "checkpoint_id": checkpoint.id if checkpoint else None,
                    }
                ),
                "task_succeeded",
                attempt_id=attempt_id,
            )

    def fail(
        self,
        attempt_id: str,
        error: EngineError,
        usage: ModelUsage | None = None,
        retry_after_s: float | None = None,
        attempt_state: AttemptState = AttemptState.FAILED,
    ) -> Task:
        """Record a failed attempt and decide: retry later, ask a person, or fail the task."""
        with self.production.transaction() as db:
            attempt = self.production.attempt(attempt_id, db)
            task = self.production.task(attempt.task_id, db)
            if attempt.state in {AttemptState.LEASED, AttemptState.RUNNING}:
                self.production.save_attempt(
                    db,
                    attempt.model_copy(
                        update={"state": attempt_state, "ended_at": self.now(), "error": error}
                    ),
                )
            revoke_tokens(db, attempt_id, self.now())
            if usage is not None:
                self.production.record_usage(db, attempt_id, task.batch_id, task.type.value, usage)
            self.production.settle_reservation(db, attempt_id, "reconciled" if usage else "released")
            if task.status not in ACTIVE:
                return task  # already handled (e.g. cancelled while running)
            return self._after_failure(db, task, error, retry_after_s)

    def _after_failure(
        self, db: sqlite3.Connection, task: Task, error: EngineError, retry_after_s: float | None
    ) -> Task:
        if error.code is ErrorCode.JOB_CANCELED:
            status, extra = TaskStatus.CANCELLED, {}
        elif error.code in HUMAN_ERRORS:
            status, extra = TaskStatus.NEEDS_HUMAN, {}
        elif is_transient(error.code) and task.attempt_count < task.max_attempts:
            delay = (
                retry_after_s if retry_after_s is not None else backoff_seconds(task.attempt_count, self.rng)
            )
            status, extra = TaskStatus.RETRY_WAIT, {"not_before": self.now() + timedelta(seconds=delay)}
        else:
            status, extra = TaskStatus.FAILED, {}
        return self.production.save_task(
            db,
            task.model_copy(update={"status": status, "error": error, "block_reason": None, **extra}),
            "task_failed" if status is TaskStatus.FAILED else "task_status",
            code=error.code.value,
            message=error.message[:300],
        )

    def cancel_task(self, db: sqlite3.Connection, task: Task, reason: str) -> Task:
        if task.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            return task
        for attempt in self.production.attempts(task.id, live_only=True):
            self.production.save_attempt(
                db, attempt.model_copy(update={"state": AttemptState.CANCELLED, "ended_at": self.now()})
            )
            revoke_tokens(db, attempt.id, self.now())
            self.production.settle_reservation(db, attempt.id, "released")
        return self.production.save_task(
            db,
            task.model_copy(
                update={
                    "status": TaskStatus.CANCELLED,
                    "error": EngineError(code=ErrorCode.JOB_CANCELED, message=reason[:2000]),
                    "block_reason": None,
                }
            ),
            "task_cancelled",
            reason=reason[:300],
        )

    def reset_for_retry(self, task_id: str) -> Task:
        """A person retries a failed or needs-human task."""
        with self.production.transaction() as db:
            task = self.production.task(task_id, db)
            if task.status not in {TaskStatus.FAILED, TaskStatus.NEEDS_HUMAN}:
                raise EngineException(
                    ErrorCode.INVALID_STATE_TRANSITION, "only failed or waiting tasks can be retried"
                )
            return self.production.save_task(
                db,
                task.model_copy(
                    update={
                        "status": TaskStatus.CREATED,
                        "attempt_count": 0,
                        "error": None,
                        "not_before": None,
                        "block_reason": None,
                    }
                ),
                "task_retry_requested",
            )

    # ------------------------------------------------------------ expiry and recovery

    def reap_expired(self) -> list[TaskAttempt]:
        """Expire live attempts whose lease ran out; their tasks retry (a stale worker cannot commit)."""
        expired: list[TaskAttempt] = []
        for attempt in self.production.attempts(live_only=True):
            if attempt.lease_expires_at > self.now():
                continue
            error = EngineError(
                code=ErrorCode.LEASE_EXPIRED, message="worker lease expired before it reported"
            )
            self.fail(attempt.id, error, attempt_state=AttemptState.EXPIRED)
            expired.append(attempt)
        return expired

    def recover(self) -> dict[str, int]:
        """Reconcile after an engine restart. Called before any new lease is granted."""
        recovered = {"abandoned": 0, "reconciled": 0}
        for attempt in self.production.attempts(live_only=True):
            task = self.production.task(attempt.task_id)
            # Only single-commit engine handlers can be reconciled from a committed revision; an agent may
            # have committed part of its work, so its task is retried instead.
            committed = (
                self._committed_revision(attempt.id) if task.writer and attempt.adapter == "engine" else None
            )
            if committed is not None and task.status in ACTIVE:
                # Work was committed before the crash but never acknowledged: record it, do not redo it.
                with self.production.transaction() as db:
                    self.production.save_attempt(
                        db,
                        attempt.model_copy(update={"state": AttemptState.SUCCEEDED, "ended_at": self.now()}),
                    )
                    revoke_tokens(db, attempt.id, self.now())
                    self.production.settle_reservation(db, attempt.id, "released")
                    self.production.save_task(
                        db,
                        task.model_copy(
                            update={
                                "status": TaskStatus.SUCCEEDED,
                                "result": {"revision": committed, "reconciled_after_restart": True},
                                "error": None,
                            }
                        ),
                        "task_reconciled",
                        attempt_id=attempt.id,
                        revision=committed,
                    )
                recovered["reconciled"] += 1
                continue
            error = EngineError(code=ErrorCode.LEASE_EXPIRED, message="engine restarted while the task ran")
            self.fail(attempt.id, error, attempt_state=AttemptState.ABANDONED, retry_after_s=0)
            recovered["abandoned"] += 1
        # Fence every clip forward so nothing issued before the restart can ever commit.
        with self.production.transaction() as db:
            db.execute("UPDATE clips SET writer_fence = writer_fence + 1")
        return recovered

    def _committed_revision(self, attempt_id: str) -> int | None:
        with self.production.connect() as db:
            row = db.execute(
                "SELECT max(revision) FROM clip_timelines WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
        return None if row is None or row[0] is None else int(row[0])


ACTIVE = frozenset({TaskStatus.LEASED, TaskStatus.RUNNING})
