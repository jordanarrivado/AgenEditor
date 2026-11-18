"""Batch lifecycle operations a person (desktop) or the orchestrator calls (spec §17).

Privileged decisions live here and are never MCP tools: selecting clips, approving revisions, requesting
changes, pausing/cancelling and packaging deliveries.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from ageneditor.contracts import (
    ArtifactKind,
    AssetRole,
    Batch,
    BatchCreateRequest,
    BatchStatus,
    CandidateStatus,
    ClipApproval,
    ClipStatus,
    CreativeDefaults,
    EngineException,
    ErrorCode,
    FindingState,
    ProductionClip,
    QAReview,
    QAReviewStatus,
    QAVerdict,
    TaskStatus,
    TaskType,
)
from ageneditor.contracts.orchestration import ClipTaskGraph, GraphNode
from ageneditor.media.hashing import sha256_file
from ageneditor.orchestration.task_graph import materialize
from ageneditor.production.common import content_hash, new_id
from ageneditor.production.context_resolver import resolve_context
from ageneditor.production.store import ProductionStore

if TYPE_CHECKING:
    from ageneditor.orchestration.orchestrator import ProductionOrchestrator

#: States from which a person may approve.
APPROVABLE = {ClipStatus.REVIEW_READY}


def check_approval(
    production: ProductionStore, clip_id: str, approval_id: str, db: sqlite3.Connection | None = None
) -> ClipApproval:
    """Re-verify the approval invariant: exact revision, render bytes, export profile and policy.

    Any mismatch revokes the approval; the clip then needs a new review decision.
    """
    if db is None:
        with production.transaction() as conn:
            return check_approval(production, clip_id, approval_id, conn)
    approval = production.active_approval(clip_id, db)
    if approval is None or approval.id != approval_id:
        raise EngineException(ErrorCode.APPROVAL_REQUIRED, "this clip has no matching active approval")
    clip = production.clip(clip_id, db)
    batch = production.batch(clip.batch_id, db)
    problems = []
    if (
        clip.current_revision != approval.timeline_revision
        or clip.draft_revision != approval.timeline_revision
    ):
        problems.append("the timeline changed after approval")
    artifact = production.artifact(approval.render_artifact_id)
    path = production.project.safe_path(artifact.relative_path)
    if not path.is_file() or sha256_file(path) != approval.render_sha256:
        problems.append("the approved preview render is missing or changed")
    if content_hash(batch.policy.output_profile) != approval.export_profile_hash:
        problems.append("the export profile changed after approval")
    if batch.context_hash != approval.policy_snapshot_hash:
        problems.append("the batch policy changed after approval")
    if problems:
        production.revoke_approval(db, clip_id, "; ".join(problems))
        raise EngineException(
            ErrorCode.APPROVAL_REQUIRED, "approval no longer applies: " + "; ".join(problems)
        )
    return approval


class ProductionService:
    def __init__(self, orchestrator: ProductionOrchestrator):
        self.orchestrator = orchestrator
        self.registry = orchestrator.registry

    def _production(self, project_id: str) -> ProductionStore:
        return self.orchestrator.production(project_id)

    def find(self, batch_id: str) -> ProductionStore:
        return self.orchestrator.production_for_batch(batch_id)

    # ------------------------------------------------------------ batches

    def create_batch(self, request: BatchCreateRequest) -> dict[str, Any]:
        production = self._production(request.project_id)
        store = production.project
        client_id = self.registry.project_client(store.project_id)
        batch_id = new_id("batch")
        snapshot = resolve_context(
            self.registry, store, client_id, request.policy, request.instructions, batch_id
        )
        if not snapshot.source_asset_hashes:
            raise EngineException(ErrorCode.ASSET_NOT_FOUND, "add a primary video before creating a batch")
        with production.transaction() as db:
            batch = Batch(
                id=batch_id,
                project_id=store.project_id,
                client_id=client_id,
                name=request.name,
                sequence=production.next_batch_sequence(db),
                policy=request.policy,
                instructions=request.instructions,
                context_hash=snapshot.hash,
                created_at=production.now(),
            )
            production.save_snapshot(db, snapshot)
            production.insert_batch(db, batch)
            production.emit(
                db,
                "batch_created",
                "batch",
                batch.id,
                name=batch.name,
                client_id=client_id,
                conflicts=len(snapshot.conflicts),
            )
        return {
            "batch": batch.model_dump(mode="json"),
            "conflicts": [c.model_dump(mode="json") for c in snapshot.conflicts],
        }

    def start_discovery(self, batch_id: str) -> Batch:
        """Queue source analysis (cached per source hash) and candidate discovery."""
        production = self.find(batch_id)
        with production.transaction() as db:
            batch = production.batch(batch_id, db)
            if batch.status is not BatchStatus.CREATED:
                raise EngineException(ErrorCode.INVALID_STATE_TRANSITION, f"batch is {batch.status.value}")
            snapshot = production.snapshot(batch.context_hash)
            nodes = [
                GraphNode(key=f"analyze_{n:02d}", type=TaskType.ANALYZE_SOURCE, params={"asset_id": asset_id})
                for n, asset_id in enumerate(sorted(snapshot.source_asset_hashes), start=1)
            ]
            nodes.append(
                GraphNode(
                    key="discover", type=TaskType.DISCOVER_CLIPS, depends_on=[node.key for node in nodes]
                )
            )
            materialize(
                production, db, batch, batch.id, None, ClipTaskGraph(clip_id=batch.id, cycle=0, nodes=nodes)
            )
            batch = production.set_batch_status(db, batch, BatchStatus.DISCOVERING)
        return batch

    def confirm_selection(
        self, batch_id: str, candidate_ids: Sequence[str], confirmed_by: str
    ) -> list[ProductionClip]:
        """Lock the chosen candidates as clips and queue their planning tasks."""
        if not candidate_ids:
            raise EngineException(ErrorCode.INVALID_ARGUMENT, "choose at least one candidate")
        production = self.find(batch_id)
        with production.transaction() as db:
            batch = production.batch(batch_id, db)
            if batch.status is not BatchStatus.AWAITING_SELECTION:
                raise EngineException(
                    ErrorCode.INVALID_STATE_TRANSITION, "this batch is not waiting for a selection"
                )
            candidates = {candidate.id: candidate for candidate in production.candidates(batch_id, db)}
            unknown = [cid for cid in candidate_ids if cid not in candidates]
            if unknown:
                raise EngineException(
                    ErrorCode.INVALID_ARGUMENT, "a selected candidate does not belong to this batch"
                )
            clips = []
            for ordinal, candidate_id in enumerate(dict.fromkeys(candidate_ids), start=1):
                candidate = candidates[candidate_id]
                clip = ProductionClip(
                    id=new_id("clip"),
                    batch_id=batch.id,
                    candidate_id=candidate.id,
                    title=candidate.topic[:200],
                    ordinal=ordinal,
                    created_at=production.now(),
                    updated_at=production.now(),
                )
                production.insert_clip(db, clip)
                production.set_candidate_status(db, candidate, CandidateStatus.SELECTED)
                materialize(
                    production,
                    db,
                    batch,
                    clip.id,
                    clip.id,
                    ClipTaskGraph(
                        clip_id=clip.id, cycle=0, nodes=[GraphNode(key="plan", type=TaskType.PLAN_CLIP)]
                    ),
                )
                clips.append(clip)
            for candidate in candidates.values():
                if candidate.id not in candidate_ids:
                    production.set_candidate_status(db, candidate, CandidateStatus.REJECTED)
            production.emit(
                db,
                "selection_confirmed",
                "batch",
                batch.id,
                by=confirmed_by[:80],
                clips=[clip.id for clip in clips],
            )
            production.set_batch_status(db, batch, BatchStatus.PRODUCING)
        return clips

    def set_clip_instructions(
        self, clip_id: str, instructions: str, creative: CreativeDefaults
    ) -> ProductionClip:
        production = self.orchestrator.production_for_clip(clip_id)
        with production.transaction() as db:
            clip = production.clip(clip_id, db)
            if clip.plan_id is not None:
                raise EngineException(ErrorCode.INVALID_STATE_TRANSITION, "the clip is already planned")
            return production.save_clip(
                db, clip.model_copy(update={"instructions": instructions, "creative": creative})
            )

    def set_paused(self, batch_id: str, paused: bool) -> Batch:
        production = self.find(batch_id)
        with production.transaction() as db:
            batch = production.batch(batch_id, db)
            if batch.status in {BatchStatus.CANCELLED, BatchStatus.COMPLETED}:
                raise EngineException(ErrorCode.INVALID_STATE_TRANSITION, f"batch is {batch.status.value}")
            batch = batch.model_copy(update={"paused": paused})
            production.save_batch(db, batch)
            production.emit(db, "batch_paused" if paused else "batch_resumed", "batch", batch_id)
        return batch

    def cancel(self, batch_id: str, reason: str = "cancelled by a person") -> Batch:
        production = self.find(batch_id)
        self.orchestrator.cancel_running(production, batch_id=batch_id)
        scheduler = self.orchestrator.scheduler(production.project_id)
        with production.transaction() as db:
            batch = production.batch(batch_id, db)
            for task in production.tasks(batch_id=batch_id, db=db):
                scheduler.cancel_task(db, task, reason)
            for clip in production.clips(batch_id, db):
                if clip.status not in {ClipStatus.DELIVERED, ClipStatus.CANCELLED}:
                    production.transition_clip(db, clip.id, ClipStatus.CANCELLED, reason)
            batch = production.set_batch_status(db, batch, BatchStatus.CANCELLED)
        for clip in production.clips(batch_id):
            self.orchestrator.teams.release((production.project_id, clip.id))
        return batch

    def retry_task(self, task_id: str) -> dict[str, Any]:
        production = self.orchestrator.production_for_task(task_id)
        task = self.orchestrator.scheduler(production.project_id).reset_for_retry(task_id)
        return task.model_dump(mode="json")

    # ------------------------------------------------------------ review decisions

    def _latest_render(self, production: ProductionStore, clip: ProductionClip) -> tuple[str, str]:
        with production.connect() as db:
            rows = db.execute(
                "SELECT data FROM artifacts WHERE clip_id=? AND kind=? ORDER BY rowid DESC",
                (clip.id, ArtifactKind.PREVIEW_RENDER.value),
            ).fetchall()
        from ageneditor.contracts import Artifact

        for row in rows:
            artifact = Artifact.model_validate_json(row[0])
            if artifact.timeline_revision == clip.draft_revision:
                return artifact.id, artifact.sha256
        raise EngineException(ErrorCode.APPROVAL_REQUIRED, "no verified preview of the draft revision exists")

    def approve(self, clip_ids: Sequence[str], approved_by: str, note: str = "") -> list[ClipApproval]:
        """A person approves an enumerated set of reviewed clips, each bound to its exact artifact."""
        if not approved_by.strip():
            raise EngineException(ErrorCode.INVALID_ARGUMENT, "say who approves")
        approvals = []
        for clip_id in dict.fromkeys(clip_ids):
            production = self.orchestrator.production_for_clip(clip_id)
            clip = production.clip(clip_id)
            if clip.status not in APPROVABLE:
                raise EngineException(
                    ErrorCode.INVALID_STATE_TRANSITION, f"clip {clip.title!r} is not ready for review"
                )
            if clip.draft_revision is None or clip.draft_revision != clip.current_revision:
                raise EngineException(ErrorCode.APPROVAL_REQUIRED, "the clip changed after its review")
            artifact_id, sha256 = self._latest_render(production, clip)
            with production.transaction() as db:
                batch = production.batch(clip.batch_id, db)
                approval = ClipApproval(
                    id=new_id("approval"),
                    clip_id=clip.id,
                    timeline_revision=clip.draft_revision,
                    render_artifact_id=artifact_id,
                    render_sha256=sha256,
                    export_profile_hash=content_hash(batch.policy.output_profile),
                    policy_snapshot_hash=batch.context_hash,
                    approved_by=approved_by.strip()[:80],
                    note=note[:500],
                    approved_at=production.now(),
                )
                production.insert_approval(db, approval)
                for finding in production.findings(
                    clip.id, [FindingState.OPEN, FindingState.NEEDS_HUMAN], db
                ):
                    production.save_finding(db, finding.model_copy(update={"state": FindingState.ACCEPTED}))
                production.transition_clip(
                    db, clip.id, ClipStatus.HUMAN_APPROVED, f"approved by {approval.approved_by}"
                )
                cycle = int(
                    db.execute(
                        "SELECT coalesce(max(cycle),0) FROM tasks WHERE clip_id=?", (clip.id,)
                    ).fetchone()[0]
                )
                materialize(
                    production,
                    db,
                    batch,
                    clip.id,
                    clip.id,
                    ClipTaskGraph(
                        clip_id=clip.id,
                        cycle=cycle,
                        nodes=[
                            GraphNode(
                                key=f"export_{approval.id[-8:]}",
                                type=TaskType.EXPORT,
                                params={"approval_id": approval.id},
                            )
                        ],
                    ),
                )
            approvals.append(approval)
        return approvals

    def accept_needs_human(self, clip_id: str, decided_by: str, note: str) -> ProductionClip:
        """A person accepts the open findings of a clip that QA could not resolve; it becomes reviewable."""
        production = self.orchestrator.production_for_clip(clip_id)
        with production.transaction() as db:
            clip = production.clip(clip_id, db)
            if clip.status is not ClipStatus.NEEDS_HUMAN or clip.draft_revision is None:
                raise EngineException(
                    ErrorCode.INVALID_STATE_TRANSITION, "the clip is not waiting for a decision"
                )
            for finding in production.findings(clip_id, [FindingState.NEEDS_HUMAN, FindingState.OPEN], db):
                production.save_finding(db, finding.model_copy(update={"state": FindingState.ACCEPTED}))
            production.emit(db, "findings_accepted", "clip", clip_id, by=decided_by[:80], note=note[:300])
            return production.transition_clip(
                db, clip_id, ClipStatus.REVIEW_READY, f"{decided_by} accepted open findings"
            )

    def request_revision(self, clip_id: str, note: str, requested_by: str) -> list[str]:
        from ageneditor.quality.revision_router import human_revision

        if not note.strip():
            raise EngineException(ErrorCode.INVALID_ARGUMENT, "describe the change you want")
        production = self.orchestrator.production_for_clip(clip_id)
        clip = production.clip(clip_id)
        if clip.status not in {
            ClipStatus.REVIEW_READY,
            ClipStatus.HUMAN_APPROVED,
            ClipStatus.EXPORTED,
            ClipStatus.NEEDS_HUMAN,
        }:
            raise EngineException(
                ErrorCode.INVALID_STATE_TRANSITION, "changes can be requested on reviewed clips"
            )
        if any(
            t.status in {TaskStatus.LEASED, TaskStatus.RUNNING} for t in production.tasks(clip_id=clip_id)
        ):
            raise EngineException(ErrorCode.REVISION_CONFLICT, "wait for the running task to finish")
        batch = production.batch(clip.batch_id)
        artifact_id, sha256 = self._latest_render(production, clip)
        review = QAReview(
            id=new_id("review"),
            clip_id=clip_id,
            timeline_revision=int(clip.draft_revision or 0),
            render_artifact_id=artifact_id,
            render_sha256=sha256,
            reviewer=f"person:{requested_by[:80]}",
            status=QAReviewStatus.COMPLETED,
            verdict=QAVerdict.NEEDS_REVISION,
            summary=note[:2000],
            created_at=production.now(),
            completed_at=production.now(),
        )
        with production.transaction() as db:
            production.save_review(db, review, insert=True)
        return human_revision(
            production, batch, clip_id, review.id, review.timeline_revision, note, requested_by
        )

    # ------------------------------------------------------------ status

    def status(self, batch_id: str) -> dict[str, Any]:
        production = self.find(batch_id)
        batch = production.batch(batch_id)
        clips = production.clips(batch_id)
        tasks = production.tasks(batch_id=batch_id)
        counts: dict[str, int] = {}
        for task in tasks:
            counts[task.status.value] = counts.get(task.status.value, 0) + 1
        clip_counts: dict[str, int] = {}
        for clip in clips:
            clip_counts[clip.status.value] = clip_counts.get(clip.status.value, 0) + 1
        spent = production.usage(batch_id)
        return {
            "batch": batch.model_dump(mode="json"),
            "clips": [clip.model_dump(mode="json") for clip in clips],
            "task_counts": counts,
            "clip_counts": clip_counts,
            "tasks": [task.model_dump(mode="json") for task in tasks],
            "candidates": [
                candidate.model_dump(mode="json") for candidate in production.candidates(batch_id)
            ],
            "usage": spent,
            "deliveries": [manifest.model_dump(mode="json") for manifest in production.manifests(batch_id)],
        }


def primary_assets(production: ProductionStore) -> list[str]:
    return [
        asset.id for asset in production.project.active_assets().values() if asset.role is AssetRole.PRIMARY
    ]
