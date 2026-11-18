"""Scoped production tools for AI workers (spec §9, §17).

Agents never name a project, batch or clip: everything is resolved from the per-attempt capability token,
which the engine checks on every call (role, tool allowlist, live lease, fencing token for writes). The
tool surface deliberately excludes leasing, approval, delivery and administration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter

from ageneditor.contracts import (
    CandidateProposal,
    ClipPlanDraft,
    EngineException,
    ErrorCode,
    QAFindingSubmission,
    RevisionOp,
    RevisionPatch,
    Timeline,
)
from ageneditor.orchestration.capabilities import Scope, token_project, verify_token
from ageneditor.production.clip_timeline import ClipTimelineStore
from ageneditor.production.store import ProductionStore

if TYPE_CHECKING:
    from ageneditor.orchestration.orchestrator import ProductionOrchestrator

_OPS = TypeAdapter(list[RevisionOp])
_PROPOSALS = TypeAdapter(list[CandidateProposal])
MAX_TRANSCRIPT_PAGE = 300


class ScopedToolService:
    def __init__(self, orchestrator: ProductionOrchestrator):
        self.orchestrator = orchestrator

    def resolve(self, token: str, tool: str) -> tuple[ProductionStore, Scope]:
        production = self.orchestrator.production(token_project(token))
        scope = verify_token(production, token)
        scope.require(tool)
        return production, scope

    def call(self, tool: str, args: dict[str, Any], token: str) -> dict[str, Any]:
        production, scope = self.resolve(token, tool)
        handler = getattr(self, f"_{tool}", None)
        if handler is None:
            raise EngineException(ErrorCode.PERMISSION_DENIED, "unknown production tool")
        with production.transaction() as db:
            production.emit(db, "agent_tool_call", "attempt", scope.attempt_id, tool=tool)
        return handler(production, scope, args)

    # ------------------------------------------------------------ shared

    def _clip_store(self, production: ProductionStore, scope: Scope) -> ClipTimelineStore:
        if scope.clip_id is None:
            raise EngineException(ErrorCode.PERMISSION_DENIED, "this task has no clip")
        attempt = production.attempt(scope.attempt_id)
        return ClipTimelineStore(production, scope.clip_id, scope.attempt_id, attempt.fencing_token)

    def _snapshot(self, production: ProductionStore, scope: Scope):
        if scope.clip_id is not None:
            clip = production.clip(scope.clip_id)
            if clip.plan_id is not None:
                return production.snapshot(production.plan(clip.plan_id).context_hash)
        return production.snapshot(production.batch(scope.batch_id).context_hash)

    def _production_get_assignment(
        self, production: ProductionStore, scope: Scope, args: dict[str, Any]
    ) -> dict[str, Any]:
        del args
        task = production.task(scope.task_id)
        batch = production.batch(scope.batch_id)
        snapshot = self._snapshot(production, scope)
        assets = production.project.assets()
        out: dict[str, Any] = {
            "task": {"id": task.id, "type": task.type.value, "cycle": task.cycle, "params": task.params},
            "role": scope.role.value,
            "allowed_tools": list(scope.tools),
            "context": {
                "engine_invariants": snapshot.engine_invariants,
                "instructions": snapshot.instructions,
                "required_constraints": snapshot.required_constraints,
                "creative": snapshot.creative.model_dump(mode="json"),
                "conflicts": [c.model_dump(mode="json") for c in snapshot.conflicts],
            },
            "batch_policy": {
                "min_duration_us": batch.policy.min_duration_us,
                "max_duration_us": batch.policy.max_duration_us,
                "target_mode": batch.policy.target_mode.value,
                "target_count": batch.policy.target_count,
                "platform": batch.policy.platform,
                "audience": batch.policy.audience,
                "exclusions": batch.policy.exclusions,
            },
            "sources": [
                {
                    "asset_id": asset_id,
                    "sha256": sha,
                    "transcript_id": (
                        t.id if (t := production.project.latest_transcript(asset_id, sha)) else None
                    ),
                    "duration_frames": assets[asset_id].video.duration_frames
                    if assets[asset_id].video
                    else None,
                }
                for asset_id, sha in snapshot.source_asset_hashes.items()
            ],
        }
        if scope.clip_id is not None:
            clip = production.clip(scope.clip_id)
            out["clip"] = {
                "id": clip.id,
                "title": clip.title,
                "status": clip.status.value,
                "current_revision": clip.current_revision,
            }
            candidate = next(
                (c for c in production.candidates(clip.batch_id) if c.id == clip.candidate_id), None
            )
            if candidate is not None:
                out["candidate"] = candidate.model_dump(mode="json")
            if clip.plan_id is not None:
                out["plan"] = production.plan(clip.plan_id).model_dump(mode="json")
        return out

    def _production_get_transcript(
        self, production: ProductionStore, scope: Scope, args: dict[str, Any]
    ) -> dict[str, Any]:
        transcript = production.project.transcript(str(args["transcript_id"]))
        sources = self._snapshot(production, scope).source_asset_hashes
        if sources.get(transcript.asset_id) != transcript.source_sha256:
            raise EngineException(
                ErrorCode.PERMISSION_DENIED, "this transcript is outside your task's sources"
            )
        start = max(0, int(args.get("start_word", 0)))
        limit = max(1, min(int(args.get("limit", 200)), MAX_TRANSCRIPT_PAGE))
        words = transcript.words[start : start + limit]
        return {
            "transcript_id": transcript.id,
            "asset_id": transcript.asset_id,
            "total_words": len(transcript.words),
            "start_word": start,
            "has_more": start + limit < len(transcript.words),
            "words": [w.model_dump(mode="json") for w in words],
        }

    def _production_task_complete(
        self, production: ProductionStore, scope: Scope, args: dict[str, Any]
    ) -> dict[str, Any]:
        claim = {
            "summary": str(args.get("summary", ""))[:1000],
            "no_op": bool(args.get("no_op", False)),
            "reason": str(args.get("reason", ""))[:500],
        }
        self.orchestrator.claims.setdefault(scope.attempt_id, {}).update(claim)
        with production.transaction() as db:
            production.emit(db, "task_completion_claimed", "attempt", scope.attempt_id, no_op=claim["no_op"])
        return {
            "recorded": True,
            "note": "The engine verifies the clip's state before the task counts as done.",
        }

    # ------------------------------------------------------------ discovery and planning

    def _discovery_submit_candidates(
        self, production: ProductionStore, scope: Scope, args: dict[str, Any]
    ) -> dict[str, Any]:
        from ageneditor.production.clip_discovery import validate_proposals

        proposals = _PROPOSALS.validate_python(args.get("proposals", []))
        batch = production.batch(scope.batch_id)
        report, rejected = validate_proposals(
            production.project, batch, production.snapshot(batch.context_hash), proposals
        )
        with production.transaction() as db:
            production.check_live_attempt(db, scope.attempt_id)
            production.replace_candidates(db, batch.id, report.candidates)
            production.emit(
                db,
                "candidates_discovered",
                "batch",
                batch.id,
                method="agent",
                viable=report.viable_count,
                rejected=len(rejected),
            )
        self.orchestrator.claims.setdefault(scope.attempt_id, {}).update(
            {
                "method": "agent",
                "candidate_ids": [c.id for c in report.candidates],
                "viable_count": report.viable_count,
                "recommended_count": report.recommended_count,
                "shortfall": report.shortfall,
                "notes": report.notes,
                "rejected": rejected,
            }
        )
        return {"report": report.model_dump(mode="json"), "rejected": rejected}

    def _plan_submit(self, production: ProductionStore, scope: Scope, args: dict[str, Any]) -> dict[str, Any]:
        from ageneditor.production.clip_planner import plan_from_draft, validate_plan
        from ageneditor.production.context_resolver import batch_context

        draft = ClipPlanDraft.model_validate(args.get("draft", {}))
        clip = production.clip(str(scope.clip_id))
        if clip.plan_id is not None:
            raise EngineException(ErrorCode.INVALID_STATE_TRANSITION, "this clip already has a plan")
        batch = production.batch(clip.batch_id)
        snapshot = batch_context(
            self.orchestrator.registry,
            production.project,
            batch,
            production.snapshot(batch.context_hash),
            clip,
        )
        with production.connect() as db:
            version = production.next_plan_version(db, clip.id)
        plan = plan_from_draft(production.project, batch, clip, draft, snapshot, version)
        issues = validate_plan(production.project, batch, plan, snapshot)
        if any(issue.severity == "error" for issue in issues):
            return {"valid": False, "issues": [issue.model_dump(mode="json") for issue in issues]}
        with production.transaction() as db:
            production.check_live_attempt(db, scope.attempt_id)
            production.save_snapshot(db, snapshot)
            production.save_plan(db, plan)
            self.orchestrator.on_plan_saved(production, db, scope.task_id, clip.id)
        production.create_clip_timeline(clip.id, plan.output_profile, plan.expected_duration_frames)
        self.orchestrator.claims.setdefault(scope.attempt_id, {})["plan_id"] = plan.id
        return {
            "valid": True,
            "plan": plan.model_dump(mode="json"),
            "issues": [issue.model_dump(mode="json") for issue in issues],
        }

    # ------------------------------------------------------------ team editing

    def _clip_get_timeline(
        self, production: ProductionStore, scope: Scope, args: dict[str, Any]
    ) -> dict[str, Any]:
        revision = args.get("revision")
        if scope.role.value == "qa":
            from ageneditor.quality.editorial_review import review_for_task

            pinned = review_for_task(production, production.task(scope.task_id)).timeline_revision
            if revision is not None and int(revision) != pinned:
                raise EngineException(
                    ErrorCode.PERMISSION_DENIED, "reviewers read only the reviewed revision"
                )
            revision = pinned
        return production.clip_timeline(
            str(scope.clip_id), None if revision is None else int(revision)
        ).model_dump(mode="json")

    def _clip_validate_timeline(
        self, production: ProductionStore, scope: Scope, args: dict[str, Any]
    ) -> dict[str, Any]:
        del scope
        timeline = Timeline.model_validate(args["timeline"])
        issues = [
            *production.project.validation_issues(timeline),
            *production.project.removed_asset_issues(timeline),
        ]
        return {
            "valid": not any(i.severity == "error" for i in issues),
            "issues": [i.model_dump(mode="json") for i in issues],
        }

    def _clip_apply_revision(
        self, production: ProductionStore, scope: Scope, args: dict[str, Any]
    ) -> dict[str, Any]:
        from ageneditor.revisions import apply_patch

        store = self._clip_store(production, scope)
        patch = RevisionPatch(
            project_id=production.project_id,
            summary=str(args.get("summary", "Production team edit"))[:500],
            ops=_OPS.validate_python(args.get("ops", [])),
        )
        return apply_patch(store, patch, int(args["expected_revision"])).model_dump(mode="json")  # type: ignore[arg-type]

    def _clip_commit_timeline(
        self, production: ProductionStore, scope: Scope, args: dict[str, Any]
    ) -> dict[str, Any]:
        from ageneditor.transitions import TransitionLibrary

        store = self._clip_store(production, scope)
        timeline = Timeline.model_validate(args["timeline"])
        TransitionLibrary(self.orchestrator.workspace).check_new_transitions(
            store.timeline(store.current_revision()), timeline
        )
        return store.commit_timeline(timeline, int(args["expected_revision"])).model_dump(mode="json")

    def _clip_suggest_broll(
        self, production: ProductionStore, scope: Scope, args: dict[str, Any]
    ) -> dict[str, Any]:
        from ageneditor.broll import METHOD, suggest_broll

        store = self._clip_store(production, scope)
        timeline = store.timeline(store.current_revision())
        limit = max(1, min(int(args.get("limit", 5)), 20))
        return {"method": METHOD, "suggestions": [s.to_dict() for s in suggest_broll(store, timeline, limit)]}  # type: ignore[arg-type]

    def _production_list_transitions(
        self, production: ProductionStore, scope: Scope, args: dict[str, Any]
    ) -> dict[str, Any]:
        del production, scope, args
        from ageneditor.transitions import TransitionLibrary

        return TransitionLibrary(self.orchestrator.workspace).catalog()

    # ------------------------------------------------------------ centralized QA

    def _qa_get_review_packet(
        self, production: ProductionStore, scope: Scope, args: dict[str, Any]
    ) -> dict[str, Any]:
        del args
        from ageneditor.quality.editorial_review import review_packet

        return review_packet(production, production.task(scope.task_id))

    def _qa_submit_findings(
        self, production: ProductionStore, scope: Scope, args: dict[str, Any]
    ) -> dict[str, Any]:
        from ageneditor.quality.editorial_review import submit_findings

        submission = QAFindingSubmission.model_validate(args.get("submission", {}))
        review = submit_findings(production, production.task(scope.task_id), scope.attempt_id, submission)
        self.orchestrator.claims.setdefault(scope.attempt_id, {})["review_id"] = review.id
        return {
            "review_id": review.id,
            "verdict": review.verdict.value if review.verdict else None,
            "note": "Findings recorded. The engine decides what happens next; you cannot edit the clip.",
        }
