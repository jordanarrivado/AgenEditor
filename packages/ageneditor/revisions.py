"""Apply bounded revision patches and versioned reverts. Never re-ingests or re-transcribes media:
captions are rebuilt from stored transcripts mapped through the new cuts."""

from __future__ import annotations

from ageneditor.captions import build_captions
from ageneditor.contracts import (
    AddOverlay,
    Author,
    BuildCaptions,
    Clip,
    CorrectWord,
    EngineException,
    ErrorCode,
    MoveClip,
    Provenance,
    RemoveClip,
    RevisionPatch,
    RevisionResult,
    SetAudioMix,
    SetCaptionPreset,
    SetClipAudio,
    SetColorPreset,
    SetTransform,
    Timeline,
    TimelineDiff,
    TrackId,
    TrimClip,
    ValidationIssue,
    WordCorrection,
)
from ageneditor.media.ranges import source_interval
from ageneditor.storage import ProjectStore
from ageneditor.timebase import seconds_to_frames_round

_V1_OPS = (TrimClip, RemoveClip, MoveClip)
_CLIP_OPS = (*_V1_OPS, SetTransform, SetClipAudio)


def _output_span(store: ProjectStore, timeline: Timeline, clip: Clip) -> int:
    asset = store.assets().get(clip.asset_id)
    if asset is None:
        raise EngineException(ErrorCode.ASSET_NOT_FOUND, f"asset {clip.asset_id!r} is not registered")
    rate = timeline.render_profile.frame_rate
    start, end = source_interval(asset, clip, store.safe_path(asset.relative_path), rate.fraction)
    return max(1, seconds_to_frames_round(end - start, rate))


def _rebuild(clip: Clip, **changes) -> Clip:
    """Re-validate a clip after a change (``model_copy`` would skip validators)."""
    return Clip.model_validate({**clip.model_dump(mode="json"), **changes})


def _ripple(
    store: ProjectStore, timeline: Timeline, v1_order: list[Clip], overlays: list[Clip]
) -> tuple[list[Clip], list[ValidationIssue]]:
    """Lay V1 back-to-back and carry each overlay with the V1 clip it started over."""
    old_v1 = {c.clip_id: c for c in timeline.clips if c.track is TrackId.V1}
    placed: list[Clip] = []
    new_in: dict[str, int] = {}
    position = 0
    for clip in v1_order:
        span = _output_span(store, timeline, clip)
        clip = _rebuild(clip, timeline_in_frames=position, timeline_duration_frames=span)
        new_in[clip.clip_id] = position
        placed.append(clip)
        position += span
    issues: list[ValidationIssue] = []
    for overlay in overlays:
        anchor = next(
            (
                c
                for c in old_v1.values()
                if c.timeline_in_frames <= overlay.timeline_in_frames < c.timeline_out_frames
            ),
            None,
        )
        if anchor is None or anchor.clip_id not in new_in:
            issues.append(
                ValidationIssue(
                    code=ErrorCode.INVALID_TIMELINE,
                    message="overlay removed because the primary clip under it was removed",
                    clip_id=overlay.clip_id,
                    severity="warning",
                )
            )
            continue
        shifted = new_in[anchor.clip_id] + overlay.timeline_in_frames - anchor.timeline_in_frames
        placed.append(_rebuild(overlay, timeline_in_frames=shifted))
    return placed, issues


def _find(clips: list[Clip], clip_id: str) -> int:
    for index, clip in enumerate(clips):
        if clip.clip_id == clip_id:
            return index
    raise EngineException(ErrorCode.INVALID_ARGUMENT, f"clip {clip_id!r} does not exist")


def apply_patch(store: ProjectStore, patch: RevisionPatch, expected_revision: int) -> RevisionResult:
    if patch.project_id != store.project_id:
        raise EngineException(ErrorCode.PERMISSION_DENIED, "patch belongs to another project")
    current = store.current_revision()
    if current != expected_revision:
        raise EngineException(
            ErrorCode.REVISION_CONFLICT, "project revision changed", current_revision=current
        )
    before = store.timeline(expected_revision)
    v1 = sorted((c for c in before.clips if c.track is TrackId.V1), key=lambda c: c.timeline_in_frames)
    overlays = [c for c in before.clips if c.track is not TrackId.V1]
    corrections = {(f.transcript_id, f.word_id): f for f in before.word_corrections}
    settings: dict[str, object] = {}
    captions_on = bool(before.captions)
    pins = sorted({cue.transcript_id for cue in before.captions if cue.transcript_id})
    captions_touched = False
    v1_touched = False

    for op in patch.ops:
        if isinstance(op, _CLIP_OPS):
            pool = v1 if any(c.clip_id == op.clip_id for c in v1) else overlays
            index = _find(pool, op.clip_id)
            clip = pool[index]
            if isinstance(op, TrimClip):
                pool[index] = _rebuild(
                    clip,
                    source_in_frames=op.source_in_frames,
                    source_out_frames=op.source_out_frames,
                    timeline_duration_frames=None,
                    fade_in_frames=0,
                    fade_out_frames=0,
                )
                if pool is overlays:
                    pool[index] = _rebuild(
                        pool[index], timeline_duration_frames=_output_span(store, before, pool[index])
                    )
            elif isinstance(op, RemoveClip):
                pool.pop(index)
            elif isinstance(op, MoveClip):
                if pool is not v1:
                    raise EngineException(ErrorCode.INVALID_ARGUMENT, "only primary (V1) clips can be moved")
                v1.insert(min(op.to_index, len(v1) - 1), v1.pop(index))
            elif isinstance(op, SetTransform):
                changes = {"transform": op.transform.model_dump(mode="json")}
                if op.fit_mode is not None:
                    changes["fit_mode"] = op.fit_mode.value
                pool[index] = _rebuild(clip, **changes)
            else:
                changes = {
                    key: value
                    for key, value in (
                        ("gain_db", op.gain_db),
                        ("fade_in_frames", op.fade_in_frames),
                        ("fade_out_frames", op.fade_out_frames),
                    )
                    if value is not None
                }
                pool[index] = _rebuild(clip, **changes)
            v1_touched |= pool is v1 and isinstance(op, _V1_OPS)
        elif isinstance(op, AddOverlay):
            clip = op.clip
            if clip.track not in {TrackId.V2, TrackId.A2, TrackId.A3}:
                raise EngineException(
                    ErrorCode.INVALID_ARGUMENT, "add_overlay places only V2, A2 or A3 clips"
                )
            if any(c.clip_id == clip.clip_id for c in [*v1, *overlays]):
                raise EngineException(ErrorCode.INVALID_ARGUMENT, f"clip {clip.clip_id!r} already exists")
            if clip.timeline_duration_frames is None:
                clip = _rebuild(clip, timeline_duration_frames=_output_span(store, before, clip))
            overlays.append(clip)
        elif isinstance(op, CorrectWord):
            transcript = store.transcript(op.transcript_id)
            if all(word.id != op.word_id for word in transcript.words):
                raise EngineException(
                    ErrorCode.TRANSCRIPT_NOT_FOUND, f"word {op.word_id!r} is not in transcript"
                )
            key = (op.transcript_id, op.word_id)
            if op.text is None:
                corrections.pop(key, None)
            else:
                corrections[key] = WordCorrection(
                    transcript_id=op.transcript_id, word_id=op.word_id, text=op.text
                )
            captions_touched = True
        elif isinstance(op, BuildCaptions):
            captions_on = op.enabled
            if op.transcript_ids:
                pins = list(op.transcript_ids)
            captions_touched = True
        elif isinstance(op, SetCaptionPreset):
            settings["caption_preset"] = op.preset.value
        elif isinstance(op, SetColorPreset):
            settings["color_preset"] = op.preset.value
        elif isinstance(op, SetAudioMix):
            settings["audio"] = op.audio.model_dump(mode="json")

    if not v1:
        raise EngineException(ErrorCode.INVALID_TIMELINE, "a revision must keep at least one primary clip")
    clips, issues = _ripple(store, before, v1, overlays)
    # A transition belongs to the cut after its clip; drop any whose cut no longer exists.
    order = sorted((c for c in clips if c.track is TrackId.V1), key=lambda c: c.timeline_in_frames)
    cuts = {clip.clip_id for clip in order[:-1]}
    transitions = [t.model_dump(mode="json") for t in before.transitions if t.after_clip_id in cuts]
    candidate = Timeline.model_validate(
        {
            **before.model_dump(mode="json"),
            **settings,
            "clips": [c.model_dump(mode="json") for c in clips],
            "transitions": transitions,
            "word_corrections": [f.model_dump(mode="json") for f in corrections.values()],
            "captions": [],
            "provenance": Provenance(
                author=Author.AGENT, tool="apply_revision", summary=patch.summary
            ).model_dump(mode="json"),
        }
    )
    if captions_on and (captions_touched or v1_touched or before.captions):
        cues = build_captions(store, candidate, pins or None)
        candidate = Timeline.model_validate(
            {**candidate.model_dump(mode="json"), "captions": [c.model_dump(mode="json") for c in cues]}
        )
    return _commit(store, before, candidate, expected_revision, issues)


def revert_to(store: ProjectStore, target_revision: int, expected_revision: int) -> RevisionResult:
    """Versioned undo: a new revision whose content equals ``target_revision``. History is kept."""
    current = store.current_revision()
    if current != expected_revision:
        raise EngineException(
            ErrorCode.REVISION_CONFLICT, "project revision changed", current_revision=current
        )
    if not 0 <= target_revision < current:
        raise EngineException(ErrorCode.INVALID_ARGUMENT, "revert target must be an earlier revision")
    before = store.timeline(expected_revision)
    target = store.timeline(target_revision)
    candidate = target.model_copy(
        update={
            "provenance": Provenance(
                author=Author.USER, tool="revert_revision", summary=f"Revert to revision {target_revision}"
            )
        }
    )
    return _commit(store, before, candidate, expected_revision, [])


def _commit(
    store: ProjectStore,
    before: Timeline,
    candidate: Timeline,
    expected_revision: int,
    issues: list[ValidationIssue],
) -> RevisionResult:
    issues = [*issues, *store.validation_issues(candidate)]
    errors = [issue for issue in issues if issue.severity == "error"]
    if errors:
        detail = "; ".join(issue.message for issue in errors[:3])
        raise EngineException(ErrorCode.INVALID_TIMELINE, f"revision is invalid: {detail}"[:2000])
    committed = store.commit_timeline(candidate, expected_revision)
    return RevisionResult(timeline=committed, diff=diff_timelines(before, committed), issues=issues)


def diff_timelines(before: Timeline, after: Timeline) -> TimelineDiff:
    old = {clip.clip_id: clip.model_dump(mode="json") for clip in before.clips}
    new = {clip.clip_id: clip.model_dump(mode="json") for clip in after.clips}
    old_fix = {(f.transcript_id, f.word_id): f.text for f in before.word_corrections}
    new_fix = {(f.transcript_id, f.word_id): f.text for f in after.word_corrections}
    settings = [
        name
        for name in ("caption_preset", "color_preset", "audio", "render_profile", "target_duration_frames")
        if getattr(before, name) != getattr(after, name)
    ]
    return TimelineDiff(
        from_revision=before.revision,
        to_revision=after.revision,
        added_clip_ids=sorted(new.keys() - old.keys()),
        removed_clip_ids=sorted(old.keys() - new.keys()),
        changed_clip_ids=sorted(key for key in old.keys() & new.keys() if old[key] != new[key]),
        captions_changed=[c.model_dump(mode="json") for c in before.captions]
        != [c.model_dump(mode="json") for c in after.captions],
        corrected_word_ids=sorted(
            key[1] for key in old_fix.keys() | new_fix.keys() if old_fix.get(key) != new_fix.get(key)
        ),
        settings_changed=settings,
        duration_frames_before=before.duration_frames,
        duration_frames_after=after.duration_frames,
    )
