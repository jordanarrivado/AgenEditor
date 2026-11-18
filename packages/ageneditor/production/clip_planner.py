"""AI Layer 2 — immutable, source-linked clip plans (spec §6).

Plans are validated before a production team gets editing permission: every cut must resolve to an
in-scope source version and contain all of its cited words, the duration must fit the batch policy, and
any external-generation requests must exist in the project checklist (the clip then waits for them).
"""

from __future__ import annotations

from collections.abc import Sequence
from fractions import Fraction

from ageneditor.contracts import (
    AssetRole,
    Batch,
    ClipCandidate,
    ClipPlan,
    ClipPlanDraft,
    ContextSnapshot,
    EngineException,
    ErrorCode,
    EvidenceCitation,
    PassKind,
    PlannedCut,
    ProductionClip,
    ValidationIssue,
)
from ageneditor.contracts.production import REQUIRED_PASSES, QACriterion
from ageneditor.media.probe import video_frame_times
from ageneditor.planning import _source_frame_bounds
from ageneditor.production.common import new_id
from ageneditor.storage import ProjectStore
from ageneditor.timebase import seconds_to_frames_round

#: Accept plans whose output length is within this share of the batch duration range.
DURATION_TOLERANCE = Fraction(1, 10)


def _cut_from_citation(
    store: ProjectStore, snapshot: ContextSnapshot, citation: EvidenceCitation
) -> PlannedCut:
    transcript = store.transcript(citation.transcript_id)
    expected_hash = snapshot.source_asset_hashes.get(transcript.asset_id)
    if expected_hash is None or transcript.source_sha256 != expected_hash:
        raise EngineException(ErrorCode.SOURCE_CHANGED, "cut cites a transcript outside this batch's sources")
    asset = store.assets().get(transcript.asset_id)
    if asset is None or asset.role is not AssetRole.PRIMARY or asset.video is None:
        raise EngineException(ErrorCode.ASSET_NOT_FOUND, "cut cites a missing primary video")
    ids = [word.id for word in transcript.words]
    if citation.first_word_id not in ids or citation.last_word_id not in ids:
        raise EngineException(ErrorCode.INVALID_ARGUMENT, "cut cites a word that is not in the transcript")
    first, last = ids.index(citation.first_word_id), ids.index(citation.last_word_id)
    if first > last:
        raise EngineException(ErrorCode.INVALID_ARGUMENT, "cited word range runs backward")
    cited = transcript.words[first : last + 1]
    start, end = _source_frame_bounds(store, asset, cited[0].start_us, max(word.end_us for word in cited))
    end = min(end, asset.video.duration_frames)
    return PlannedCut(
        asset_id=asset.id,
        transcript_id=transcript.id,
        first_word_id=citation.first_word_id,
        last_word_id=citation.last_word_id,
        source_in_frames=start,
        source_out_frames=max(start + 1, end),
        rationale=citation.rationale,
    )


def output_frames(store: ProjectStore, cut: PlannedCut, profile_rate: Fraction) -> int:
    asset = store.assets()[cut.asset_id]
    if asset.video is None:
        raise EngineException(ErrorCode.ASSET_NOT_FOUND, "cut cites a source without video")
    if asset.video.is_vfr:
        times = video_frame_times(store.safe_path(asset.relative_path))
        end = (
            times[cut.source_out_frames]
            if cut.source_out_frames < len(times)
            else times[-1] + (times[-1] - times[-2] if len(times) > 1 else Fraction(1, 30))
        )
        duration = end - times[cut.source_in_frames]
    else:
        duration = Fraction(cut.source_out_frames - cut.source_in_frames, 1) / asset.video.frame_rate.fraction
    return max(1, seconds_to_frames_round(duration, profile_rate))


def _plan(
    store: ProjectStore,
    batch: Batch,
    clip: ProductionClip,
    snapshot: ContextSnapshot,
    version: int,
    candidate_id: str,
    cuts: Sequence[PlannedCut],
    narrative_goal: str,
    hook: str,
    conditional: Sequence[PassKind],
    asset_request_ids: Sequence[str],
    exceptions: Sequence[str],
    criteria: Sequence[str],
    author: str,
) -> ClipPlan:
    profile = batch.policy.output_profile
    rate = profile.frame_rate.fraction
    expected = sum(output_frames(store, cut, rate) for cut in cuts)
    texts = list(dict.fromkeys([*criteria, *snapshot.required_constraints]))[:20]
    prefix = snapshot.delivery.naming_prefix or "Clip"
    return ClipPlan(
        id=new_id("plan"),
        clip_id=clip.id,
        version=version,
        candidate_id=candidate_id,
        narrative_goal=narrative_goal[:1000] or "Self-contained short clip",
        hook=hook[:500],
        cuts=list(cuts),
        expected_duration_frames=expected,
        output_profile=profile,
        required_passes=list(REQUIRED_PASSES),
        conditional_passes=[item for item in dict.fromkeys(conditional) if item not in REQUIRED_PASSES],
        asset_request_ids=list(dict.fromkeys(asset_request_ids)),
        allowed_exceptions=list(exceptions),
        qa_criteria=[
            QACriterion(id=f"criterion_{n:02d}", text=text[:300]) for n, text in enumerate(texts, 1)
        ],
        delivery_name=f"{prefix}_{batch.sequence:03d}_{clip.ordinal:02d}"[:60],
        creative=snapshot.creative,
        context_hash=snapshot.hash,
        author=author,  # type: ignore[arg-type]
    )


def plan_from_candidate(
    store: ProjectStore,
    batch: Batch,
    clip: ProductionClip,
    candidate: ClipCandidate,
    snapshot: ContextSnapshot,
    version: int,
) -> ClipPlan:
    """Deterministic plan: the candidate's cited passages in source order, every required pass."""
    cuts = [
        _cut_from_citation(
            store,
            snapshot,
            EvidenceCitation(
                transcript_id=evidence.transcript_id,
                first_word_id=evidence.first_word_id,
                last_word_id=evidence.last_word_id,
                rationale=candidate.rationale or f"Cited passage: {candidate.topic}"[:1000],
            ),
        )
        for evidence in candidate.evidence
    ]
    has_broll = any(
        asset.role is AssetRole.BROLL and asset.labels for asset in store.active_assets().values()
    )
    return _plan(
        store,
        batch,
        clip,
        snapshot,
        version,
        candidate.id,
        cuts,
        narrative_goal=f"Self-contained clip: {candidate.topic}",
        hook=candidate.hook_text,
        conditional=[PassKind.BROLL] if has_broll else [],
        asset_request_ids=[],
        exceptions=[],
        criteria=["Clip is self-contained and starts on its hook", "Captions match the spoken words"],
        author="engine",
    )


def plan_from_draft(
    store: ProjectStore,
    batch: Batch,
    clip: ProductionClip,
    draft: ClipPlanDraft,
    snapshot: ContextSnapshot,
    version: int,
) -> ClipPlan:
    cuts = [_cut_from_citation(store, snapshot, citation) for citation in draft.cuts]
    return _plan(
        store,
        batch,
        clip,
        snapshot,
        version,
        clip.candidate_id,
        cuts,
        narrative_goal=draft.narrative_goal,
        hook=draft.hook,
        conditional=draft.conditional_passes,
        asset_request_ids=draft.asset_request_ids,
        exceptions=draft.allowed_exceptions,
        criteria=draft.qa_criteria,
        author="agent",
    )


def validate_plan(
    store: ProjectStore, batch: Batch, plan: ClipPlan, snapshot: ContextSnapshot
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    def add(code: ErrorCode, message: str, severity: str = "error") -> None:
        issues.append(ValidationIssue(code=code, message=message, severity=severity))  # type: ignore[arg-type]

    if snapshot.conflicts:
        add(
            ErrorCode.INVALID_ARGUMENT,
            "instruction layers conflict; a person must resolve: "
            + "; ".join(conflict.message for conflict in snapshot.conflicts[:3]),
        )
    assets = store.assets()
    for index, cut in enumerate(plan.cuts, start=1):
        asset = assets.get(cut.asset_id)
        if asset is None or asset.video is None or snapshot.source_asset_hashes.get(asset.id) != asset.sha256:
            add(
                ErrorCode.SOURCE_CHANGED, f"cut {index} cites a source outside this batch or a changed source"
            )
            continue
        if cut.source_out_frames > asset.video.duration_frames:
            add(ErrorCode.RANGE_OUT_OF_BOUNDS, f"cut {index} exceeds its source")
            continue
        try:
            rebuilt = _cut_from_citation(
                store,
                snapshot,
                EvidenceCitation(
                    transcript_id=cut.transcript_id,
                    first_word_id=cut.first_word_id,
                    last_word_id=cut.last_word_id,
                ),
            )
        except EngineException as exc:
            add(exc.error.code, f"cut {index}: {exc.error.message}")
            continue
        if (
            cut.source_in_frames > rebuilt.source_in_frames
            or cut.source_out_frames < rebuilt.source_out_frames
        ):
            add(ErrorCode.RANGE_OUT_OF_BOUNDS, f"cut {index} does not contain all of its cited speech")
    rate = plan.output_profile.frame_rate.fraction
    low = Fraction(batch.policy.min_duration_us, 1_000_000) * rate * (1 - DURATION_TOLERANCE)
    high = Fraction(batch.policy.max_duration_us, 1_000_000) * rate * (1 + DURATION_TOLERANCE)
    if not low <= plan.expected_duration_frames <= high:
        add(
            ErrorCode.INVALID_TIMELINE,
            f"planned length {plan.expected_duration_frames} frames is outside the batch duration range",
        )
    known = {request.id for request in store.asset_checklist().requests}
    for request_id in plan.asset_request_ids:
        if request_id not in known:
            add(ErrorCode.INVALID_ARGUMENT, f"asset request {request_id!r} is not in the project checklist")
    if plan.context_hash != snapshot.hash:
        add(ErrorCode.INVALID_ARGUMENT, "plan was built for a different context snapshot")
    return issues
