"""AgenEditor 2.0 gate E: centralized QA, human approval and verified delivery.

Exit criteria: QA failures requeue only the affected work; approval is tied to the exact reviewed
artifacts; only enumerated approved clips are packaged, and every packaged byte is verified.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from production_helpers import build, create_batch, discover, make_project

from ageneditor.contracts import (
    AgentCapabilities,
    AgentResult,
    BatchStatus,
    BlockReason,
    ClipStatus,
    CostKind,
    EngineException,
    ErrorCode,
    FindingState,
    ModelUsage,
    RunState,
    RunStatus,
    ScopedTask,
    TaskStatus,
    TaskType,
)
from ageneditor.media.hashing import sha256_file
from ageneditor.orchestration.agent_runtime import RunHandle
from ageneditor.orchestration.orchestrator import ProductionOrchestrator
from ageneditor.production.batch_service import ProductionService, check_approval
from ageneditor.production.common import new_id
from ageneditor.production.workspace import WorkspaceRegistry
from ageneditor.quality.editorial_review import open_review

Script = Callable[[int, dict[str, Any]], dict[str, Any]]


class ScriptedReviewer:
    """A centralized QA worker driven by a script. It can only use the scoped QA tools."""

    name = "claude_code"

    def __init__(self, script: Script):
        self.script = script
        self.reviews: list[dict[str, Any]] = []
        self.tools: Any = None

    def bind(self, tools: Any) -> None:
        self.tools = tools

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            adapter_type=self.name, task_types=[TaskType.EDITORIAL_QA], deterministic=False
        )

    def available(self) -> tuple[bool, str]:
        return True, ""

    def start_task(self, assignment: ScopedTask, model: str | None = None) -> RunHandle:
        handle = RunHandle(id=new_id("run"), adapter=self.name, attempt_id=assignment.attempt_id)
        token = assignment.capability_token
        packet = self.tools.call("qa_get_review_packet", {}, token)
        self.reviews.append(packet)
        self.tools.call("qa_submit_findings", {"submission": self.script(len(self.reviews), packet)}, token)
        self.tools.call("production_task_complete", {"summary": "reviewed"}, token)
        handle.data["usage"] = ModelUsage(
            provider_id="claude_code", model=model or "x", cost_kind=CostKind.UNKNOWN
        )
        return handle

    def poll(self, handle: RunHandle) -> RunStatus:
        return RunStatus(state=RunState.FINISHED)

    def cancel(self, handle: RunHandle) -> None:
        handle.cancel.set()

    def collect_result(self, handle: RunHandle) -> AgentResult:
        return AgentResult(ok=True, usage=handle.data["usage"])


def _with_reviewer(
    workspace: Path, script: Script
) -> tuple[ProductionOrchestrator, ProductionService, ScriptedReviewer]:
    registry = WorkspaceRegistry(workspace)
    registry.set_provider_enabled("claude_code", True)
    reviewer = ScriptedReviewer(script)
    orchestrator = ProductionOrchestrator(workspace, registry, adapters=[reviewer])
    return orchestrator, ProductionService(orchestrator), reviewer


def _finding(
    severity: str = "major", ops: list[dict] | None = None, category: str = "framing"
) -> dict[str, Any]:
    return {
        "severity": severity,
        "category": category,
        "reference": {"kind": "timeline"},
        "evidence": "The grade looks flat for this client's warm style.",
        "suggested_correction": "Use the warm colour preset.",
        "suggested_ops": ops if ops is not None else [{"op": "set_color_preset", "preset": "warm"}],
        "confidence": 0.8,
    }


def _passed(summary: str = "Clean, coherent clip.") -> dict[str, Any]:
    return {
        "verdict": "pass",
        "summary": summary,
        "evidence_basis": ["timeline", "transcript", "technical_qa"],
        "findings": [_finding("minor", ops=[])],
    }


def test_reviewer_is_scoped_pinned_and_evidence_bound(tmp_path: Path, media_dir: Path) -> None:
    store, _ = make_project(tmp_path, media_dir)
    orchestrator, service = build(tmp_path)  # no AI provider: editorial QA waits for a reviewer
    batch_id = create_batch(service, store.project_id, editorial_qa="required", allow_data_egress=True)
    clip = service.confirm_selection(batch_id, discover(orchestrator, service, batch_id)[:1], "tester")[0]
    orchestrator.run_until_idle(timeout=300)
    production = orchestrator.production(store.project_id)
    qa = next(t for t in production.tasks(clip_id=clip.id) if t.type is TaskType.EDITORIAL_QA)
    assert qa.status is TaskStatus.BLOCKED and qa.block_reason is BlockReason.NO_CAPABLE_WORKER
    # Lease it as a reviewer would be leased.
    orchestrator._unblock(production, qa)
    lease = orchestrator.scheduler(store.project_id).lease(qa.id, "qa_worker", "test", 120)
    review = open_review(production, lease.task, "qa_worker")
    tools, token = orchestrator.tools, lease.token
    packet = tools.call("qa_get_review_packet", {}, token)
    render = production.artifact(review.render_artifact_id)
    assert packet["render"]["sha256"] == render.sha256 == sha256_file(store.safe_path(render.relative_path))
    assert packet["reviewed_revision"] == production.clip(clip.id).draft_revision
    with pytest.raises(EngineException) as denied:
        tools.call("clip_apply_revision", {"ops": [], "expected_revision": 0}, token)
    assert denied.value.error.code is ErrorCode.PERMISSION_DENIED
    with pytest.raises(EngineException):
        tools.call("clip_get_timeline", {"revision": 0}, token)  # only the reviewed revision
    bad_evidence = {**_passed(), "evidence_basis": ["frames"]}
    with pytest.raises(EngineException) as frames:
        tools.call("qa_submit_findings", {"submission": bad_evidence}, token)
    assert "not provided" in frames.value.error.message
    bad_ref = {**_passed(), "findings": [{**_finding(), "reference": {"kind": "clip", "id": "not_a_clip"}}]}
    with pytest.raises(EngineException):
        tools.call("qa_submit_findings", {"submission": bad_ref}, token)
    result = tools.call("qa_submit_findings", {"submission": _passed()}, token)
    assert result["verdict"] == "pass"
    with pytest.raises(EngineException):
        tools.call("qa_submit_findings", {"submission": _passed()}, token)  # once only
    # The reviewer never changed the clip.
    assert production.clip(clip.id).current_revision == review.timeline_revision


def test_major_finding_triggers_one_targeted_revision_then_passes(tmp_path: Path, media_dir: Path) -> None:
    store, _ = make_project(tmp_path, media_dir)

    def script(n: int, packet: dict) -> dict:
        if n == 1:
            return {
                "verdict": "needs_revision",
                "summary": "Grade is off.",
                "evidence_basis": ["timeline"],
                "findings": [_finding()],
            }
        assert packet["timeline"]["color_preset"] == "warm"  # the re-review sees the fixed revision
        return _passed()

    orchestrator, service, reviewer = _with_reviewer(tmp_path, script)
    batch_id = create_batch(service, store.project_id, editorial_qa="required", allow_data_egress=True)
    clip = service.confirm_selection(batch_id, discover(orchestrator, service, batch_id)[:1], "tester")[0]
    orchestrator.run_until_idle(timeout=300)
    production = orchestrator.production(store.project_id)
    assert production.clip(clip.id).status is ClipStatus.REVIEW_READY
    assert len(reviewer.reviews) == 2
    revisions = [t for t in production.tasks(clip_id=clip.id) if t.type is TaskType.REVISION]
    assert len(revisions) == 1 and revisions[0].cycle == 1 and revisions[0].status is TaskStatus.SUCCEEDED
    assert production.attempts(revisions[0].id)[0].adapter == "engine"  # typed fix applied deterministically
    states = [f.state for f in production.findings(clip.id)]
    assert FindingState.RESOLVED in states
    # Only this clip's downstream work re-ran: one structural edit, two renders.
    renders = [t for t in production.tasks(clip_id=clip.id) if t.type is TaskType.RENDER_PREVIEW]
    assert len(renders) == 2
    assert len([t for t in production.tasks(clip_id=clip.id) if t.type is TaskType.STRUCTURAL_EDIT]) == 1


def test_repeated_findings_stop_at_the_limit_and_go_to_a_person(tmp_path: Path, media_dir: Path) -> None:
    store, _ = make_project(tmp_path, media_dir)
    orchestrator, service, reviewer = _with_reviewer(
        tmp_path,
        lambda n, packet: {
            "verdict": "needs_revision",
            "summary": "Pacing still drags.",
            "evidence_basis": ["timeline"],
            "findings": [
                _finding(
                    category="pacing", ops=[{"op": "set_color_preset", "preset": "cool" if n % 2 else "warm"}]
                )
            ],
        },
    )
    batch_id = create_batch(service, store.project_id, editorial_qa="required", allow_data_egress=True)
    clip = service.confirm_selection(batch_id, discover(orchestrator, service, batch_id)[:1], "tester")[0]
    orchestrator.run_until_idle(timeout=600)
    production = orchestrator.production(store.project_id)
    assert production.clip(clip.id).status is ClipStatus.NEEDS_HUMAN
    assert len([t for t in production.tasks(clip_id=clip.id) if t.type is TaskType.REVISION]) == 2
    assert len(reviewer.reviews) == 3
    assert production.findings(clip.id, [FindingState.NEEDS_HUMAN])
    service.accept_needs_human(clip.id, "Producer", "Pacing is the client's style")
    assert production.clip(clip.id).status is ClipStatus.REVIEW_READY


def test_approval_binds_exact_artifacts_and_any_change_revokes_it(tmp_path: Path, media_dir: Path) -> None:
    store, _ = make_project(tmp_path, media_dir)
    orchestrator, service = build(tmp_path)
    batch_id = create_batch(service, store.project_id)
    clip = service.confirm_selection(batch_id, discover(orchestrator, service, batch_id)[:1], "tester")[0]
    orchestrator.run_until_idle(timeout=300)
    production = orchestrator.production(store.project_id)
    with pytest.raises(EngineException):
        service.approve([clip.id], "  ")
    approval = service.approve([clip.id], "Producer")[0]
    current = production.clip(clip.id)
    render = production.artifact(approval.render_artifact_id)
    assert (approval.timeline_revision, approval.render_sha256) == (current.draft_revision, render.sha256)
    assert approval.policy_snapshot_hash == production.batch(batch_id).context_hash
    orchestrator.run_until_idle(timeout=300)
    assert production.clip(clip.id).status is ClipStatus.EXPORTED
    export = production.export_for(clip.id, approval.id)
    assert export is not None
    assert sha256_file(store.safe_path(export.video_path)) == export.video_sha256
    assert (
        export.captions_path and sha256_file(store.safe_path(export.captions_path)) == export.captions_sha256
    )
    report = store.get_qa_report(export.video_artifact_id)
    assert report.overall.value != "fail" and report.timeline_revision == approval.timeline_revision
    # A person changes the timeline: the approval no longer applies to the new revision.
    head = production.clip(clip.id).current_revision
    assert head is not None
    production.commit_clip_timeline(clip.id, production.clip_timeline(clip.id), head)
    assert production.active_approval(clip.id) is None
    with pytest.raises(EngineException) as revoked:
        check_approval(production, clip.id, approval.id)
    assert revoked.value.error.code is ErrorCode.APPROVAL_REQUIRED


def test_human_revision_request_revokes_and_requeues_only_that_clip(tmp_path: Path, media_dir: Path) -> None:
    store, _ = make_project(tmp_path, media_dir)
    orchestrator, service = build(tmp_path)
    batch_id = create_batch(service, store.project_id)
    a, b = service.confirm_selection(batch_id, discover(orchestrator, service, batch_id)[:2], "tester")
    orchestrator.run_until_idle(timeout=300)
    production = orchestrator.production(store.project_id)
    service.approve([a.id, b.id], "Producer")  # deliberate bulk approval of an enumerated set
    orchestrator.run_until_idle(timeout=300)
    service.request_revision(a.id, "Start on the second sentence instead", "Producer")
    orchestrator.run_until_idle(timeout=120)
    assert production.active_approval(a.id) is None
    assert production.clip(b.id).status is ClipStatus.EXPORTED and production.active_approval(b.id)
    revision = next(t for t in production.tasks(clip_id=a.id) if t.type is TaskType.REVISION)
    # A free-form human request needs an editing agent; with none enabled it waits visibly.
    assert revision.status is TaskStatus.BLOCKED and revision.block_reason in {
        BlockReason.NO_CAPABLE_WORKER,
        BlockReason.POLICY,
    }


def test_partial_delivery_packages_only_enumerated_approved_clips(tmp_path: Path, media_dir: Path) -> None:
    from ageneditor.production.delivery_service import package_delivery

    store, _ = make_project(tmp_path, media_dir)
    orchestrator, service = build(tmp_path)
    batch_id = create_batch(service, store.project_id)
    a, b = service.confirm_selection(batch_id, discover(orchestrator, service, batch_id)[:2], "tester")
    orchestrator.run_until_idle(timeout=300)
    production = orchestrator.production(store.project_id)
    with pytest.raises(EngineException) as unapproved:
        package_delivery(production, batch_id, [a.id], "Producer")
    assert unapproved.value.error.code is ErrorCode.APPROVAL_REQUIRED
    service.approve([a.id], "Producer")
    orchestrator.run_until_idle(timeout=300)
    manifest = package_delivery(production, batch_id, [a.id], "Producer", client_name="Acme Co")
    assert manifest.partial and [c.clip_id for c in manifest.clips] == [a.id]
    folder = store.safe_path(f"deliveries/{manifest.package_name}")
    assert manifest.package_name.startswith("Acme_Co_")
    for item in manifest.files:
        assert sha256_file(folder / item.path) == item.sha256
    stored = json.loads((folder / "reports" / "delivery_manifest.json").read_text(encoding="utf-8"))
    assert stored["notice"].startswith("Packaged locally")
    assert {p.name for p in (folder / "videos").iterdir()} == {
        f"{production.plan(str(production.clip(a.id).plan_id)).delivery_name}.mp4"
    }
    assert not any(p.name.startswith(".staging") for p in folder.parent.iterdir())
    assert production.clip(a.id).status is ClipStatus.DELIVERED
    assert production.batch(batch_id).status is BatchStatus.PRODUCING  # clip B is still open
    service.approve([b.id], "Producer")
    orchestrator.run_until_idle(timeout=300)
    package_delivery(production, batch_id, [b.id], "Producer")
    assert production.batch(batch_id).status is BatchStatus.COMPLETED

    from ageneditor.production.metrics import batch_metrics

    metrics = batch_metrics(production, batch_id)
    assert (metrics.clips_total, metrics.clips_reached_review) == (2, 2)
    assert (metrics.clips_approved, metrics.clips_delivered) == (2, 2)
    assert metrics.first_review_ready_ms is not None and metrics.last_approval_ms is not None
    assert metrics.first_review_ready_ms <= metrics.last_approval_ms
    assert metrics.queue_wait.samples > 0 and metrics.attempt_run.samples > 0
    assert metrics.tasks_failed == 0 and 0 < metrics.tasks_succeeded <= metrics.tasks_total


def test_client_rule_can_forbid_partial_delivery(tmp_path: Path, media_dir: Path) -> None:
    from ageneditor.production.delivery_service import package_delivery

    store, _ = make_project(tmp_path, media_dir)
    orchestrator, service = build(tmp_path)
    batch_id = create_batch(service, store.project_id, allow_partial_delivery=False)
    a, _ = service.confirm_selection(batch_id, discover(orchestrator, service, batch_id)[:2], "tester")
    orchestrator.run_until_idle(timeout=300)
    service.approve([a.id], "Producer")
    orchestrator.run_until_idle(timeout=300)
    with pytest.raises(EngineException) as refused:
        package_delivery(orchestrator.production(store.project_id), batch_id, [a.id], "Producer")
    assert refused.value.error.code is ErrorCode.PERMISSION_DENIED


def test_tokens_cannot_cross_projects_or_outlive_their_attempt(tmp_path: Path, media_dir: Path) -> None:
    store_a, _ = make_project(tmp_path, media_dir, "client a")
    store_b, _ = make_project(tmp_path, media_dir, "client b")
    registry = WorkspaceRegistry(tmp_path)
    registry.bind_project(store_a.project_id, registry.create_client("Client A").id, False)
    registry.bind_project(store_b.project_id, registry.create_client("Client B").id, False)
    orchestrator, service = build(tmp_path)
    batch_id = create_batch(service, store_a.project_id)
    service.start_discovery(batch_id)
    production = orchestrator.production(store_a.project_id)
    scheduler = orchestrator.scheduler(store_a.project_id)
    scheduler.refresh()
    task = production.tasks(statuses=[TaskStatus.READY])[0]
    lease = scheduler.lease(task.id, "worker", "test", 120)
    tools = orchestrator.tools
    with pytest.raises(EngineException):  # another client's transcript is outside this token's scope
        tools.call("production_get_transcript", {"transcript_id": "transcript_client_b"}, lease.token)
    assert tools.call("production_get_transcript", {"transcript_id": "transcript_client_a"}, lease.token)[
        "words"
    ]
    forged = f"{store_b.project_id}.{lease.token.split('.', 1)[1]}"
    with pytest.raises(EngineException):
        tools.call("production_get_assignment", {}, forged)
    with pytest.raises(EngineException):  # not a discovery tool
        tools.call("qa_get_review_packet", {}, lease.token)
    scheduler.complete(lease.attempt.id, {"transcript_id": "transcript_client_a"})
    with pytest.raises(EngineException):  # revoked with the attempt
        tools.call("production_get_assignment", {}, lease.token)
