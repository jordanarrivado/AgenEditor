"""Turn QA findings into bounded, targeted revision rounds (spec §12).

* Blocker and major findings send the clip back for a revision round that fixes only those findings and
  then re-runs assemble → render → technical QA → editorial QA.
* A finding that repeats an earlier one (same category and reference) inherits its attempt count. Once a
  finding has used the batch's attempt limit (default two), it goes to a person instead of looping.
* Minor and info findings are kept for the human reviewer; they never block review.
"""

from __future__ import annotations

import sqlite3

from ageneditor.contracts import (
    Batch,
    ClipStatus,
    FindingInput,
    FindingReference,
    FindingState,
    QAFinding,
    QAReview,
    QAVerdict,
    TaskType,
)
from ageneditor.contracts.review import REVISION_SEVERITIES
from ageneditor.orchestration.task_graph import materialize, revision_graph
from ageneditor.production.common import new_id
from ageneditor.production.store import ProductionStore


def _same_issue(a: QAFinding, b: QAFinding) -> bool:
    return (
        a.category == b.category and a.reference.kind == b.reference.kind and a.reference.id == b.reference.id
    )


def next_cycle(production: ProductionStore, db: sqlite3.Connection, clip_id: str) -> int:
    return int(
        db.execute("SELECT coalesce(max(cycle), 0) + 1 FROM tasks WHERE clip_id=?", (clip_id,)).fetchone()[0]
    )


def queue_revision(
    production: ProductionStore, db: sqlite3.Connection, batch: Batch, clip_id: str, findings: list[QAFinding]
) -> list[str]:
    """Create one revision round for ``findings`` and mark them queued. Returns the new task IDs."""
    cycle = next_cycle(production, db, clip_id)
    graph = revision_graph(
        clip_id, cycle, [finding.id for finding in findings], batch.policy.editorial_qa == "required"
    )
    deterministic = all(finding.suggested_ops for finding in findings)
    graph.nodes[0].params["deterministic_ops"] = deterministic
    ids = materialize(production, db, batch, clip_id, clip_id, graph)
    for finding in findings:
        production.save_finding(
            db,
            finding.model_copy(
                update={
                    "state": FindingState.REVISION_QUEUED,
                    "revision_attempts": finding.revision_attempts + 1,
                }
            ),
        )
    production.emit(db, "revision_queued", "clip", clip_id, cycle=cycle, findings=[f.id for f in findings])
    return list(ids.values())


def route_review(production: ProductionStore, batch: Batch, review: QAReview) -> ClipStatus:
    """Decide what happens after a completed editorial review. Returns the clip's new status."""
    clip_id = review.clip_id
    with production.transaction() as db:
        current = production.findings(clip_id, db=db)
        new = [finding for finding in current if finding.review_id == review.id]
        earlier = [finding for finding in current if finding.review_id != review.id]
        # Earlier queued findings that were not raised again count as resolved by the revision.
        for old in earlier:
            if old.state is FindingState.REVISION_QUEUED:
                repeated = any(
                    _same_issue(old, finding) and finding.severity in REVISION_SEVERITIES for finding in new
                )
                if not repeated:
                    production.save_finding(db, old.model_copy(update={"state": FindingState.RESOLVED}))
        limit = batch.policy.max_revision_attempts_per_finding
        to_revise: list[QAFinding] = []
        to_human: list[QAFinding] = []
        for finding in new:
            if finding.severity not in REVISION_SEVERITIES:
                continue
            prior = max((old.revision_attempts for old in earlier if _same_issue(old, finding)), default=0)
            finding = finding.model_copy(update={"revision_attempts": prior})
            production.save_finding(db, finding)
            for old in earlier:
                if _same_issue(old, finding) and old.state is FindingState.REVISION_QUEUED:
                    production.save_finding(db, old.model_copy(update={"state": FindingState.RESOLVED}))
            (to_human if prior >= limit else to_revise).append(finding)
        for finding in to_human:
            production.save_finding(db, finding.model_copy(update={"state": FindingState.NEEDS_HUMAN}))
        if review.verdict is QAVerdict.NEEDS_REVISION and to_revise:
            queue_revision(production, db, batch, clip_id, to_revise)
            status, reason = (
                ClipStatus.NEEDS_REVISION,
                f"{len(to_revise)} finding(s) sent for a targeted revision",
            )
        elif to_human:
            status, reason = (
                ClipStatus.NEEDS_HUMAN,
                (
                    f"{len(to_human)} finding(s) still open after {limit} revision attempt(s); "
                    "a person decides"
                ),
            )
        else:
            status, reason = ClipStatus.REVIEW_READY, "editorial QA complete; waiting for a person to approve"
        production.transition_clip(db, clip_id, status, reason)
    return status


def human_revision(
    production: ProductionStore,
    batch: Batch,
    clip_id: str,
    review_id: str,
    revision: int,
    note: str,
    requested_by: str,
    finding: FindingInput | None = None,
) -> list[str]:
    """A person asks for changes on a reviewed clip. Their request becomes a human finding."""
    body = (
        finding
        or FindingInput(
            severity="major",  # type: ignore[arg-type]
            category="other",  # type: ignore[arg-type]
            reference=FindingReference(kind="timeline"),
            evidence=f"Requested by {requested_by}: {note}"[:1000],
            suggested_correction=note[:1000],
            confidence=1.0,
        )
    )
    with production.transaction() as db:
        record = QAFinding(
            **body.model_dump(),
            id=new_id("finding"),
            review_id=review_id,
            clip_id=clip_id,
            review_revision=revision,
            source="human",
            created_at=production.now(),
        )
        production.save_finding(db, record, insert=True)
        production.revoke_approval(db, clip_id, f"{requested_by} requested changes")
        ids = queue_revision(production, db, batch, clip_id, [record])
        production.transition_clip(
            db, clip_id, ClipStatus.NEEDS_REVISION, f"{requested_by} requested changes"
        )
    return ids


def cancel_pending_editorial(
    production: ProductionStore, db: sqlite3.Connection, clip_id: str, cycle: int, reason: str
) -> None:
    """Stop the editorial QA of a cycle whose technical QA failed (spec §7: fatal technical failures)."""
    from ageneditor.orchestration.scheduler import Scheduler

    scheduler = Scheduler(production)
    for task in production.tasks(clip_id=clip_id, db=db):
        if task.cycle == cycle and task.type is TaskType.EDITORIAL_QA:
            scheduler.cancel_task(db, task, reason)
