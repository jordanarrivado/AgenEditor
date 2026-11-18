"""Centralized, independent editorial QA (spec §12).

The review is opened by the engine when a QA worker leases the task, pinned to the clip's draft revision,
its verified preview render and its technical QA report. The reviewer gets a read-only packet and may
only submit typed findings; every finding reference is resolved against the pinned revision. A reviewer
can never change the timeline, and a review never counts as human approval.
"""

from __future__ import annotations

from typing import Any

from ageneditor.captions import map_words, select_transcripts
from ageneditor.contracts import (
    ClipPlan,
    EngineException,
    ErrorCode,
    FindingState,
    QAFinding,
    QAFindingSubmission,
    QAReview,
    QAReviewStatus,
    Task,
    TaskType,
    Timeline,
)
from ageneditor.production.clip_timeline import ClipTimelineStore
from ageneditor.production.common import new_id
from ageneditor.production.store import ProductionStore

#: Evidence the engine actually provides in a packet. Frames are not sent, so "frames" is never valid.
PROVIDED_EVIDENCE = frozenset({"timeline", "transcript", "technical_qa", "plan", "brief"})


def _pinned_inputs(production: ProductionStore, task: Task) -> tuple[str, str, int, str | None]:
    """(render artifact id, sha256, revision, technical report id) from this cycle's checks."""
    with production.connect() as db:
        for dependency in production.dependencies(db, task.id):
            if dependency.type is TaskType.TECHNICAL_QA and dependency.result:
                result = dependency.result
                artifact = production.artifact(str(result["render_artifact_id"]))
                return artifact.id, artifact.sha256, int(result["revision"]), str(result["report_id"])
    raise EngineException(ErrorCode.QA_FAILED, "editorial QA needs a completed technical QA of a render")


def open_review(production: ProductionStore, task: Task, reviewer: str) -> QAReview:
    """Create (or reuse) the pinned review for a leased editorial-QA task."""
    for review in production.reviews(str(task.clip_id)):
        if review.task_id == task.id and review.status is not QAReviewStatus.COMPLETED:
            updated = review.model_copy(
                update={"reviewer": reviewer[:100], "status": QAReviewStatus.IN_REVIEW}
            )
            with production.transaction() as db:
                production.save_review(db, updated)
            return updated
    artifact_id, sha256, revision, report_id = _pinned_inputs(production, task)
    review = QAReview(
        id=new_id("review"),
        clip_id=str(task.clip_id),
        task_id=task.id,
        timeline_revision=revision,
        render_artifact_id=artifact_id,
        render_sha256=sha256,
        technical_report_id=report_id,
        reviewer=reviewer[:100],
        status=QAReviewStatus.IN_REVIEW,
        created_at=production.now(),
    )
    with production.transaction() as db:
        production.save_review(db, review, insert=True)
        production.emit(db, "review_opened", "review", review.id, clip_id=review.clip_id, revision=revision)
    return review


def review_for_task(production: ProductionStore, task: Task) -> QAReview:
    for review in production.reviews(str(task.clip_id)):
        if review.task_id == task.id:
            return review
    raise EngineException(ErrorCode.QA_FAILED, "no review is open for this task")


def _output_words(production: ProductionStore, clip_id: str, timeline: Timeline) -> list[dict[str, Any]]:
    store = ClipTimelineStore(production, clip_id)
    try:
        transcripts = select_transcripts(store, timeline)  # type: ignore[arg-type]
        words = map_words(store, timeline, transcripts)  # type: ignore[arg-type]
    except EngineException:
        return []
    return [
        {
            "word_id": w.word_id,
            "transcript_id": w.transcript_id,
            "text": w.text,
            "in_frame": w.in_frame,
            "out_frame": w.out_frame,
        }
        for w in words
    ][:3000]


def review_packet(production: ProductionStore, task: Task) -> dict[str, Any]:
    """Everything the reviewer may examine, all pinned to the reviewed revision. Read-only."""
    review = review_for_task(production, task)
    clip = production.clip(review.clip_id)
    timeline = production.clip_timeline(review.clip_id, review.timeline_revision)
    plan: ClipPlan | None = production.plan(clip.plan_id) if clip.plan_id else None
    report = production.project.get_qa_report(review.render_artifact_id)
    snapshot = production.snapshot(plan.context_hash) if plan else None
    return {
        "review_id": review.id,
        "clip": {"id": clip.id, "title": clip.title},
        "reviewed_revision": review.timeline_revision,
        "render": {"artifact_id": review.render_artifact_id, "sha256": review.render_sha256},
        "timeline": timeline.model_dump(mode="json"),
        "spoken_words_in_output_time": _output_words(production, clip.id, timeline),
        "plan": plan.model_dump(mode="json") if plan else None,
        "technical_qa": report.model_dump(mode="json"),
        "brief": {
            "instructions": snapshot.instructions if snapshot else [],
            "required_constraints": snapshot.required_constraints if snapshot else [],
            "qa_criteria": [criterion.text for criterion in plan.qa_criteria] if plan else [],
        },
        "evidence_available": sorted(PROVIDED_EVIDENCE),
        "notice": "You review evidence only. You cannot edit the clip; submit typed findings with "
        "qa_submit_findings.",
    }


def _check_reference(
    finding_number: int, reference: Any, timeline: Timeline, production: ProductionStore
) -> None:
    where = f"finding {finding_number}"
    if reference.kind == "clip" and reference.id not in {clip.clip_id for clip in timeline.clips}:
        raise EngineException(
            ErrorCode.INVALID_ARGUMENT, f"{where} cites a clip that is not in the reviewed revision"
        )
    if reference.kind == "caption" and reference.id not in {cue.cue_id for cue in timeline.captions}:
        raise EngineException(
            ErrorCode.INVALID_ARGUMENT, f"{where} cites a caption that is not in the reviewed revision"
        )
    if reference.kind == "word":
        try:
            transcript = production.project.transcript(str(reference.transcript_id))
        except EngineException as exc:
            raise EngineException(ErrorCode.INVALID_ARGUMENT, f"{where} cites an unknown transcript") from exc
        used = {clip.asset_id for clip in timeline.clips}
        if transcript.asset_id not in used or all(word.id != reference.id for word in transcript.words):
            raise EngineException(
                ErrorCode.INVALID_ARGUMENT, f"{where} cites a word outside the reviewed clip"
            )
    if reference.kind == "time_range" and reference.end_frame > timeline.duration_frames:
        raise EngineException(ErrorCode.INVALID_ARGUMENT, f"{where} cites time beyond the end of the clip")


def submit_findings(
    production: ProductionStore, task: Task, attempt_id: str, submission: QAFindingSubmission
) -> QAReview:
    """Validate and persist a reviewer's findings. The reviewer's lease must still be live."""
    review = review_for_task(production, task)
    if review.status is QAReviewStatus.COMPLETED:
        raise EngineException(ErrorCode.INVALID_STATE_TRANSITION, "this review was already submitted")
    unsupported = set(submission.evidence_basis) - PROVIDED_EVIDENCE
    if unsupported:
        raise EngineException(
            ErrorCode.INVALID_ARGUMENT,
            f"evidence {sorted(unsupported)} was not provided to you; only claim what you examined",
        )
    timeline = production.clip_timeline(review.clip_id, review.timeline_revision)
    for number, finding in enumerate(submission.findings, start=1):
        _check_reference(number, finding.reference, timeline, production)
    with production.transaction() as db:
        production.check_live_attempt(db, attempt_id)
        for finding in submission.findings:
            production.save_finding(
                db,
                QAFinding(
                    **finding.model_dump(),
                    id=new_id("finding"),
                    review_id=review.id,
                    clip_id=review.clip_id,
                    review_revision=review.timeline_revision,
                    source="ai",
                    created_at=production.now(),
                ),
                insert=True,
            )
        completed = review.model_copy(
            update={
                "status": QAReviewStatus.COMPLETED,
                "verdict": submission.verdict,
                "summary": submission.summary,
                "evidence_basis": list(submission.evidence_basis),
                "completed_at": production.now(),
            }
        )
        production.save_review(db, completed)
        production.emit(
            db,
            "review_completed",
            "review",
            review.id,
            clip_id=review.clip_id,
            verdict=submission.verdict.value,
            findings=len(submission.findings),
        )
    return completed


def open_findings(production: ProductionStore, clip_id: str) -> list[QAFinding]:
    return production.findings(
        clip_id, [FindingState.OPEN, FindingState.REVISION_QUEUED, FindingState.NEEDS_HUMAN]
    )
