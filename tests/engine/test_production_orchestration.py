"""AgenEditor 2.0 gates C and D: two-team orchestration, recovery and capacity.

Exit criteria: two teams work concurrently through one engine with isolated outputs and no conflicting
writes; completed checkpoints are reused and failed tasks recover without duplicate committed outputs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from production_helpers import Clock, ScriptedClient, build, create_batch, discover, make_project

from ageneditor.contracts import (
    AssetChecklist,
    AssetRequest,
    AttemptState,
    BlockReason,
    ClipStatus,
    EngineException,
    ErrorCode,
    TaskStatus,
    TaskType,
)
from ageneditor.orchestration.capabilities import verify_token
from ageneditor.orchestration.orchestrator import ProductionOrchestrator
from ageneditor.production.common import new_id


def _ready_task(orchestrator: ProductionOrchestrator, project_id: str, task_type: TaskType):
    production = orchestrator.production(project_id)
    scheduler = orchestrator.scheduler(project_id)
    scheduler.refresh()
    return next(t for t in production.tasks(statuses=[TaskStatus.READY]) if t.type is task_type)


def _plan_then_pause(orchestrator: ProductionOrchestrator, service, batch_id: str) -> None:
    """Dispatch the planning tasks, then pause so nothing after planning starts."""
    orchestrator.tick()
    service.set_paused(batch_id, True)
    orchestrator.run_until_idle(timeout=120)


def test_three_clips_two_teams_third_waits(tmp_path: Path, media_dir: Path) -> None:
    store, _ = make_project(tmp_path, media_dir)
    orchestrator, service = build(tmp_path, team_slots=2)
    batch_id = create_batch(service, store.project_id)
    clips = service.confirm_selection(batch_id, discover(orchestrator, service, batch_id)[:3], "tester")
    orchestrator.tick()
    production = orchestrator.production(store.project_id)
    assert len(orchestrator.teams.active()) == 2
    waiting = [
        t for t in production.tasks(batch_id=batch_id) if t.block_reason is BlockReason.WAITING_FOR_TEAM
    ]
    assert len(waiting) == 1 and waiting[0].type is TaskType.PLAN_CLIP
    peak = 0
    while True:
        moved = orchestrator.tick()
        peak = max(peak, len(orchestrator.teams.active()))
        assert peak <= 2
        if not moved and not orchestrator.running():
            orchestrator.run_until_idle(timeout=300)
            break
    for clip in clips:
        assert production.clip(clip.id).status is ClipStatus.REVIEW_READY
    assert orchestrator.teams.active() == []  # released after each draft


def test_stale_writer_cannot_commit_after_lease_expiry(tmp_path: Path, media_dir: Path) -> None:
    clock = Clock()
    store, _ = make_project(tmp_path, media_dir)
    orchestrator, service = build(tmp_path, clock=clock)
    batch_id = create_batch(service, store.project_id)
    clip = service.confirm_selection(batch_id, discover(orchestrator, service, batch_id)[:1], "tester")[0]
    production = orchestrator.production(store.project_id)
    scheduler = orchestrator.scheduler(store.project_id)
    # Run planning only.
    plan_task = _ready_task(orchestrator, store.project_id, TaskType.PLAN_CLIP)
    lease = scheduler.lease(plan_task.id, "team_test", "engine", 120)
    from ageneditor.contracts import ScopedTask
    from ageneditor.orchestration.handlers import plan_clip

    assignment = ScopedTask(
        task=lease.task,
        attempt_id=lease.attempt.id,
        fencing_token=lease.attempt.fencing_token,
        lease_expires_at=lease.attempt.lease_expires_at,
        role="planner",
        context=production.snapshot(production.batch(batch_id).context_hash),
        instructions="x",
        capability_token=lease.token,
    )
    import threading

    output = plan_clip(orchestrator, assignment, threading.Event())
    scheduler.complete(lease.attempt.id, output)
    structural = _ready_task(orchestrator, store.project_id, TaskType.STRUCTURAL_EDIT)
    old = scheduler.lease(structural.id, "team_a", "engine", 60)
    # A person cannot commit while a team holds the clip's writer lease.
    from ageneditor.contracts import Clip, TrackId

    cut = production.plan(str(production.clip(clip.id).plan_id)).cuts[0]
    timeline = production.clip_timeline(clip.id).model_copy(
        update={
            "clips": [
                Clip(
                    clip_id="cut_01",
                    asset_id=cut.asset_id,
                    track=TrackId.V1,
                    source_in_frames=cut.source_in_frames,
                    source_out_frames=cut.source_out_frames,
                    timeline_in_frames=0,
                )
            ]
        }
    )
    with pytest.raises(EngineException) as busy:
        production.commit_clip_timeline(clip.id, timeline, 0)
    assert busy.value.error.code is ErrorCode.REVISION_CONFLICT
    clock.advance(61)
    assert scheduler.reap_expired()
    with pytest.raises(EngineException) as expired:
        verify_token(production, old.token)
    assert expired.value.error.code in {ErrorCode.PERMISSION_DENIED, ErrorCode.LEASE_EXPIRED}
    clock.advance(400)  # past the retry backoff
    retry = _ready_task(orchestrator, store.project_id, TaskType.STRUCTURAL_EDIT)
    new = scheduler.lease(retry.id, "team_b", "engine", 60)
    assert new.attempt.fencing_token > old.attempt.fencing_token
    with pytest.raises(EngineException) as stale:
        production.commit_clip_timeline(clip.id, timeline, 0, old.attempt.id, old.attempt.fencing_token)
    assert stale.value.error.code in {ErrorCode.LEASE_EXPIRED, ErrorCode.STALE_FENCE}
    with pytest.raises(EngineException):
        scheduler.complete(old.attempt.id, {"revision": 1})
    committed = production.commit_clip_timeline(
        clip.id, timeline, 0, new.attempt.id, new.attempt.fencing_token
    )
    assert committed.revision == 1 and production.clip_revisions(clip.id)[-1]["attempt_id"] == new.attempt.id


def test_missing_assets_block_one_clip_while_others_continue(tmp_path: Path, media_dir: Path) -> None:
    store, _ = make_project(tmp_path, media_dir)
    orchestrator, service = build(tmp_path)
    store.save_asset_checklist(
        AssetChecklist(
            project_id=store.project_id,
            configured=True,
            requests=[
                AssetRequest(id="broll_01", kind="video", prompt="Steam over a coffee cup, slow motion")
            ],
        ),
        0,
    )
    batch_id = create_batch(service, store.project_id)
    a, b = service.confirm_selection(batch_id, discover(orchestrator, service, batch_id)[:2], "tester")
    production = orchestrator.production(store.project_id)
    # Plan both clips, then give clip A an agent-revised plan that waits for the external B-roll.
    _plan_then_pause(orchestrator, service, batch_id)
    service.set_paused(batch_id, False)
    plan = production.plan(str(production.clip(a.id).plan_id))
    with production.transaction() as db:
        production.save_plan(
            db,
            plan.model_copy(
                update={"id": new_id("plan"), "version": plan.version + 1, "asset_request_ids": ["broll_01"]}
            ),
        )
    orchestrator.run_until_idle(timeout=300)
    assert production.clip(b.id).status is ClipStatus.REVIEW_READY
    assert production.clip(a.id).status is ClipStatus.WAITING_FOR_ASSETS
    blocked = [t for t in production.tasks(clip_id=a.id) if t.status is TaskStatus.BLOCKED]
    assert blocked and all(
        t.block_reason in {BlockReason.WAITING_FOR_ASSETS, BlockReason.DEPENDENCIES} for t in blocked
    )
    # The person skips the request: the clip resumes.
    checklist = store.asset_checklist()
    store.save_asset_checklist(
        checklist.model_copy(update={"links": {"broll_01": "dropped"}}), checklist.version
    )
    orchestrator.run_until_idle(timeout=300)
    assert production.clip(a.id).status is ClipStatus.REVIEW_READY


def test_restart_reconciles_committed_work_and_requeues_the_rest(tmp_path: Path, media_dir: Path) -> None:
    store, _ = make_project(tmp_path, media_dir)
    orchestrator, service = build(tmp_path)
    batch_id = create_batch(service, store.project_id)
    a, b = service.confirm_selection(batch_id, discover(orchestrator, service, batch_id)[:2], "tester")
    production = orchestrator.production(store.project_id)
    _plan_then_pause(orchestrator, service, batch_id)
    service.set_paused(batch_id, False)
    # The engine "crashes" after leasing the first edit of each clip.
    scheduler = orchestrator.scheduler(store.project_id)
    scheduler.refresh()
    ready = [t for t in production.tasks(statuses=[TaskStatus.READY]) if t.type is TaskType.STRUCTURAL_EDIT]
    task_a = next(t for t in ready if t.clip_id == a.id)
    task_b = next(t for t in ready if t.clip_id == b.id)
    lease_a = scheduler.lease(task_a.id, "team_a", "engine", 120)
    lease_b = scheduler.lease(task_b.id, "team_b", "engine", 120)
    # Clip A's attempt committed its revision just before the crash; clip B's did nothing.
    timeline = production.clip_timeline(a.id)
    plan = production.plan(str(production.clip(a.id).plan_id))
    from ageneditor.contracts import Clip, TrackId

    cut = plan.cuts[0]
    production.commit_clip_timeline(
        a.id,
        timeline.model_copy(
            update={
                "clips": [
                    Clip(
                        clip_id="cut_01",
                        asset_id=cut.asset_id,
                        track=TrackId.V1,
                        source_in_frames=cut.source_in_frames,
                        source_out_frames=cut.source_out_frames,
                        timeline_in_frames=0,
                    )
                ]
            }
        ),
        0,
        lease_a.attempt.id,
        lease_a.attempt.fencing_token,
    )
    restarted, _ = build(tmp_path)  # a new engine process on the same workspace
    production2 = restarted.production(store.project_id)
    assert production2.task(task_a.id).status is TaskStatus.SUCCEEDED
    assert production2.task(task_a.id).result["reconciled_after_restart"] is True
    assert production2.task(task_b.id).status is TaskStatus.RETRY_WAIT
    assert production2.attempt(lease_b.attempt.id).state is AttemptState.ABANDONED
    with pytest.raises(EngineException):  # the abandoned worker can never commit
        production2.commit_clip_timeline(
            b.id, production2.clip_timeline(b.id), 0, lease_b.attempt.id, lease_b.attempt.fencing_token
        )
    restarted.run_until_idle(timeout=300)
    assert production2.clip(a.id).status is ClipStatus.REVIEW_READY
    assert production2.clip(b.id).status is ClipStatus.REVIEW_READY
    assert len([r for r in production2.clip_revisions(a.id) if r["attempt_id"] == lease_a.attempt.id]) == 1


def test_crash_during_render_is_never_marked_complete(tmp_path: Path, media_dir: Path, monkeypatch) -> None:
    from ageneditor.orchestration import handlers

    store, _ = make_project(tmp_path, media_dir)
    orchestrator, service = build(tmp_path, clock=None)
    real_render = handlers.render_timeline
    calls = {"n": 0}

    def flaky(store_, timeline, output, cancel=None, **_):
        calls["n"] += 1
        if calls["n"] == 1:
            output.write_bytes(b"partial")
            raise EngineException(ErrorCode.RENDER_FAILED, "ffmpeg crashed")
        return real_render(store_, timeline, output, cancel=cancel)

    monkeypatch.setattr(handlers, "render_timeline", flaky)
    monkeypatch.setattr("ageneditor.orchestration.scheduler.backoff_seconds", lambda attempt, rng: 0.0)
    batch_id = create_batch(service, store.project_id)
    clip = service.confirm_selection(batch_id, discover(orchestrator, service, batch_id)[:1], "tester")[0]
    orchestrator.run_until_idle(timeout=300)
    production = orchestrator.production(store.project_id)
    render = next(t for t in production.tasks(clip_id=clip.id) if t.type is TaskType.RENDER_PREVIEW)
    attempts = production.attempts(render.id)
    assert [a.state for a in attempts] == [AttemptState.FAILED, AttemptState.SUCCEEDED]
    with production.connect() as db:
        artifacts = db.execute("SELECT count(*) FROM artifacts WHERE kind='preview_render'").fetchone()[0]
    assert artifacts == 1  # the partial file was never registered
    assert production.clip(clip.id).status is ClipStatus.REVIEW_READY


def test_rate_limit_only_delays_the_affected_task(tmp_path: Path, media_dir: Path) -> None:
    clock = Clock()

    def limited(_: dict) -> Exception:
        return EngineException(ErrorCode.RATE_LIMITED, "slow down", retry_after_s=30.0)

    client = ScriptedClient([limited])
    store, _ = make_project(tmp_path, media_dir)
    orchestrator, service = build(tmp_path, clock=clock, client=client)
    batch_id = create_batch(service, store.project_id, editorial_qa="required", allow_data_egress=True)
    clips = service.confirm_selection(batch_id, discover(orchestrator, service, batch_id)[:2], "tester")
    orchestrator.run_until_idle(timeout=300)
    production = orchestrator.production(store.project_id)
    qa = [t for t in production.tasks(batch_id=batch_id) if t.type is TaskType.EDITORIAL_QA]
    assert qa and all(t.status in {TaskStatus.RETRY_WAIT, TaskStatus.BLOCKED} for t in qa)
    assert any(t.error and t.error.code is ErrorCode.RATE_LIMITED for t in qa)
    # Everything not depending on the provider finished: both drafts rendered and passed technical QA.
    for clip in clips:
        tqa = next(t for t in production.tasks(clip_id=clip.id) if t.type is TaskType.TECHNICAL_QA)
        assert tqa.status is TaskStatus.SUCCEEDED
    assert production.usage(batch_id) == []  # the refused call reported no usage; nothing is invented


def test_budget_blocks_new_paid_requests(tmp_path: Path, media_dir: Path) -> None:
    client = ScriptedClient(
        [lambda _: {"verdict": "pass", "summary": "fine", "evidence_basis": ["timeline"], "findings": []}]
    )
    store, _ = make_project(tmp_path, media_dir)
    orchestrator, service = build(tmp_path, client=client)
    batch_id = create_batch(
        service, store.project_id, editorial_qa="required", allow_data_egress=True, budget_micros=1_000
    )
    clip = service.confirm_selection(batch_id, discover(orchestrator, service, batch_id)[:1], "tester")[0]
    orchestrator.run_until_idle(timeout=300)
    production = orchestrator.production(store.project_id)
    qa = next(t for t in production.tasks(clip_id=clip.id) if t.type is TaskType.EDITORIAL_QA)
    assert qa.status is TaskStatus.BLOCKED and qa.block_reason is BlockReason.BUDGET
    assert client.calls == []  # no paid call was made


def test_ai_without_egress_permission_falls_back_or_waits(tmp_path: Path, media_dir: Path) -> None:
    client = ScriptedClient(
        [lambda _: {"verdict": "pass", "summary": "fine", "evidence_basis": ["timeline"], "findings": []}]
    )
    store, _ = make_project(tmp_path, media_dir)
    orchestrator, service = build(tmp_path, client=client)
    batch_id = create_batch(service, store.project_id, editorial_qa="required", allow_data_egress=False)
    clip = service.confirm_selection(batch_id, discover(orchestrator, service, batch_id)[:1], "tester")[0]
    orchestrator.run_until_idle(timeout=300)
    production = orchestrator.production(store.project_id)
    qa = next(t for t in production.tasks(clip_id=clip.id) if t.type is TaskType.EDITORIAL_QA)
    assert qa.status is TaskStatus.BLOCKED and qa.block_reason is BlockReason.POLICY
    assert production.clip(clip.id).status is ClipStatus.BLOCKED
    assert client.calls == []
