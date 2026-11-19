"""Durable production records in the project database (one authoritative store per project).

Every clip owns an independent, immutable timeline history in ``clip_timelines``. Commits are guarded by
optimistic revision checks and, for production workers, by the clip's writer fencing token, so a worker
whose lease expired or was reassigned can never commit (spec §9, §13).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from ageneditor.contracts import (
    Artifact,
    AttemptState,
    Author,
    Batch,
    BatchStatus,
    CandidateStatus,
    CheckpointDescriptor,
    ClipApproval,
    ClipCandidate,
    ClipPlan,
    ClipStatus,
    ContextSnapshot,
    DeliveryManifest,
    EngineException,
    ErrorCode,
    ExportArtifact,
    FindingState,
    ModelUsage,
    ProductionClip,
    ProductionEvent,
    Provenance,
    QAFinding,
    QAReview,
    RenderProfile,
    Task,
    TaskAttempt,
    TaskStatus,
    Timeline,
)
from ageneditor.production.common import content_hash, new_id, parse_ts, ts, utc_now
from ageneditor.storage import ProjectStore

Clock = Callable[[], datetime]

_WORK_STAGES = {
    ClipStatus.PLANNED,
    ClipStatus.WAITING_FOR_ASSETS,
    ClipStatus.READY,
    ClipStatus.EDITING,
    ClipStatus.PREVIEW_RENDERING,
    ClipStatus.TECHNICAL_QA,
    ClipStatus.EDITORIAL_QA,
    ClipStatus.NEEDS_REVISION,
    ClipStatus.REVIEW_READY,
    ClipStatus.HUMAN_APPROVED,
    ClipStatus.EXPORTED,
}
_INTERRUPTS = {ClipStatus.BLOCKED, ClipStatus.RETRY_WAIT, ClipStatus.NEEDS_HUMAN, ClipStatus.FAILED}
_FORWARD: dict[ClipStatus, set[ClipStatus]] = {
    ClipStatus.DISCOVERED: {ClipStatus.PLANNED},
    ClipStatus.PLANNED: {ClipStatus.WAITING_FOR_ASSETS, ClipStatus.READY},
    ClipStatus.WAITING_FOR_ASSETS: {ClipStatus.READY},
    ClipStatus.READY: {ClipStatus.EDITING, ClipStatus.WAITING_FOR_ASSETS},
    ClipStatus.EDITING: {ClipStatus.PREVIEW_RENDERING, ClipStatus.READY},
    ClipStatus.PREVIEW_RENDERING: {ClipStatus.TECHNICAL_QA},
    ClipStatus.TECHNICAL_QA: {ClipStatus.EDITORIAL_QA, ClipStatus.REVIEW_READY, ClipStatus.NEEDS_REVISION},
    ClipStatus.EDITORIAL_QA: {ClipStatus.REVIEW_READY, ClipStatus.NEEDS_REVISION},
    ClipStatus.NEEDS_REVISION: {ClipStatus.READY, ClipStatus.EDITING},
    ClipStatus.REVIEW_READY: {ClipStatus.HUMAN_APPROVED, ClipStatus.NEEDS_REVISION},
    ClipStatus.HUMAN_APPROVED: {ClipStatus.EXPORTED, ClipStatus.REVIEW_READY, ClipStatus.NEEDS_REVISION},
    ClipStatus.EXPORTED: {ClipStatus.DELIVERED, ClipStatus.REVIEW_READY, ClipStatus.NEEDS_REVISION},
    ClipStatus.DELIVERED: {ClipStatus.NEEDS_REVISION, ClipStatus.REVIEW_READY},
}


def clip_transition_allowed(current: ClipStatus, target: ClipStatus) -> bool:
    if current == target:
        return True
    if current is ClipStatus.CANCELLED:
        return False
    if target is ClipStatus.CANCELLED:
        return current is not ClipStatus.DELIVERED
    if target in _INTERRUPTS:
        return current is not ClipStatus.DELIVERED
    if current in _INTERRUPTS:
        # Resume where the work stands; a clip whose planning failed goes back to planning.
        return target in _WORK_STAGES or target is ClipStatus.DISCOVERED
    return target in _FORWARD.get(current, set())


def _row_model(model: type, row: sqlite3.Row | None, what: str, code: ErrorCode = ErrorCode.INVALID_ARGUMENT):
    if row is None:
        raise EngineException(code, f"{what} does not exist")
    return model.model_validate_json(row[0])


class ProductionStore:
    """Production records for one project. Thread-safe through SQLite transactions."""

    def __init__(self, project: ProjectStore, clock: Clock = utc_now):
        self.project = project
        self.clock = clock
        self.project_id = project.project_id

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with self.project._connect() as db:
            yield db

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.project._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    def now(self) -> datetime:
        return self.clock()

    # ------------------------------------------------------------ events

    def emit(
        self,
        db: sqlite3.Connection,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        **payload: Any,
    ) -> None:
        db.execute(
            "INSERT INTO production_events(event_type, aggregate_type, aggregate_id, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (event_type, aggregate_type, aggregate_id, json.dumps(payload, default=str), ts(self.now())),
        )

    def change_marker(self) -> str:
        """Cheap value that changes whenever production state does (for UI long-polling)."""
        with self.connect() as db:
            row = db.execute(
                "SELECT (SELECT coalesce(max(seq), 0) FROM production_events), "
                "(SELECT count(*) FROM batches), (SELECT count(*) FROM clip_candidates)"
            ).fetchone()
        return f"{row[0]}.{row[1]}.{row[2]}"

    def events(
        self, after_seq: int = 0, limit: int = 200, aggregate_id: str | None = None
    ) -> list[ProductionEvent]:
        query = "SELECT * FROM production_events WHERE seq > ?"
        params: list[Any] = [after_seq]
        if aggregate_id is not None:
            query += " AND aggregate_id = ?"
            params.append(aggregate_id)
        query += " ORDER BY seq LIMIT ?"
        params.append(max(1, min(limit, 1000)))
        with self.connect() as db:
            rows = db.execute(query, params).fetchall()
        return [
            ProductionEvent(
                seq=row["seq"],
                event_type=row["event_type"],
                aggregate_type=row["aggregate_type"],
                aggregate_id=row["aggregate_id"],
                payload=json.loads(row["payload"]),
                created_at=parse_ts(row["created_at"]),
            )
            for row in rows
        ]

    # ------------------------------------------------------------ context snapshots

    def save_snapshot(self, db: sqlite3.Connection, snapshot: ContextSnapshot) -> None:
        db.execute(
            "INSERT OR IGNORE INTO context_snapshots VALUES (?, ?, ?)",
            (snapshot.hash, snapshot.model_dump_json(), ts(self.now())),
        )

    def snapshot(self, snapshot_hash: str) -> ContextSnapshot:
        with self.connect() as db:
            row = db.execute("SELECT data FROM context_snapshots WHERE hash=?", (snapshot_hash,)).fetchone()
        return _row_model(ContextSnapshot, row, "context snapshot")

    # ------------------------------------------------------------ batches

    def insert_batch(self, db: sqlite3.Connection, batch: Batch) -> None:
        db.execute(
            "INSERT INTO batches VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                batch.id,
                batch.sequence,
                batch.status.value,
                int(batch.paused),
                batch.policy.priority,
                ts(batch.policy.deadline) if batch.policy.deadline else None,
                batch.model_dump_json(),
                ts(batch.created_at),
            ),
        )

    def next_batch_sequence(self, db: sqlite3.Connection) -> int:
        return int(db.execute("SELECT coalesce(max(sequence), 0) + 1 FROM batches").fetchone()[0])

    def batch(self, batch_id: str, db: sqlite3.Connection | None = None) -> Batch:
        if db is None:
            with self.connect() as conn:
                return self.batch(batch_id, conn)
        row = db.execute("SELECT data FROM batches WHERE id=?", (batch_id,)).fetchone()
        return _row_model(Batch, row, "batch")

    def batches(self) -> list[Batch]:
        with self.connect() as db:
            rows = db.execute("SELECT data FROM batches ORDER BY sequence DESC").fetchall()
        return [Batch.model_validate_json(row[0]) for row in rows]

    def has_batches(self) -> bool:
        with self.connect() as db:
            return db.execute("SELECT 1 FROM batches LIMIT 1").fetchone() is not None

    def save_batch(self, db: sqlite3.Connection, batch: Batch) -> None:
        db.execute(
            "UPDATE batches SET status=?, paused=?, priority=?, data=? WHERE id=?",
            (batch.status.value, int(batch.paused), batch.policy.priority, batch.model_dump_json(), batch.id),
        )

    def set_batch_status(self, db: sqlite3.Connection, batch: Batch, status: BatchStatus) -> Batch:
        if batch.status in {BatchStatus.CANCELLED, BatchStatus.COMPLETED} and status != batch.status:
            raise EngineException(
                ErrorCode.INVALID_STATE_TRANSITION, f"batch is already {batch.status.value}"
            )
        updated = batch.model_copy(update={"status": status})
        self.save_batch(db, updated)
        if status != batch.status:
            self.emit(db, "batch_status", "batch", batch.id, status=status.value)
        return updated

    # ------------------------------------------------------------ candidates

    def replace_candidates(
        self, db: sqlite3.Connection, batch_id: str, candidates: Sequence[ClipCandidate]
    ) -> None:
        if db.execute(
            "SELECT 1 FROM clip_candidates WHERE batch_id=? AND status=?",
            (batch_id, CandidateStatus.SELECTED.value),
        ).fetchone():
            raise EngineException(ErrorCode.INVALID_STATE_TRANSITION, "candidates were already selected")
        db.execute("DELETE FROM clip_candidates WHERE batch_id=?", (batch_id,))
        for candidate in candidates:
            db.execute(
                "INSERT INTO clip_candidates VALUES (?, ?, ?, ?, ?)",
                (candidate.id, batch_id, candidate.rank, candidate.status.value, candidate.model_dump_json()),
            )

    def candidates(self, batch_id: str, db: sqlite3.Connection | None = None) -> list[ClipCandidate]:
        if db is None:
            with self.connect() as conn:
                return self.candidates(batch_id, conn)
        rows = db.execute(
            "SELECT data FROM clip_candidates WHERE batch_id=? ORDER BY rank", (batch_id,)
        ).fetchall()
        return [ClipCandidate.model_validate_json(row[0]) for row in rows]

    def set_candidate_status(
        self, db: sqlite3.Connection, candidate: ClipCandidate, status: CandidateStatus
    ) -> None:
        updated = candidate.model_copy(update={"status": status})
        db.execute(
            "UPDATE clip_candidates SET status=?, data=? WHERE id=?",
            (status.value, updated.model_dump_json(), candidate.id),
        )

    # ------------------------------------------------------------ clips

    def insert_clip(self, db: sqlite3.Connection, clip: ProductionClip) -> None:
        db.execute(
            "INSERT INTO clips VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
            (
                clip.id,
                clip.batch_id,
                clip.candidate_id,
                clip.status.value,
                clip.current_revision,
                clip.model_dump_json(),
                ts(clip.updated_at),
            ),
        )
        self.emit(db, "clip_created", "clip", clip.id, batch_id=clip.batch_id, title=clip.title)

    def clip(self, clip_id: str, db: sqlite3.Connection | None = None) -> ProductionClip:
        if db is None:
            with self.connect() as conn:
                return self.clip(clip_id, conn)
        row = db.execute("SELECT data FROM clips WHERE id=?", (clip_id,)).fetchone()
        return _row_model(ProductionClip, row, "clip")

    def clips(
        self, batch_id: str | None = None, db: sqlite3.Connection | None = None
    ) -> list[ProductionClip]:
        if db is None:
            with self.connect() as conn:
                return self.clips(batch_id, conn)
        if batch_id is None:
            rows = db.execute("SELECT data FROM clips ORDER BY rowid").fetchall()
        else:
            rows = db.execute(
                "SELECT data FROM clips WHERE batch_id=? ORDER BY rowid", (batch_id,)
            ).fetchall()
        return [ProductionClip.model_validate_json(row[0]) for row in rows]

    def save_clip(self, db: sqlite3.Connection, clip: ProductionClip) -> ProductionClip:
        clip = clip.model_copy(update={"updated_at": self.now()})
        db.execute(
            "UPDATE clips SET status=?, current_revision=?, data=?, updated_at=? WHERE id=?",
            (clip.status.value, clip.current_revision, clip.model_dump_json(), ts(clip.updated_at), clip.id),
        )
        return clip

    def transition_clip(
        self, db: sqlite3.Connection, clip_id: str, target: ClipStatus, reason: str = ""
    ) -> ProductionClip:
        clip = self.clip(clip_id, db)
        if clip.status == target and clip.status_reason == reason[:500]:
            return clip
        if not clip_transition_allowed(clip.status, target):
            raise EngineException(
                ErrorCode.INVALID_STATE_TRANSITION,
                f"clip cannot move from {clip.status.value} to {target.value}",
            )
        updated = self.save_clip(
            db, clip.model_copy(update={"status": target, "status_reason": reason[:500]})
        )
        if clip.status != target:
            self.emit(
                db,
                "clip_status",
                "clip",
                clip_id,
                status=target.value,
                previous=clip.status.value,
                reason=reason[:300],
            )
        return updated

    # ------------------------------------------------------------ plans

    def save_plan(self, db: sqlite3.Connection, plan: ClipPlan) -> ClipPlan:
        db.execute(
            "INSERT INTO clip_plans VALUES (?, ?, ?, ?, ?)",
            (plan.id, plan.clip_id, plan.version, content_hash(plan), plan.model_dump_json()),
        )
        clip = self.clip(plan.clip_id, db)
        self.save_clip(db, clip.model_copy(update={"plan_id": plan.id}))
        self.emit(db, "plan_saved", "clip", plan.clip_id, plan_id=plan.id, version=plan.version)
        return plan

    def plan(self, plan_id: str, db: sqlite3.Connection | None = None) -> ClipPlan:
        if db is None:
            with self.connect() as conn:
                return self.plan(plan_id, conn)
        row = db.execute("SELECT data FROM clip_plans WHERE id=?", (plan_id,)).fetchone()
        return _row_model(ClipPlan, row, "clip plan")

    def next_plan_version(self, db: sqlite3.Connection, clip_id: str) -> int:
        row = db.execute("SELECT coalesce(max(version), 0) + 1 FROM clip_plans WHERE clip_id=?", (clip_id,))
        return int(row.fetchone()[0])

    def plan_hash(self, plan_id: str) -> str:
        with self.connect() as db:
            row = db.execute("SELECT hash FROM clip_plans WHERE id=?", (plan_id,)).fetchone()
        if row is None:
            raise EngineException(ErrorCode.INVALID_ARGUMENT, "clip plan does not exist")
        return str(row[0])

    def pending_plan_assets(self, plan: ClipPlan) -> list[str]:
        """Plan asset requests not yet ready in the project's external-generation checklist."""
        if not plan.asset_request_ids:
            return []
        checklist = self.project.asset_checklist()
        known = {request.id for request in checklist.requests}
        pending = []
        for request_id in plan.asset_request_ids:
            link = checklist.links.get(request_id)
            ready = request_id in known and (
                link == "dropped"
                or (link is not None and (not checklist.review_required or request_id in checklist.approved))
            )
            if not ready:
                pending.append(request_id)
        return pending

    # ------------------------------------------------------------ clip timelines

    def create_clip_timeline(self, clip_id: str, profile: RenderProfile, target_frames: int) -> Timeline:
        with self.transaction() as db:
            row = db.execute(
                "SELECT data FROM clip_timelines WHERE clip_id=? AND revision=0", (clip_id,)
            ).fetchone()
            if row is not None:
                return Timeline.model_validate_json(row[0])
            timeline = Timeline(
                project_id=self.project_id,
                revision=0,
                target_duration_frames=target_frames,
                render_profile=profile,
                provenance=Provenance(author=Author.SYSTEM, tool="production", summary="Empty clip timeline"),
            )
            db.execute(
                "INSERT INTO clip_timelines VALUES (?, 0, ?, NULL, ?)",
                (clip_id, timeline.model_dump_json(), ts(self.now())),
            )
            clip = self.clip(clip_id, db)
            self.save_clip(db, clip.model_copy(update={"current_revision": 0}))
        return timeline

    def clip_timeline(self, clip_id: str, revision: int | None = None) -> Timeline:
        with self.connect() as db:
            if revision is None:
                row = db.execute(
                    "SELECT data FROM clip_timelines WHERE clip_id=? ORDER BY revision DESC LIMIT 1",
                    (clip_id,),
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT data FROM clip_timelines WHERE clip_id=? AND revision=?", (clip_id, revision)
                ).fetchone()
        if row is None:
            raise EngineException(ErrorCode.INVALID_TIMELINE, "clip timeline revision does not exist")
        return Timeline.model_validate_json(row[0])

    def clip_revisions(self, clip_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT revision, attempt_id, created_at FROM clip_timelines WHERE clip_id=? "
                "ORDER BY revision",
                (clip_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def check_live_attempt(
        self,
        db: sqlite3.Connection,
        attempt_id: str,
        fencing_token: int | None = None,
        writer_clip: str | None = None,
    ) -> TaskAttempt:
        """Raise unless the attempt still holds an unexpired lease (and, for writers, the clip fence)."""
        row = db.execute("SELECT data FROM task_attempts WHERE id=?", (attempt_id,)).fetchone()
        attempt = _row_model(TaskAttempt, row, "task attempt")
        if attempt.state not in {AttemptState.LEASED, AttemptState.RUNNING}:
            raise EngineException(ErrorCode.LEASE_EXPIRED, f"task attempt is {attempt.state.value}")
        if attempt.lease_expires_at <= self.now():
            raise EngineException(ErrorCode.LEASE_EXPIRED, "task lease expired")
        if fencing_token is not None and fencing_token != attempt.fencing_token:
            raise EngineException(ErrorCode.STALE_FENCE, "fencing token does not match this attempt")
        if writer_clip is not None:
            fence = db.execute("SELECT writer_fence FROM clips WHERE id=?", (writer_clip,)).fetchone()
            if fence is None or int(fence[0]) != attempt.fencing_token:
                raise EngineException(ErrorCode.STALE_FENCE, "another writer holds this clip")
        return attempt

    def live_writer(self, db: sqlite3.Connection, clip_id: str) -> str | None:
        row = db.execute(
            "SELECT a.id FROM task_attempts a JOIN tasks t ON t.id = a.task_id "
            "WHERE t.clip_id=? AND a.state IN ('leased','running') AND json_extract(t.data, '$.writer') = 1 "
            "AND a.lease_expires_at > ?",
            (clip_id, ts(self.now())),
        ).fetchone()
        return None if row is None else str(row[0])

    def commit_clip_timeline(
        self,
        clip_id: str,
        candidate: Timeline,
        expected_revision: int,
        attempt_id: str | None = None,
        fencing_token: int | None = None,
    ) -> Timeline:
        """Commit a new immutable revision of one clip's timeline.

        Production workers pass their attempt and fencing token; a person (desktop) passes neither and is
        refused while a team holds the clip's writer lease.
        """
        candidate = Timeline.model_validate(candidate.model_dump(mode="json"))
        if candidate.project_id != self.project_id:
            raise EngineException(ErrorCode.PERMISSION_DENIED, "timeline belongs to another project")
        with self.transaction() as db:
            clip = self.clip(clip_id, db)
            if clip.status in {ClipStatus.CANCELLED, ClipStatus.DELIVERED}:
                raise EngineException(ErrorCode.INVALID_STATE_TRANSITION, f"clip is {clip.status.value}")
            if attempt_id is not None:
                attempt = self.check_live_attempt(db, attempt_id, fencing_token, writer_clip=clip_id)
                task = self.task(attempt.task_id, db)
                if task.clip_id != clip_id or not task.writer:
                    raise EngineException(ErrorCode.PERMISSION_DENIED, "this task may not edit this clip")
            elif self.live_writer(db, clip_id) is not None:
                raise EngineException(
                    ErrorCode.REVISION_CONFLICT,
                    "a production team is editing this clip; pause the batch first",
                )
            if clip.plan_id is not None:
                pending = self.pending_plan_assets(self.plan(clip.plan_id, db))
                if pending:
                    raise EngineException(
                        ErrorCode.INVALID_ARGUMENT,
                        "clip is waiting for requested assets: " + ", ".join(pending[:5]),
                    )
            current = clip.current_revision
            if current != expected_revision:
                raise EngineException(
                    ErrorCode.REVISION_CONFLICT, "clip revision changed", current_revision=current
                )
            issues = [
                *self.project.validation_issues(candidate),
                *self.project.removed_asset_issues(candidate),
            ]
            errors = [issue for issue in issues if issue.severity == "error"]
            if errors:
                detail = "; ".join(issue.message for issue in errors[:3])
                raise EngineException(
                    ErrorCode.INVALID_TIMELINE, f"timeline failed validation: {detail}"[:2000]
                )
            committed = candidate.model_copy(
                update={"revision": expected_revision + 1, "parent_revision": expected_revision}
            )
            db.execute(
                "INSERT INTO clip_timelines VALUES (?, ?, ?, ?, ?)",
                (clip_id, committed.revision, committed.model_dump_json(), attempt_id, ts(self.now())),
            )
            # A new revision invalidates any draft pin and approval of the older revision.
            self.save_clip(db, clip.model_copy(update={"current_revision": committed.revision}))
            self._revoke_approvals(db, clip_id, f"revision {committed.revision} replaced the approved one")
            self.emit(
                db, "clip_revision", "clip", clip_id, revision=committed.revision, attempt_id=attempt_id
            )
        self.project.write_artifact(
            f"production/clips/{clip_id}/timelines/r{committed.revision}.json",
            committed.model_dump_json(indent=2),
        )
        return committed

    # ------------------------------------------------------------ tasks

    def insert_task(
        self, db: sqlite3.Connection, task: Task, graph_key: str, depends_on: Sequence[str]
    ) -> str:
        row = db.execute("SELECT id FROM tasks WHERE graph_key=?", (graph_key,)).fetchone()
        if row is not None:
            return str(row[0])  # idempotent graph creation
        db.execute(
            "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task.id,
                task.batch_id,
                task.clip_id,
                graph_key,
                task.type.value,
                task.cycle,
                task.status.value,
                task.priority,
                ts(task.not_before) if task.not_before else None,
                task.model_dump_json(),
                ts(task.created_at),
            ),
        )
        for dependency in depends_on:
            db.execute("INSERT INTO task_dependencies VALUES (?, ?)", (task.id, dependency))
        self.emit(db, "task_created", "task", task.id, type=task.type.value, clip_id=task.clip_id)
        return task.id

    def task(self, task_id: str, db: sqlite3.Connection | None = None) -> Task:
        if db is None:
            with self.connect() as conn:
                return self.task(task_id, conn)
        row = db.execute("SELECT data FROM tasks WHERE id=?", (task_id,)).fetchone()
        return _row_model(Task, row, "task")

    def tasks(
        self,
        batch_id: str | None = None,
        clip_id: str | None = None,
        statuses: Sequence[TaskStatus] | None = None,
        db: sqlite3.Connection | None = None,
    ) -> list[Task]:
        if db is None:
            with self.connect() as conn:
                return self.tasks(batch_id, clip_id, statuses, conn)
        query, params = "SELECT data FROM tasks WHERE 1=1", []
        if batch_id is not None:
            query += " AND batch_id=?"
            params.append(batch_id)
        if clip_id is not None:
            query += " AND clip_id=?"
            params.append(clip_id)
        if statuses:
            query += f" AND status IN ({','.join('?' * len(statuses))})"
            params.extend(status.value for status in statuses)
        rows = db.execute(query + " ORDER BY created_at, rowid", params).fetchall()
        return [Task.model_validate_json(row[0]) for row in rows]

    def save_task(self, db: sqlite3.Connection, task: Task, event: str | None = None, **payload: Any) -> Task:
        task = Task.model_validate({**task.model_dump(mode="json"), "updated_at": self.now()})
        db.execute(
            "UPDATE tasks SET status=?, priority=?, not_before=?, data=? WHERE id=?",
            (
                task.status.value,
                task.priority,
                ts(task.not_before) if task.not_before else None,
                task.model_dump_json(),
                task.id,
            ),
        )
        if event:
            self.emit(db, event, "task", task.id, status=task.status.value, type=task.type.value, **payload)
        return task

    def dependencies(self, db: sqlite3.Connection, task_id: str) -> list[Task]:
        rows = db.execute(
            "SELECT t.data FROM task_dependencies d JOIN tasks t ON t.id = d.depends_on WHERE d.task_id=?",
            (task_id,),
        ).fetchall()
        return [Task.model_validate_json(row[0]) for row in rows]

    def dependents(self, db: sqlite3.Connection, task_id: str) -> list[Task]:
        rows = db.execute(
            "SELECT t.data FROM task_dependencies d JOIN tasks t ON t.id = d.task_id WHERE d.depends_on=?",
            (task_id,),
        ).fetchall()
        return [Task.model_validate_json(row[0]) for row in rows]

    # ------------------------------------------------------------ attempts

    def insert_attempt(self, db: sqlite3.Connection, attempt: TaskAttempt) -> None:
        db.execute(
            "INSERT INTO task_attempts VALUES (?, ?, ?, ?, ?, ?)",
            (
                attempt.id,
                attempt.task_id,
                attempt.state.value,
                attempt.fencing_token,
                ts(attempt.lease_expires_at),
                attempt.model_dump_json(),
            ),
        )

    def attempt(self, attempt_id: str, db: sqlite3.Connection | None = None) -> TaskAttempt:
        if db is None:
            with self.connect() as conn:
                return self.attempt(attempt_id, conn)
        row = db.execute("SELECT data FROM task_attempts WHERE id=?", (attempt_id,)).fetchone()
        return _row_model(TaskAttempt, row, "task attempt")

    def attempts(self, task_id: str | None = None, live_only: bool = False) -> list[TaskAttempt]:
        query, params = "SELECT data FROM task_attempts WHERE 1=1", []
        if task_id is not None:
            query += " AND task_id=?"
            params.append(task_id)
        if live_only:
            query += " AND state IN ('leased','running')"
        with self.connect() as db:
            rows = db.execute(query + " ORDER BY rowid", params).fetchall()
        return [TaskAttempt.model_validate_json(row[0]) for row in rows]

    def save_attempt(self, db: sqlite3.Connection, attempt: TaskAttempt) -> None:
        db.execute(
            "UPDATE task_attempts SET state=?, lease_expires_at=?, data=? WHERE id=?",
            (attempt.state.value, ts(attempt.lease_expires_at), attempt.model_dump_json(), attempt.id),
        )

    # ------------------------------------------------------------ artifacts and checkpoints

    def save_artifact(self, db: sqlite3.Connection, artifact: Artifact) -> Artifact:
        db.execute(
            "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?)",
            (artifact.id, artifact.clip_id, artifact.kind.value, artifact.sha256, artifact.model_dump_json()),
        )
        return artifact

    def artifact(self, artifact_id: str) -> Artifact:
        with self.connect() as db:
            row = db.execute("SELECT data FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        return _row_model(Artifact, row, "artifact")

    def find_checkpoint(
        self, task_type: str, scope_key: str, fingerprint: str
    ) -> CheckpointDescriptor | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT data FROM checkpoints WHERE task_type=? AND scope_key=? AND input_fingerprint=? "
                "AND valid=1 ORDER BY rowid DESC LIMIT 1",
                (task_type, scope_key, fingerprint),
            ).fetchone()
        return None if row is None else CheckpointDescriptor.model_validate_json(row[0])

    def save_checkpoint(self, db: sqlite3.Connection, checkpoint: CheckpointDescriptor) -> None:
        db.execute(
            "INSERT INTO checkpoints VALUES (?, ?, ?, ?, ?, ?)",
            (
                checkpoint.id,
                checkpoint.task_type.value,
                checkpoint.scope_key,
                checkpoint.input_fingerprint,
                int(checkpoint.valid),
                checkpoint.model_dump_json(),
            ),
        )

    def invalidate_checkpoint(self, checkpoint_id: str, reason: str) -> None:
        with self.transaction() as db:
            row = db.execute("SELECT data FROM checkpoints WHERE id=?", (checkpoint_id,)).fetchone()
            if row is None:
                return
            checkpoint = CheckpointDescriptor.model_validate_json(row[0]).model_copy(update={"valid": False})
            db.execute(
                "UPDATE checkpoints SET valid=0, data=? WHERE id=?",
                (checkpoint.model_dump_json(), checkpoint_id),
            )
            self.emit(db, "checkpoint_invalidated", "task", checkpoint.task_id, reason=reason[:300])

    # ------------------------------------------------------------ QA reviews and findings

    def save_review(self, db: sqlite3.Connection, review: QAReview, insert: bool = False) -> QAReview:
        if insert:
            db.execute(
                "INSERT INTO qa_reviews VALUES (?, ?, ?, ?)",
                (review.id, review.clip_id, review.status.value, review.model_dump_json()),
            )
        else:
            db.execute(
                "UPDATE qa_reviews SET status=?, data=? WHERE id=?",
                (review.status.value, review.model_dump_json(), review.id),
            )
        return review

    def review(self, review_id: str, db: sqlite3.Connection | None = None) -> QAReview:
        if db is None:
            with self.connect() as conn:
                return self.review(review_id, conn)
        row = db.execute("SELECT data FROM qa_reviews WHERE id=?", (review_id,)).fetchone()
        return _row_model(QAReview, row, "QA review")

    def reviews(self, clip_id: str) -> list[QAReview]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT data FROM qa_reviews WHERE clip_id=? ORDER BY rowid", (clip_id,)
            ).fetchall()
        return [QAReview.model_validate_json(row[0]) for row in rows]

    def save_finding(self, db: sqlite3.Connection, finding: QAFinding, insert: bool = False) -> QAFinding:
        if insert:
            db.execute(
                "INSERT INTO qa_findings VALUES (?, ?, ?, ?, ?)",
                (
                    finding.id,
                    finding.review_id,
                    finding.clip_id,
                    finding.state.value,
                    finding.model_dump_json(),
                ),
            )
        else:
            db.execute(
                "UPDATE qa_findings SET state=?, data=? WHERE id=?",
                (finding.state.value, finding.model_dump_json(), finding.id),
            )
        return finding

    def finding(self, finding_id: str, db: sqlite3.Connection | None = None) -> QAFinding:
        if db is None:
            with self.connect() as conn:
                return self.finding(finding_id, conn)
        row = db.execute("SELECT data FROM qa_findings WHERE id=?", (finding_id,)).fetchone()
        return _row_model(QAFinding, row, "QA finding")

    def findings(
        self, clip_id: str, states: Sequence[FindingState] | None = None, db: sqlite3.Connection | None = None
    ) -> list[QAFinding]:
        if db is None:
            with self.connect() as conn:
                return self.findings(clip_id, states, conn)
        query, params = "SELECT data FROM qa_findings WHERE clip_id=?", [clip_id]
        if states:
            query += f" AND state IN ({','.join('?' * len(states))})"
            params.extend(state.value for state in states)
        rows = db.execute(query + " ORDER BY rowid", params).fetchall()
        return [QAFinding.model_validate_json(row[0]) for row in rows]

    # ------------------------------------------------------------ approvals, exports, deliveries

    def active_approval(self, clip_id: str, db: sqlite3.Connection | None = None) -> ClipApproval | None:
        if db is None:
            with self.connect() as conn:
                return self.active_approval(clip_id, conn)
        row = db.execute("SELECT data FROM approvals WHERE clip_id=? AND revoked=0", (clip_id,)).fetchone()
        return None if row is None else ClipApproval.model_validate_json(row[0])

    def insert_approval(self, db: sqlite3.Connection, approval: ClipApproval) -> None:
        self._revoke_approvals(db, approval.clip_id, "superseded by a new approval")
        db.execute(
            "INSERT INTO approvals VALUES (?, ?, 0, ?)",
            (approval.id, approval.clip_id, approval.model_dump_json()),
        )
        self.emit(
            db,
            "clip_approved",
            "approval",
            approval.id,
            clip_id=approval.clip_id,
            revision=approval.timeline_revision,
            approved_by=approval.approved_by,
        )

    def _revoke_approvals(self, db: sqlite3.Connection, clip_id: str, reason: str) -> None:
        rows = db.execute("SELECT data FROM approvals WHERE clip_id=? AND revoked=0", (clip_id,)).fetchall()
        for row in rows:
            approval = ClipApproval.model_validate_json(row[0]).model_copy(
                update={"revoked_at": self.now(), "revoked_reason": reason[:300]}
            )
            db.execute(
                "UPDATE approvals SET revoked=1, data=? WHERE id=?", (approval.model_dump_json(), approval.id)
            )
            self.emit(db, "approval_revoked", "approval", approval.id, clip_id=clip_id, reason=reason[:300])

    def revoke_approval(self, db: sqlite3.Connection, clip_id: str, reason: str) -> None:
        self._revoke_approvals(db, clip_id, reason)

    def approvals(self, clip_id: str) -> list[ClipApproval]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT data FROM approvals WHERE clip_id=? ORDER BY rowid", (clip_id,)
            ).fetchall()
        return [ClipApproval.model_validate_json(row[0]) for row in rows]

    def insert_export(self, db: sqlite3.Connection, export: ExportArtifact) -> None:
        db.execute(
            "INSERT INTO exports VALUES (?, ?, ?, ?)",
            (export.id, export.clip_id, export.approval_id, export.model_dump_json()),
        )
        self.emit(
            db, "clip_exported", "clip", export.clip_id, export_id=export.id, approval_id=export.approval_id
        )

    def export_for(self, clip_id: str, approval_id: str) -> ExportArtifact | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT data FROM exports WHERE clip_id=? AND approval_id=? ORDER BY rowid DESC LIMIT 1",
                (clip_id, approval_id),
            ).fetchone()
        return None if row is None else ExportArtifact.model_validate_json(row[0])

    def insert_manifest(self, db: sqlite3.Connection, manifest: DeliveryManifest, sha256: str) -> None:
        db.execute(
            "INSERT INTO delivery_manifests VALUES (?, ?, ?, ?)",
            (manifest.id, manifest.batch_id, sha256, manifest.model_dump_json()),
        )
        self.emit(
            db,
            "delivery_packaged",
            "delivery",
            manifest.id,
            batch_id=manifest.batch_id,
            clips=[clip.clip_id for clip in manifest.clips],
        )

    def manifests(self, batch_id: str) -> list[DeliveryManifest]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT data FROM delivery_manifests WHERE batch_id=? ORDER BY rowid", (batch_id,)
            ).fetchall()
        return [DeliveryManifest.model_validate_json(row[0]) for row in rows]

    # ------------------------------------------------------------ cost and usage

    def record_usage(
        self, db: sqlite3.Connection, attempt_id: str, batch_id: str, task_type: str, usage: ModelUsage
    ) -> None:
        db.execute(
            "INSERT INTO model_usage VALUES (?, ?, ?, ?, ?, ?)",
            (new_id("usage"), attempt_id, batch_id, task_type, usage.model_dump_json(), ts(self.now())),
        )

    def usage(self, batch_id: str | None = None) -> list[dict[str, Any]]:
        query, params = "SELECT attempt_id, batch_id, task_type, data, created_at FROM model_usage", []
        if batch_id is not None:
            query += " WHERE batch_id=?"
            params.append(batch_id)
        with self.connect() as db:
            rows = db.execute(query + " ORDER BY rowid", params).fetchall()
        return [
            {
                **json.loads(row["data"]),
                "attempt_id": row["attempt_id"],
                "batch_id": row["batch_id"],
                "task_type": row["task_type"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def committed_spend(self, db: sqlite3.Connection, batch_id: str) -> int:
        """Reconciled usage plus live reservations, in micro-USD."""
        spent = 0
        for row in db.execute("SELECT data FROM model_usage WHERE batch_id=?", (batch_id,)):
            spent += json.loads(row[0]).get("cost_micros") or 0
        reserved = db.execute(
            "SELECT coalesce(sum(amount_micros), 0) FROM cost_reservations "
            "WHERE batch_id=? AND state='reserved'",
            (batch_id,),
        ).fetchone()[0]
        return int(spent) + int(reserved)

    def reserve_cost(self, db: sqlite3.Connection, attempt_id: str, batch_id: str, amount: int) -> None:
        db.execute(
            "INSERT INTO cost_reservations VALUES (?, ?, ?, ?, 'reserved')",
            (new_id("reserve"), attempt_id, batch_id, amount),
        )

    def settle_reservation(self, db: sqlite3.Connection, attempt_id: str, state: str) -> None:
        db.execute(
            "UPDATE cost_reservations SET state=? WHERE attempt_id=? AND state='reserved'",
            (state, attempt_id),
        )
