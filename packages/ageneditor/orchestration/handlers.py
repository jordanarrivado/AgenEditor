"""Deterministic engine handlers: the "deterministic tools first" half of every production team.

Each handler runs one scoped task through the existing engine services and returns a small output dict
that the orchestrator verifies against engine state before the task may succeed. Timeline writers commit
through a clip-scoped store that carries the attempt's fencing token. No handler calls a model.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from ageneditor.captions import to_srt
from ageneditor.contracts import (
    AddOverlay,
    Artifact,
    ArtifactKind,
    AudioMix,
    Author,
    BuildCaptions,
    CheckStatus,
    Clip,
    EngineException,
    ErrorCode,
    ExportArtifact,
    JobStatus,
    ModelPreset,
    Provenance,
    RevisionPatch,
    ScopedTask,
    SetAudioMix,
    SetCaptionPreset,
    SetColorPreset,
    TaskType,
    TrackId,
)
from ageneditor.jobs import preview_profile
from ageneditor.media.hashing import sha256_file
from ageneditor.media.probe import probe
from ageneditor.media.qa import run_qa
from ageneditor.media.render import render_timeline
from ageneditor.orchestration.agent_runtime import Handler
from ageneditor.production.clip_discovery import discover_deterministic
from ageneditor.production.clip_planner import output_frames, plan_from_candidate, validate_plan
from ageneditor.production.clip_timeline import ClipTimelineStore, PinnedTimelineStore
from ageneditor.production.common import content_hash, new_id
from ageneditor.production.context_resolver import batch_context
from ageneditor.production.store import ProductionStore
from ageneditor.production.workspace import WorkspaceRegistry
from ageneditor.revisions import apply_patch


class HandlerEnv(Protocol):
    registry: WorkspaceRegistry

    def production(self, project_id: str) -> ProductionStore: ...

    def transcription_manager(self, project_id: str) -> Any: ...

    def on_plan_saved(self, production: ProductionStore, db: Any, task_id: str, clip_id: str) -> None: ...


def _production(env: HandlerEnv, a: ScopedTask) -> ProductionStore:
    return env.production(a.task.project_id)


def _clip_store(env: HandlerEnv, a: ScopedTask) -> ClipTimelineStore:
    if a.task.clip_id is None:
        raise EngineException(ErrorCode.INVALID_ARGUMENT, "this task needs a clip")
    return ClipTimelineStore(_production(env, a), a.task.clip_id, a.attempt_id, a.fencing_token)


def _check_cancel(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise EngineException(ErrorCode.JOB_CANCELED, "task was cancelled")


def _patch(store: ClipTimelineStore, summary: str, ops: list[Any]) -> dict[str, Any]:
    if not ops:
        return {"no_op": True, "reason": summary, "revision": store.current_revision()}
    result = apply_patch(
        store,
        RevisionPatch(project_id=store.project_id, summary=summary[:500], ops=ops[:20]),
        store.current_revision(),
    )
    return {"revision": result.timeline.revision, "diff": result.diff.model_dump(mode="json")}


# ---------------------------------------------------------------- batch-level


def analyze_source(env: HandlerEnv, a: ScopedTask, cancel: threading.Event) -> dict[str, Any]:
    """Reuse the cached transcript of this exact source version, or transcribe locally once."""
    store = _production(env, a).project
    asset = store.assets()[str(a.task.params["asset_id"])]
    cached = store.latest_transcript(asset.id, asset.sha256)
    if cached is not None:
        return {"transcript_id": cached.id, "cached": True, "source_sha256": asset.sha256}
    manager = env.transcription_manager(a.task.project_id)
    job = manager.start(asset.id, ModelPreset(str(a.task.params.get("model_preset", "base"))))
    while not store.get_job(job.id).is_terminal:
        if cancel.is_set():
            manager.cancel(job.id)
            raise EngineException(ErrorCode.JOB_CANCELED, "transcription was cancelled")
        time.sleep(0.5)
    job = store.get_job(job.id)
    if job.status is not JobStatus.SUCCEEDED:
        raise EngineException(
            ErrorCode.TRANSCRIPTION_FAILED, job.error.message if job.error else "transcription did not finish"
        )
    transcript = store.latest_transcript(asset.id, asset.sha256)
    if transcript is None:
        raise EngineException(ErrorCode.TRANSCRIPTION_FAILED, "transcription produced no transcript")
    return {"transcript_id": transcript.id, "cached": False, "source_sha256": asset.sha256}


def discover_clips(env: HandlerEnv, a: ScopedTask, cancel: threading.Event) -> dict[str, Any]:
    production = _production(env, a)
    batch = production.batch(a.task.batch_id)
    report = discover_deterministic(production.project, batch, production.snapshot(batch.context_hash))
    _check_cancel(cancel)
    with production.transaction() as db:
        production.check_live_attempt(db, a.attempt_id)
        production.replace_candidates(db, batch.id, report.candidates)
        production.emit(
            db,
            "candidates_discovered",
            "batch",
            batch.id,
            method=report.method,
            viable=report.viable_count,
            shortfall=report.shortfall,
        )
    return {
        "method": report.method,
        "candidate_ids": [candidate.id for candidate in report.candidates],
        "viable_count": report.viable_count,
        "recommended_count": report.recommended_count,
        "shortfall": report.shortfall,
        "notes": report.notes,
    }


# ---------------------------------------------------------------- clip planning and editing


def plan_clip(env: HandlerEnv, a: ScopedTask, cancel: threading.Event) -> dict[str, Any]:
    production = _production(env, a)
    store = production.project
    clip = production.clip(str(a.task.clip_id))
    batch = production.batch(clip.batch_id)
    snapshot = batch_context(env.registry, store, batch, production.snapshot(batch.context_hash), clip)
    if snapshot.conflicts:
        raise EngineException(
            ErrorCode.HUMAN_DECISION_REQUIRED,
            "instruction layers conflict: " + "; ".join(item.message for item in snapshot.conflicts[:3]),
        )
    candidate = next(item for item in production.candidates(batch.id) if item.id == clip.candidate_id)
    with production.connect() as db:
        version = production.next_plan_version(db, clip.id)
    plan = plan_from_candidate(store, batch, clip, candidate, snapshot, version)
    errors = [issue for issue in validate_plan(store, batch, plan, snapshot) if issue.severity == "error"]
    if errors:
        raise EngineException(
            errors[0].code, "plan failed validation: " + "; ".join(i.message for i in errors[:3])
        )
    _check_cancel(cancel)
    with production.transaction() as db:
        production.check_live_attempt(db, a.attempt_id)
        production.save_snapshot(db, snapshot)
        production.save_plan(db, plan)
        env.on_plan_saved(production, db, a.task.id, clip.id)
    production.create_clip_timeline(clip.id, plan.output_profile, plan.expected_duration_frames)
    return {
        "plan_id": plan.id,
        "plan_version": plan.version,
        "pending_assets": production.pending_plan_assets(plan),
    }


def structural_edit(env: HandlerEnv, a: ScopedTask, cancel: threading.Event) -> dict[str, Any]:
    production = _production(env, a)
    clip_store = _clip_store(env, a)
    clip = production.clip(clip_store.clip_id)
    plan = production.plan(str(clip.plan_id))
    before = clip_store.timeline(clip_store.current_revision())
    rate = before.render_profile.frame_rate.fraction
    clips, position = [], 0
    for number, cut in enumerate(plan.cuts, start=1):
        span = output_frames(production.project, cut, rate)
        clips.append(
            Clip(
                clip_id=f"cut_{number:02d}",
                asset_id=cut.asset_id,
                track=TrackId.V1,
                source_in_frames=cut.source_in_frames,
                source_out_frames=cut.source_out_frames,
                timeline_in_frames=position,
                timeline_duration_frames=span,
                rationale=cut.rationale or None,
            )
        )
        position += span
    _check_cancel(cancel)
    candidate = before.model_copy(
        update={
            "clips": clips,
            "captions": [],
            "transitions": [],
            "provenance": Provenance(
                author=Author.SYSTEM,
                tool="production.structural_edit",
                summary=f"Cuts from plan v{plan.version}",
            ),
        }
    )
    committed = clip_store.commit_timeline(candidate, before.revision)
    return {"revision": committed.revision, "cuts": len(clips)}


def captions(env: HandlerEnv, a: ScopedTask, cancel: threading.Event) -> dict[str, Any]:
    del cancel
    store = _clip_store(env, a)
    creative = _production(env, a).plan(str(_production(env, a).clip(store.clip_id).plan_id)).creative
    timeline = store.timeline(store.current_revision())
    if creative.captions:
        ops: list[Any] = [SetCaptionPreset(preset=creative.caption_preset), BuildCaptions(enabled=True)]
        return _patch(store, "Captions from transcript words", ops)
    if timeline.captions:
        return _patch(store, "Captions turned off by the creative settings", [BuildCaptions(enabled=False)])
    return _patch(store, "Captions are off in the creative settings", [])


def visual_polish(env: HandlerEnv, a: ScopedTask, cancel: threading.Event) -> dict[str, Any]:
    del cancel
    store = _clip_store(env, a)
    production = _production(env, a)
    creative = production.plan(str(production.clip(store.clip_id).plan_id)).creative
    timeline = store.timeline(store.current_revision())
    ops: list[Any] = []
    if timeline.color_preset != creative.color_preset:
        ops.append(SetColorPreset(preset=creative.color_preset))
    return _patch(
        store, "Colour preset from the creative settings" if ops else "Visual settings already match", ops
    )


def audio_polish(env: HandlerEnv, a: ScopedTask, cancel: threading.Event) -> dict[str, Any]:
    del cancel
    store = _clip_store(env, a)
    production = _production(env, a)
    creative = production.plan(str(production.clip(store.clip_id).plan_id)).creative
    timeline = store.timeline(store.current_revision())
    wanted = AudioMix.model_validate(
        {**timeline.audio.model_dump(mode="json"), "normalize": creative.normalize_audio}
    )
    ops = [SetAudioMix(audio=wanted)] if wanted != timeline.audio else []
    return _patch(
        store, "Loudness normalization per the creative settings" if ops else "Audio mix already matches", ops
    )


def enhancement(env: HandlerEnv, a: ScopedTask, cancel: threading.Event) -> dict[str, Any]:
    """Approved B-roll only, matched from the person's own shot labels. Other passes need an agent."""
    del cancel
    from ageneditor.broll import suggest_broll

    store = _clip_store(env, a)
    passes = list(a.task.params.get("passes", []))
    skipped = [item for item in passes if item != "broll"]
    ops: list[Any] = []
    if "broll" in passes:
        timeline = store.timeline(store.current_revision())
        for number, suggestion in enumerate(suggest_broll(store, timeline, limit=3), start=1):
            ops.append(
                AddOverlay(
                    clip=Clip(
                        clip_id=f"broll_{number:02d}",
                        asset_id=suggestion.asset_id,
                        track=TrackId.V2,
                        source_in_frames=suggestion.source_in_frames,
                        source_out_frames=suggestion.source_out_frames,
                        timeline_in_frames=suggestion.timeline_in_frames,
                        timeline_duration_frames=suggestion.timeline_out_frames
                        - suggestion.timeline_in_frames,
                        rationale=suggestion.reason,
                    )
                )
            )
    summary = (
        "Supplied B-roll matched to spoken words" if ops else "No labelled B-roll matched the spoken words"
    )
    result = _patch(store, summary, ops)
    if skipped:
        result["skipped_passes"] = skipped
        result["skipped_reason"] = "music, SFX and transition passes need an editing agent"
    return result


def assemble_draft(env: HandlerEnv, a: ScopedTask, cancel: threading.Event) -> dict[str, Any]:
    del cancel
    production = _production(env, a)
    store = _clip_store(env, a)
    revision = store.current_revision()
    timeline = store.timeline(revision)
    issues = [
        *production.project.validation_issues(timeline),
        *production.project.removed_asset_issues(timeline),
    ]
    errors = [issue for issue in issues if issue.severity == "error"]
    if errors or not timeline.clips:
        detail = "; ".join(issue.message for issue in errors[:3]) or "timeline has no clips"
        raise EngineException(ErrorCode.INVALID_TIMELINE, f"draft is not valid: {detail}"[:2000])
    with production.transaction() as db:
        production.check_live_attempt(db, a.attempt_id)
        clip = production.clip(store.clip_id, db)
        production.save_clip(db, clip.model_copy(update={"draft_revision": revision}))
        production.emit(db, "draft_assembled", "clip", clip.id, revision=revision)
    return {"draft_revision": revision, "warnings": [issue.message for issue in issues][:10]}


def _verified_artifact(production: ProductionStore, path_rel: str, **fields: Any) -> Artifact:
    path = production.project.safe_path(path_rel)
    if not path.is_file() or path.stat().st_size == 0:
        raise EngineException(ErrorCode.RENDER_FAILED, "render produced no output file")
    if (
        fields["kind"] in {ArtifactKind.PREVIEW_RENDER, ArtifactKind.FINAL_RENDER}
        and probe(path).video is None
    ):
        raise EngineException(ErrorCode.RENDER_FAILED, "render output has no video stream")
    return Artifact(
        relative_path=path_rel, sha256=sha256_file(path), size_bytes=path.stat().st_size, **fields
    )


def render_preview(env: HandlerEnv, a: ScopedTask, cancel: threading.Event) -> dict[str, Any]:
    production = _production(env, a)
    store = _clip_store(env, a)
    clip = production.clip(store.clip_id)
    if clip.draft_revision is None:
        raise EngineException(ErrorCode.INVALID_TIMELINE, "no draft revision to render")
    timeline = store.timeline(clip.draft_revision)
    profile = preview_profile(timeline.render_profile)
    timeline = timeline.model_copy(update={"render_profile": profile})
    artifact_id = new_id("artifact")
    relative = f"renders/{artifact_id}/preview.mp4"
    output = production.project.safe_path(relative)
    output.parent.mkdir(parents=True, exist_ok=True)
    render_timeline(store, timeline, output, cancel=cancel)
    _check_cancel(cancel)
    artifact = _verified_artifact(
        production,
        relative,
        id=artifact_id,
        clip_id=clip.id,
        kind=ArtifactKind.PREVIEW_RENDER,
        timeline_revision=clip.draft_revision,
        profile_hash=content_hash(profile),
        created_by_attempt=a.attempt_id,
    )
    with production.transaction() as db:
        production.check_live_attempt(db, a.attempt_id)
        production.save_artifact(db, artifact)
    return {"artifact_id": artifact.id, "sha256": artifact.sha256, "revision": clip.draft_revision}


def _render_dependency(production: ProductionStore, task_id: str) -> Artifact:
    with production.connect() as db:
        for dependency in production.dependencies(db, task_id):
            if dependency.type is TaskType.RENDER_PREVIEW and dependency.result:
                return production.artifact(str(dependency.result["artifact_id"]))
    raise EngineException(ErrorCode.QA_FAILED, "no verified preview render precedes this check")


def technical_qa(env: HandlerEnv, a: ScopedTask, cancel: threading.Event) -> dict[str, Any]:
    del cancel
    production = _production(env, a)
    store = _clip_store(env, a)
    artifact = _render_dependency(production, a.task.id)
    path = production.project.safe_path(artifact.relative_path)
    if not path.is_file() or sha256_file(path) != artifact.sha256:
        raise EngineException(ErrorCode.QA_FAILED, "preview render changed or is missing")
    if artifact.timeline_revision is None:
        raise EngineException(ErrorCode.QA_FAILED, "render artifact has no pinned revision")
    timeline = store.timeline(artifact.timeline_revision)
    pinned = PinnedTimelineStore(
        store, timeline.model_copy(update={"render_profile": preview_profile(timeline.render_profile)})
    )
    report = run_qa(pinned, artifact.id, artifact.timeline_revision, path)  # type: ignore[arg-type]
    failed = [check.check_id for check in report.checks if check.status is CheckStatus.FAIL]
    return {
        "report_id": report.id,
        "overall": report.overall.value,
        "failed_checks": failed,
        "render_artifact_id": artifact.id,
        "revision": artifact.timeline_revision,
    }


def revision(env: HandlerEnv, a: ScopedTask, cancel: threading.Event) -> dict[str, Any]:
    """Apply reviewer-supplied typed fixes. Free-form findings are routed to an editing agent instead."""
    del cancel
    production = _production(env, a)
    store = _clip_store(env, a)
    findings = [production.finding(str(finding_id)) for finding_id in a.task.params.get("finding_ids", [])]
    ops = [op for finding in findings for op in finding.suggested_ops]
    if not ops:
        raise EngineException(ErrorCode.NO_CAPABLE_WORKER, "these findings need an editing agent to fix")
    summary = "Targeted fix: " + "; ".join(
        finding.suggested_correction or finding.category.value for finding in findings
    )
    result = _patch(store, summary[:500], ops)
    result["finding_ids"] = [finding.id for finding in findings]
    return result


def export_clip(env: HandlerEnv, a: ScopedTask, cancel: threading.Event) -> dict[str, Any]:
    from ageneditor.production.batch_service import check_approval

    production = _production(env, a)
    store = _clip_store(env, a)
    clip = production.clip(store.clip_id)
    approval = check_approval(production, clip.id, str(a.task.params.get("approval_id")))
    timeline = store.timeline(approval.timeline_revision)
    video_id = new_id("artifact")
    relative = f"renders/{video_id}/final.mp4"
    output = production.project.safe_path(relative)
    output.parent.mkdir(parents=True, exist_ok=True)
    render_timeline(store, timeline, output, cancel=cancel)
    _check_cancel(cancel)
    report = run_qa(store, video_id, approval.timeline_revision, output)  # type: ignore[arg-type]
    if report.overall is CheckStatus.FAIL:
        failed = ", ".join(check.check_id for check in report.checks if check.status is CheckStatus.FAIL)
        raise EngineException(ErrorCode.QA_FAILED, f"final render failed technical QA: {failed}"[:2000])
    video = _verified_artifact(
        production,
        relative,
        id=video_id,
        clip_id=clip.id,
        kind=ArtifactKind.FINAL_RENDER,
        timeline_revision=approval.timeline_revision,
        profile_hash=approval.export_profile_hash,
        created_by_attempt=a.attempt_id,
    )
    captions_artifact = None
    if timeline.captions:
        srt_relative = f"renders/{video_id}/captions.srt"
        production.project.write_artifact(srt_relative, to_srt(timeline))
        captions_artifact = _verified_artifact(
            production,
            srt_relative,
            id=new_id("artifact"),
            clip_id=clip.id,
            kind=ArtifactKind.CAPTIONS_SRT,
            timeline_revision=approval.timeline_revision,
            created_by_attempt=a.attempt_id,
        )
    export = ExportArtifact(
        id=new_id("export"),
        clip_id=clip.id,
        approval_id=approval.id,
        timeline_revision=approval.timeline_revision,
        video_artifact_id=video.id,
        video_path=video.relative_path,
        video_sha256=video.sha256,
        captions_path=captions_artifact.relative_path if captions_artifact else None,
        captions_sha256=captions_artifact.sha256 if captions_artifact else None,
        qa_report_id=report.id,
    )
    with production.transaction() as db:
        production.check_live_attempt(db, a.attempt_id)
        check_approval(production, clip.id, approval.id, db)
        production.save_artifact(db, video)
        if captions_artifact is not None:
            production.save_artifact(db, captions_artifact)
        production.insert_export(db, export)
    return {
        "export_id": export.id,
        "video_artifact_id": video.id,
        "video_sha256": video.sha256,
        "qa_report_id": report.id,
        "qa_overall": report.overall.value,
    }


def build_handlers(env: HandlerEnv) -> dict[TaskType, Handler]:
    def bind(function: Callable[[HandlerEnv, ScopedTask, threading.Event], dict[str, Any]]) -> Handler:
        return lambda assignment, cancel: function(env, assignment, cancel)

    return {
        TaskType.ANALYZE_SOURCE: bind(analyze_source),
        TaskType.DISCOVER_CLIPS: bind(discover_clips),
        TaskType.PLAN_CLIP: bind(plan_clip),
        TaskType.STRUCTURAL_EDIT: bind(structural_edit),
        TaskType.CAPTIONS: bind(captions),
        TaskType.VISUAL_POLISH: bind(visual_polish),
        TaskType.AUDIO_POLISH: bind(audio_polish),
        TaskType.ENHANCEMENT: bind(enhancement),
        TaskType.ASSEMBLE_DRAFT: bind(assemble_draft),
        TaskType.RENDER_PREVIEW: bind(render_preview),
        TaskType.TECHNICAL_QA: bind(technical_qa),
        TaskType.REVISION: bind(revision),
        TaskType.EXPORT: bind(export_clip),
    }
