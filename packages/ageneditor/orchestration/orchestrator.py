"""Master Production Orchestrator (spec §3, §7, §9).

One orchestrator per engine service (workspace). Each ``tick``:

1. reaps expired leases and polls running work (heartbeating live leases, collecting finished results);
2. verifies every finished result against engine state before a task may succeed;
3. re-evaluates task readiness (dependencies, assets, approvals, pause);
4. dispatches ready tasks in priority order, subject to routing, team allocation, resource permits,
   provider rate limits and budget reservations.

The orchestrator owns scheduling and state. Agents (through adapters) only do the scoped work.
"""

from __future__ import annotations

import random
import sqlite3
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ageneditor.contracts import (
    AgentResult,
    BatchStatus,
    BlockReason,
    CheckStatus,
    ClipStatus,
    EngineError,
    EngineException,
    ErrorCode,
    QAReviewStatus,
    ResourceSnapshot,
    RouteKind,
    RunState,
    ScopedTask,
    SelectionMode,
    Task,
    TaskStatus,
    TaskType,
)
from ageneditor.orchestration.agent_runtime import AgentRuntimeAdapter, EngineToolAdapter, RunHandle
from ageneditor.orchestration.capabilities import ROLE_TOOLS, role_for
from ageneditor.orchestration.checkpoint_service import (
    REUSABLE,
    fingerprint,
    make_checkpoint,
    reusable_checkpoint,
)
from ageneditor.orchestration.handlers import build_handlers
from ageneditor.orchestration.instructions import task_instructions
from ageneditor.orchestration.model_router import SpendState, route
from ageneditor.orchestration.resource_manager import TASK_POOLS, ResourceManager
from ageneditor.orchestration.scheduler import SOFT_BLOCKS, Scheduler
from ageneditor.orchestration.task_graph import materialize, production_graph
from ageneditor.orchestration.team_manager import TeamManager
from ageneditor.production.common import utc_now
from ageneditor.production.store import ProductionStore
from ageneditor.production.workspace import WorkspaceRegistry
from ageneditor.storage import ProjectStore

#: Tasks that need the clip's production team.
TEAM_TASKS = frozenset(
    {
        TaskType.PLAN_CLIP,
        TaskType.STRUCTURAL_EDIT,
        TaskType.CAPTIONS,
        TaskType.VISUAL_POLISH,
        TaskType.AUDIO_POLISH,
        TaskType.ENHANCEMENT,
        TaskType.REVISION,
        TaskType.ASSEMBLE_DRAFT,
    }
)
#: Clip stage shown while a task of this type runs.
STAGE_FOR_TASK: dict[TaskType, ClipStatus] = {
    TaskType.STRUCTURAL_EDIT: ClipStatus.EDITING,
    TaskType.CAPTIONS: ClipStatus.EDITING,
    TaskType.VISUAL_POLISH: ClipStatus.EDITING,
    TaskType.AUDIO_POLISH: ClipStatus.EDITING,
    TaskType.ENHANCEMENT: ClipStatus.EDITING,
    TaskType.REVISION: ClipStatus.EDITING,
    TaskType.ASSEMBLE_DRAFT: ClipStatus.EDITING,
    TaskType.RENDER_PREVIEW: ClipStatus.PREVIEW_RENDERING,
    TaskType.TECHNICAL_QA: ClipStatus.TECHNICAL_QA,
    TaskType.EDITORIAL_QA: ClipStatus.EDITORIAL_QA,
}


@dataclass
class Running:
    project_id: str
    task: Task
    attempt_id: str
    adapter: AgentRuntimeAdapter
    handle: RunHandle
    provider_id: str | None
    model: str | None
    scope_key: str
    fingerprint: str
    started: datetime
    claims: dict[str, Any] = field(default_factory=dict)


class ProductionOrchestrator:
    def __init__(
        self,
        workspace: Path,
        registry: WorkspaceRegistry | None = None,
        clock: Callable[[], datetime] = utc_now,
        rng: random.Random | None = None,
        adapters: Iterable[AgentRuntimeAdapter] = (),
    ):
        self.workspace = workspace.expanduser().resolve()
        self.registry = registry or WorkspaceRegistry(self.workspace)
        self.clock = clock
        self.rng = rng or random.Random()  # noqa: S311 — retry jitter only
        self.config = self.registry.resource_config()
        self.resources = ResourceManager(self.config, self.workspace)
        self.resources.configure(self.config, self.registry.providers())
        self.teams = TeamManager()
        self._lock = threading.RLock()
        self._productions: dict[str, ProductionStore] = {}
        self._schedulers: dict[str, Scheduler] = {}
        self._transcribers: dict[str, Any] = {}
        self._running: dict[str, Running] = {}
        self.claims: dict[str, dict[str, Any]] = {}
        self.engine = EngineToolAdapter(build_handlers(self))
        self.adapters: dict[str, AgentRuntimeAdapter] = {"engine": self.engine}
        from ageneditor.production.agent_tools import ScopedToolService

        self.tools = ScopedToolService(self)
        for adapter in adapters:
            self.adapters[adapter.name] = adapter
            bind = getattr(adapter, "bind", None)
            if bind is not None:
                bind(self.tools)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ------------------------------------------------------------ project registry

    def production(self, project_id: str) -> ProductionStore:
        with self._lock:
            if project_id not in self._productions:
                for database in self.workspace.glob("*/project.sqlite3"):
                    try:
                        store = ProjectStore(database.parent)
                        if store.project_id == project_id:
                            self._attach(store)
                            break
                    except (EngineException, sqlite3.Error):
                        continue
            if project_id not in self._productions:
                raise EngineException(ErrorCode.PROJECT_NOT_FOUND, "project is not in this workspace")
            return self._productions[project_id]

    def _attach(self, store: ProjectStore) -> ProductionStore:
        production = ProductionStore(store, self.clock)
        scheduler = Scheduler(production, self.rng)
        self._productions[store.project_id] = production
        self._schedulers[store.project_id] = scheduler
        scheduler.recover()  # reconcile anything a previous engine process left running
        return production

    def scheduler(self, project_id: str) -> Scheduler:
        self.production(project_id)
        return self._schedulers[project_id]

    def all_productions(self) -> list[ProductionStore]:
        with self._lock:
            for database in self.workspace.glob("*/project.sqlite3"):
                try:
                    store = ProjectStore(database.parent)
                except (EngineException, sqlite3.Error):
                    continue
                if store.project_id not in self._productions:
                    self._attach(store)
            return list(self._productions.values())

    def _find(self, table: str, row_id: str) -> ProductionStore:
        for production in self.all_productions():
            with production.connect() as db:
                if db.execute(f"SELECT 1 FROM {table} WHERE id=?", (row_id,)).fetchone():  # noqa: S608
                    return production
        raise EngineException(ErrorCode.INVALID_ARGUMENT, f"{table[:-1]} does not exist")

    def production_for_batch(self, batch_id: str) -> ProductionStore:
        return self._find("batches", batch_id)

    def production_for_clip(self, clip_id: str) -> ProductionStore:
        return self._find("clips", clip_id)

    def production_for_task(self, task_id: str) -> ProductionStore:
        return self._find("tasks", task_id)

    def transcription_manager(self, project_id: str) -> Any:
        from ageneditor.transcription import TranscriptionManager

        with self._lock:
            if project_id not in self._transcribers:
                self._transcribers[project_id] = TranscriptionManager(self.production(project_id).project)
            return self._transcribers[project_id]

    # ------------------------------------------------------------ graph hooks used by handlers

    def on_plan_saved(
        self, production: ProductionStore, db: sqlite3.Connection, task_id: str, clip_id: str
    ) -> None:
        clip = production.clip(clip_id, db)
        plan = production.plan(str(clip.plan_id), db)
        batch = production.batch(clip.batch_id, db)
        graph = production_graph(clip_id, plan, batch.policy.editorial_qa == "required")
        materialize(production, db, batch, clip_id, clip_id, graph, external={"structural": [task_id]})

    # ------------------------------------------------------------ main loop

    #: Seconds between machine-pressure samples recorded for batch metrics.
    RESOURCE_SAMPLE_SECONDS = 30.0

    def sample_resources(self) -> ResourceSnapshot:
        """Record one machine-pressure sample (CPU since the last sample, memory, disk, permits)."""
        snapshot = self.resources.sample().model_copy(update={"measured_at": self.clock()})
        self.registry.record_resource_sample(snapshot)
        return snapshot

    def start(self, interval: float = 0.5) -> None:
        if self._thread is not None:
            return
        self._stop.clear()

        def loop() -> None:
            import time

            next_sample = 0.0
            while not self._stop.wait(interval):
                if time.monotonic() >= next_sample:
                    next_sample = time.monotonic() + self.RESOURCE_SAMPLE_SECONDS
                    try:
                        self.sample_resources()
                    except Exception:  # noqa: S110 — telemetry must never stop production
                        pass
                try:
                    self.tick()
                except Exception:  # noqa: S112 — the loop must survive one bad tick; state is durable
                    continue

        self._thread = threading.Thread(target=loop, name="ageneditor-orchestrator", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def tick(self) -> int:
        """One scheduling pass. Returns how many tasks started or finished."""
        with self._lock:
            changed = 0
            productions = self.all_productions()
            for production in productions:
                scheduler = self._schedulers[production.project_id]
                for attempt in scheduler.reap_expired():
                    running = self._running.pop(attempt.id, None)
                    if running is not None:
                        running.adapter.cancel(running.handle)
                        self.resources.release(attempt.id)
                    self._after_failure(production, production.task(attempt.task_id))
                    changed += 1
            changed += self._poll_running()
            for production in productions:
                for task in self._schedulers[production.project_id].refresh():
                    self._reflect_waiting(production, task)
            candidates: list[tuple[float, ProductionStore, Task]] = []
            for production in productions:
                scheduler = self._schedulers[production.project_id]
                waiting = production.tasks(statuses=[TaskStatus.READY, TaskStatus.BLOCKED])
                for task in waiting:
                    if task.status is TaskStatus.BLOCKED and task.block_reason not in SOFT_BLOCKS:
                        continue
                    candidates.append((scheduler.effective_priority(task), production, task))
            candidates.sort(key=lambda item: (-item[0], item[2].created_at, item[2].id))
            for _, production, task in candidates:
                if self._dispatch(production, task):
                    changed += 1
            return changed

    def run_until_idle(self, timeout: float = 120.0, poll: float = 0.05) -> None:
        """Tick until nothing is running or dispatchable (tests and the CLI)."""
        import time

        deadline = time.monotonic() + timeout
        idle_ticks = 0
        while time.monotonic() < deadline:
            moved = self.tick()
            if moved == 0 and not self._running:
                idle_ticks += 1
                if idle_ticks >= 3:
                    return
            else:
                idle_ticks = 0
            time.sleep(poll)
        raise TimeoutError("production did not settle in time")

    # ------------------------------------------------------------ dispatch

    def _snapshot_for(self, production: ProductionStore, task: Task):
        if task.clip_id is not None:
            clip = production.clip(task.clip_id)
            if clip.plan_id is not None:
                return production.snapshot(production.plan(clip.plan_id).context_hash)
        return production.snapshot(production.batch(task.batch_id).context_hash)

    def _adapter_ready(self) -> dict[str, tuple[bool, str]]:
        ready = {}
        for name, adapter in self.adapters.items():
            try:
                ready[name] = adapter.available()
            except Exception as exc:  # an adapter probe must never break scheduling
                ready[name] = (False, f"unavailable: {type(exc).__name__}")
        return ready

    def _dispatch(self, production: ProductionStore, task: Task) -> bool:
        scheduler = self._schedulers[production.project_id]
        batch = production.batch(task.batch_id)
        if batch.paused or batch.status is BatchStatus.CANCELLED:
            return False
        scope_key, fp = fingerprint(production, task)
        if task.status is TaskStatus.READY and task.type in REUSABLE:
            checkpoint = reusable_checkpoint(production, task, scope_key, fp)
            if checkpoint is not None:
                done = scheduler.complete_from_checkpoint(task, checkpoint)
                self._after_success(production, done)
                return True
        providers = self.registry.providers()
        provider_ready = {p.id: self.resources.provider_allows(p, self.clock()) for p in providers}
        with production.connect() as db:
            spent = production.committed_spend(db, batch.id)
        decision = route(
            task,
            batch.policy,
            providers,
            self._adapter_ready(),
            provider_ready,
            SpendState(batch.policy.budget_micros, spent),
            self.clock(),
        )
        if decision.route is RouteKind.UNAVAILABLE:
            self._soft_block(
                production,
                task,
                decision.block_reason or BlockReason.NO_CAPABLE_WORKER,
                "; ".join(decision.reasons[-1:]),
            )
            return False
        adapter = (
            self.adapters["engine"]
            if decision.route is RouteKind.ENGINE
            else self.adapters[next(p.adapter_type for p in providers if p.id == decision.provider_id)]
        )
        pools: list[str] = []
        if (pool := TASK_POOLS.get(task.type)) is not None:
            pools.append(pool)
        if decision.provider_id is not None:
            pools.append(f"provider:{decision.provider_id}")
        team_id = None
        if task.type in TEAM_TASKS and task.clip_id is not None:
            team_id = self._team_for(production, task)
            if team_id is None:
                self._soft_block(
                    production, task, BlockReason.WAITING_FOR_TEAM, "all production teams are busy"
                )
                return False
        if task.writer and task.clip_id is not None:
            with production.connect() as db:
                if production.live_writer(db, task.clip_id) is not None:
                    return False
        holder = f"{production.project_id}:{task.id}"
        if not self.resources.try_acquire(pools, holder):
            return False
        try:
            if task.status is TaskStatus.BLOCKED:
                task = self._unblock(production, task)
            lease = scheduler.lease(
                task.id,
                worker_id=team_id or f"{adapter.name}-worker",
                adapter=adapter.name,
                lease_seconds=self.config.lease_seconds,
                model=decision.model,
                fingerprint=fp,
                reserve_micros=decision.estimated_cost_micros,
            )
        except EngineException:
            self.resources.release(holder)
            return False
        self.resources.release(holder)
        self.resources.try_acquire(pools, lease.attempt.id)
        self._enter_stage(production, lease.task)
        try:
            if task.type is TaskType.EDITORIAL_QA:
                from ageneditor.quality.editorial_review import open_review

                open_review(production, lease.task, f"{adapter.name}:{decision.model or 'engine'}")
            assignment = ScopedTask(
                task=lease.task,
                attempt_id=lease.attempt.id,
                fencing_token=lease.attempt.fencing_token,
                lease_expires_at=lease.attempt.lease_expires_at,
                role=role_for(lease.task),
                context=self._snapshot_for(production, lease.task),
                allowed_tools=list(ROLE_TOOLS[role_for(lease.task)]),
                instructions=task_instructions(production, lease.task),
                capability_token=lease.token,
            )
            handle = adapter.start_task(assignment, decision.model)
            scheduler.mark_running(lease.attempt.id)
        except EngineException as exc:
            self.resources.release(lease.attempt.id)
            failed = scheduler.fail(lease.attempt.id, exc.error)
            self._after_failure(production, failed)
            return True
        if decision.provider_id is not None:
            self.resources.record_call(decision.provider_id, self.clock())
        self._running[lease.attempt.id] = Running(
            project_id=production.project_id,
            task=lease.task,
            attempt_id=lease.attempt.id,
            adapter=adapter,
            handle=handle,
            provider_id=decision.provider_id,
            model=decision.model,
            scope_key=scope_key,
            fingerprint=fp,
            started=self.clock(),
        )
        return True

    def _soft_block(self, production: ProductionStore, task: Task, reason: BlockReason, message: str) -> None:
        self._schedulers[production.project_id].block(task.id, reason, message)
        if task.clip_id is not None and reason in {BlockReason.NO_CAPABLE_WORKER, BlockReason.POLICY}:
            self._set_clip(production, task.clip_id, ClipStatus.BLOCKED, message)

    def _unblock(self, production: ProductionStore, task: Task) -> Task:
        with production.transaction() as db:
            current = production.task(task.id, db)
            if current.status is TaskStatus.BLOCKED:
                current = production.save_task(
                    db,
                    current.model_copy(update={"status": TaskStatus.READY, "block_reason": None}),
                    "task_status",
                )
            return current

    def _team_for(self, production: ProductionStore, task: Task) -> str | None:
        clip_key = (production.project_id, str(task.clip_id))
        batch = production.batch(task.batch_id)
        existing = self.teams.team_for(clip_key)
        if existing is not None:
            return existing
        waiting = set()
        for other in self.all_productions():
            for blocked in other.tasks(statuses=[TaskStatus.BLOCKED]):
                if blocked.block_reason is BlockReason.WAITING_FOR_TEAM:
                    waiting.add((other.project_id, blocked.batch_id))
        team = self.teams.assign(
            clip_key,
            (production.project_id, batch.id),
            self.config.team_slots,
            waiting,
            batch.policy.max_parallel_teams,
            self.clock(),
        )
        if team is not None:
            with production.transaction() as db:
                clip = production.clip(str(task.clip_id), db)
                production.save_clip(db, clip.model_copy(update={"team_id": team}))
                production.emit(db, "team_assigned", "clip", clip.id, team_id=team)
        return team

    def _release_team_if_done(self, production: ProductionStore, clip_id: str) -> None:
        pending = [
            task
            for task in production.tasks(clip_id=clip_id)
            if task.type in TEAM_TASKS
            and task.status not in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}
        ]
        if pending:
            return
        team = self.teams.release((production.project_id, clip_id))
        if team is not None:
            with production.transaction() as db:
                clip = production.clip(clip_id, db)
                production.save_clip(db, clip.model_copy(update={"team_id": None}))
                production.emit(db, "team_released", "clip", clip_id, team_id=team)

    # ------------------------------------------------------------ running work

    def _poll_running(self) -> int:
        finished = 0
        for attempt_id, running in list(self._running.items()):
            production = self._productions[running.project_id]
            scheduler = self._schedulers[running.project_id]
            status = running.adapter.poll(running.handle)
            if status.state is RunState.RUNNING:
                if not scheduler.heartbeat(attempt_id, self.config.lease_seconds):
                    running.adapter.cancel(running.handle)
                timeout = timedelta(seconds=self.config.host_timeout_seconds)
                if running.adapter.name != "engine" and self.clock() - running.started > timeout:
                    running.adapter.cancel(running.handle)
                continue
            del self._running[attempt_id]
            self.resources.release(attempt_id)
            finished += 1
            result = running.adapter.collect_result(running.handle)
            self._finish(production, running, result)
        return finished

    def _finish(self, production: ProductionStore, running: Running, result: AgentResult) -> None:
        scheduler = self._schedulers[production.project_id]
        claim = self.claims.pop(running.attempt_id, {})
        output = {**claim, **result.output}
        if result.ok:
            try:
                verified, artifact_id = self._verify(production, running.task, running.attempt_id, output)
            except EngineException as exc:
                verified, artifact_id = None, None
                result = AgentResult(
                    ok=False,
                    error=EngineError(
                        code=ErrorCode.ADAPTER_FAILED,
                        message=f"result could not be verified: {exc.error.message}"[:2000],
                    ),
                )
            if verified is not None:
                checkpoint = (
                    make_checkpoint(
                        running.task,
                        running.attempt_id,
                        running.scope_key,
                        running.fingerprint,
                        verified,
                        artifact_id,
                    )
                    if running.task.type in REUSABLE
                    else None
                )
                try:
                    task = scheduler.complete(running.attempt_id, verified, checkpoint, result.usage)
                except EngineException as exc:
                    # Lease expired or was reassigned while running: the stale result is discarded.
                    production_event(production, running, "stale_result_discarded", exc.error.message)
                    return
                self._after_success(production, task)
                return
        error = result.error or EngineError(code=ErrorCode.ADAPTER_FAILED, message="worker reported failure")
        retry_after = None
        if error.code is ErrorCode.RATE_LIMITED and running.provider_id is not None:
            retry_after = float(error.details.get("retry_after_s") or 60)
            self.resources.cool_down(running.provider_id, self.clock() + timedelta(seconds=retry_after))
        task = scheduler.fail(running.attempt_id, error, result.usage, retry_after)
        self._after_failure(production, task)

    # ------------------------------------------------------------ verification (engine state, not agent text)

    def _verify(
        self, production: ProductionStore, task: Task, attempt_id: str, output: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str | None]:
        if task.type is TaskType.ANALYZE_SOURCE:
            asset = production.project.assets()[str(task.params["asset_id"])]
            transcript = production.project.latest_transcript(asset.id, asset.sha256)
            if transcript is None or transcript.id != output.get("transcript_id"):
                raise EngineException(ErrorCode.TRANSCRIPT_NOT_FOUND, "no transcript of this source version")
            return {"transcript_id": transcript.id, "cached": bool(output.get("cached"))}, None
        if task.type is TaskType.DISCOVER_CLIPS:
            stored = {candidate.id for candidate in production.candidates(task.batch_id)}
            claimed = list(output.get("candidate_ids") or [])
            if not set(claimed) <= stored:
                raise EngineException(ErrorCode.INVALID_ARGUMENT, "reported candidates were not stored")
            return {
                key: output[key]
                for key in output
                if key
                in {
                    "method",
                    "candidate_ids",
                    "viable_count",
                    "recommended_count",
                    "shortfall",
                    "notes",
                    "rejected",
                }
            }, None
        if task.type is TaskType.PLAN_CLIP:
            clip = production.clip(str(task.clip_id))
            if clip.plan_id is None or (output.get("plan_id") and output["plan_id"] != clip.plan_id):
                raise EngineException(ErrorCode.INVALID_ARGUMENT, "no validated plan was saved")
            plan = production.plan(clip.plan_id)
            return {
                "plan_id": plan.id,
                "plan_version": plan.version,
                "pending_assets": production.pending_plan_assets(plan),
            }, None
        if task.writer:
            revisions = [
                r["revision"]
                for r in production.clip_revisions(str(task.clip_id))
                if r["attempt_id"] == attempt_id
            ]
            if revisions:
                verified = {"revision": max(revisions), "revisions": revisions}
                for key in ("skipped_passes", "skipped_reason", "finding_ids", "summary"):
                    if key in output:
                        verified[key] = output[key]
                return verified, None
            if output.get("no_op") and output.get("reason"):
                return {"no_op": True, "reason": str(output["reason"])[:500]}, None
            raise EngineException(
                ErrorCode.INVALID_TIMELINE, "the task finished without committing a revision"
            )
        if task.type is TaskType.ASSEMBLE_DRAFT:
            clip = production.clip(str(task.clip_id))
            if clip.draft_revision is None or clip.draft_revision != output.get("draft_revision"):
                raise EngineException(ErrorCode.INVALID_TIMELINE, "draft revision was not pinned")
            return {"draft_revision": clip.draft_revision, "warnings": output.get("warnings", [])}, None
        if task.type is TaskType.RENDER_PREVIEW:
            from ageneditor.media.hashing import sha256_file

            artifact = production.artifact(str(output.get("artifact_id")))
            path = production.project.safe_path(artifact.relative_path)
            if (
                artifact.created_by_attempt != attempt_id
                or not path.is_file()
                or sha256_file(path) != artifact.sha256
            ):
                raise EngineException(ErrorCode.RENDER_FAILED, "render artifact is missing or changed")
            return {
                "artifact_id": artifact.id,
                "sha256": artifact.sha256,
                "revision": artifact.timeline_revision,
            }, artifact.id
        if task.type is TaskType.TECHNICAL_QA:
            report = production.project.get_qa_report(str(output.get("render_artifact_id")))
            if report.id != output.get("report_id"):
                raise EngineException(ErrorCode.QA_FAILED, "technical QA report was not stored")
            return {
                **{k: output[k] for k in ("report_id", "render_artifact_id", "revision", "failed_checks")},
                "overall": report.overall.value,
            }, None
        if task.type is TaskType.EDITORIAL_QA:
            from ageneditor.quality.editorial_review import review_for_task

            review = review_for_task(production, task)
            if review.status is not QAReviewStatus.COMPLETED or review.verdict is None:
                raise EngineException(ErrorCode.QA_FAILED, "the reviewer did not submit findings")
            return {
                "review_id": review.id,
                "verdict": review.verdict.value,
                "findings": len([f for f in production.findings(review.clip_id) if f.review_id == review.id]),
            }, None
        if task.type is TaskType.EXPORT:
            approval_id = str(task.params.get("approval_id"))
            export = production.export_for(str(task.clip_id), approval_id)
            if export is None or export.id != output.get("export_id"):
                raise EngineException(ErrorCode.RENDER_FAILED, "export was not recorded")
            return {
                "export_id": export.id,
                "video_sha256": export.video_sha256,
                "qa_report_id": export.qa_report_id,
                "video_artifact_id": export.video_artifact_id,
            }, export.video_artifact_id
        raise EngineException(ErrorCode.INVALID_ARGUMENT, f"no verifier for {task.type.value}")

    # ------------------------------------------------------------ clip state hooks

    def _set_clip(
        self, production: ProductionStore, clip_id: str, status: ClipStatus, reason: str = ""
    ) -> None:
        with production.transaction() as db:
            try:
                production.transition_clip(db, clip_id, status, reason)
            except EngineException as exc:
                production.emit(
                    db,
                    "clip_status_skipped",
                    "clip",
                    clip_id,
                    wanted=status.value,
                    reason=exc.error.message[:300],
                )

    def _enter_stage(self, production: ProductionStore, task: Task) -> None:
        if task.clip_id is None:
            return
        clip = production.clip(task.clip_id)
        if task.type is TaskType.PLAN_CLIP:
            self._set_clip(production, task.clip_id, ClipStatus.DISCOVERED, "planning")
            return
        stage = STAGE_FOR_TASK.get(task.type)
        if stage is None:
            return
        if stage is ClipStatus.EDITING and clip.status in {
            ClipStatus.NEEDS_REVISION,
            ClipStatus.WAITING_FOR_ASSETS,
            ClipStatus.PLANNED,
        }:
            self._set_clip(production, task.clip_id, ClipStatus.READY)
        self._set_clip(production, task.clip_id, stage, task.type.value.replace("_", " "))

    def _reflect_waiting(self, production: ProductionStore, task: Task) -> None:
        if task.clip_id is None:
            return
        if task.status is TaskStatus.BLOCKED and task.block_reason is BlockReason.WAITING_FOR_ASSETS:
            self._set_clip(
                production, task.clip_id, ClipStatus.WAITING_FOR_ASSETS, "waiting for requested assets"
            )
        elif (
            task.status is TaskStatus.READY
            and production.clip(task.clip_id).status is ClipStatus.WAITING_FOR_ASSETS
        ):
            self._set_clip(production, task.clip_id, ClipStatus.READY, "requested assets are ready")

    def _after_success(self, production: ProductionStore, task: Task) -> None:
        batch = production.batch(task.batch_id)
        result = task.result or {}
        if task.type is TaskType.DISCOVER_CLIPS:
            with production.transaction() as db:
                production.set_batch_status(
                    db, production.batch(batch.id, db), BatchStatus.AWAITING_SELECTION
                )
            ids = list(result.get("candidate_ids") or [])[: int(result.get("recommended_count") or 0)]
            if batch.policy.selection is SelectionMode.AUTO_TOP and ids:
                from ageneditor.production.batch_service import ProductionService

                ProductionService(self).confirm_selection(batch.id, ids, "policy:auto_top")
            return
        if task.clip_id is None:
            return
        clip_id = task.clip_id
        if task.type is TaskType.PLAN_CLIP:
            self._set_clip(production, clip_id, ClipStatus.PLANNED, f"plan v{result.get('plan_version')}")
            pending = result.get("pending_assets") or []
            self._set_clip(
                production,
                clip_id,
                ClipStatus.WAITING_FOR_ASSETS if pending else ClipStatus.READY,
                "waiting for: " + ", ".join(pending) if pending else "plan validated",
            )
        elif task.type is TaskType.ASSEMBLE_DRAFT:
            self._release_team_if_done(production, clip_id)
        elif task.type is TaskType.TECHNICAL_QA:
            if result.get("overall") == CheckStatus.FAIL.value:
                from ageneditor.quality.revision_router import cancel_pending_editorial

                reason = "technical QA failed: " + ", ".join(result.get("failed_checks") or [])
                with production.transaction() as db:
                    cancel_pending_editorial(production, db, clip_id, task.cycle, reason)
                self._set_clip(production, clip_id, ClipStatus.NEEDS_HUMAN, reason)
            elif batch.policy.editorial_qa == "skip":
                self._set_clip(
                    production,
                    clip_id,
                    ClipStatus.REVIEW_READY,
                    "technical QA passed; editorial QA skipped by batch policy",
                )
        elif task.type is TaskType.EDITORIAL_QA:
            from ageneditor.quality.editorial_review import review_for_task
            from ageneditor.quality.revision_router import route_review

            route_review(production, batch, review_for_task(production, task))
        elif task.type is TaskType.EXPORT:
            self._set_clip(production, clip_id, ClipStatus.EXPORTED, "final render verified")
        if task.type in TEAM_TASKS:
            self._release_team_if_done(production, clip_id)

    def _after_failure(self, production: ProductionStore, task: Task) -> None:
        if task.clip_id is None:
            return
        message = task.error.message if task.error else ""
        if task.status is TaskStatus.FAILED:
            self._set_clip(production, task.clip_id, ClipStatus.FAILED, message)
            self.teams.release((production.project_id, task.clip_id))
        elif task.status is TaskStatus.NEEDS_HUMAN:
            self._set_clip(production, task.clip_id, ClipStatus.NEEDS_HUMAN, message)
            self.teams.release((production.project_id, task.clip_id))
        elif task.status is TaskStatus.RETRY_WAIT:
            self._set_clip(production, task.clip_id, ClipStatus.RETRY_WAIT, message)

    # ------------------------------------------------------------ control

    def cancel_running(
        self, production: ProductionStore, batch_id: str | None = None, clip_id: str | None = None
    ) -> None:
        with self._lock:
            for running in list(self._running.values()):
                if running.project_id != production.project_id:
                    continue
                if (batch_id and running.task.batch_id != batch_id) or (
                    clip_id and running.task.clip_id != clip_id
                ):
                    continue
                running.adapter.cancel(running.handle)

    def running(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "attempt_id": r.attempt_id,
                    "project_id": r.project_id,
                    "task_id": r.task.id,
                    "type": r.task.type.value,
                    "clip_id": r.task.clip_id,
                    "batch_id": r.task.batch_id,
                    "adapter": r.adapter.name,
                    "model": r.model,
                    "provider_id": r.provider_id,
                    "started_at": r.started.isoformat(),
                }
                for r in self._running.values()
            ]


def production_event(production: ProductionStore, running: Running, event: str, message: str) -> None:
    with production.transaction() as db:
        production.emit(
            db, event, "attempt", running.attempt_id, task_id=running.task.id, message=message[:300]
        )
