"""Variants of the main edit (roadmap Phase 2c): overrides on a shared body, computed per main revision.

See ``contracts/variant.py`` for the override model. Nothing here copies or edits the main timeline: a
variant's timeline is computed from a main revision plus the variant's overrides, validated with the same
asset rules, and rendered through the normal renderer. Variant revisions are immutable rows.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from ageneditor.contracts import (
    EngineException,
    ErrorCode,
    Timeline,
    TrackId,
)
from ageneditor.contracts.timeline import Author, Provenance, ValidationIssue
from ageneditor.contracts.variant import (
    MAX_VARIANTS,
    Variant,
    VariantKind,
    VariantOverrides,
    VariantView,
)
from ageneditor.storage import ProjectStore


def _issue(message: str, severity: str = "error", clip_id: str | None = None) -> ValidationIssue:
    return ValidationIssue(
        code=ErrorCode.INVALID_TIMELINE, message=message, severity=severity, clip_id=clip_id
    )


def materialize(
    store: ProjectStore, main: Timeline, variant: Variant
) -> tuple[Timeline | None, list[ValidationIssue], list[str], int]:
    """Compute a variant's timeline from one main revision.

    Returns the timeline (or ``None`` when the overrides do not fit this main revision), the issues, the
    main clips left out because they sat in the replaced hook, and how many main captions were left out.
    """
    from ageneditor.captions import build_captions

    overrides = variant.overrides
    issues: list[ValidationIssue] = []
    v1 = sorted((c for c in main.clips if c.track is TrackId.V1), key=lambda c: c.timeline_in_frames)
    count = overrides.hook_replaces_clips
    if count > len(v1):
        issues.append(_issue(f"the hook replaces {count} clips but the main edit has only {len(v1)}"))
        return None, issues, [], 0
    replaced = {clip.clip_id for clip in v1[:count]}
    old_length = sum(clip.timeline_span_frames for clip in v1[:count])
    main_ids = {clip.clip_id for clip in main.clips}
    for clip in [*overrides.hook_clips, *overrides.hook_overlays]:
        if clip.clip_id in main_ids:
            message = f"clip ID {clip.clip_id!r} is already used by the main edit"
            issues.append(_issue(message, clip_id=clip.clip_id))
    if issues:
        return None, issues, [], 0

    hook = []
    position = 0
    for clip in overrides.hook_clips:
        hook.append(clip.model_copy(update={"timeline_in_frames": position}))
        position += clip.timeline_span_frames
    delta = position - old_length

    clips = list(hook)
    dropped: list[str] = []
    used_swaps: set[str] = set()
    for clip in main.clips:
        if clip.clip_id in replaced:
            continue
        if clip.track is not TrackId.V1 and clip.timeline_in_frames < old_length:
            # Overlays that start inside the replaced hook belong to its content; a music bed that
            # starts there keeps playing from where it was.
            if clip.track is not TrackId.A2:
                dropped.append(clip.clip_id)
                continue
            moved = clip
        else:
            moved = clip.model_copy(update={"timeline_in_frames": clip.timeline_in_frames + delta})
        if clip.track is not TrackId.V1 and clip.asset_id in overrides.asset_swaps:
            used_swaps.add(clip.asset_id)
            moved = moved.model_copy(update={"asset_id": overrides.asset_swaps[clip.asset_id]})
        clips.append(moved)
    clips.extend(overrides.hook_overlays)
    for source in sorted(set(overrides.asset_swaps) - used_swaps):
        issues.append(_issue(f"asset swap for {source!r} matches no overlay in the main edit", "warning"))

    captions = []
    dropped_captions = 0
    if overrides.captions == "keep":
        for cue in main.captions:
            if cue.timeline_in_frames < old_length:
                dropped_captions += 1
                continue
            captions.append(
                cue.model_copy(
                    update={
                        "timeline_in_frames": cue.timeline_in_frames + delta,
                        "timeline_out_frames": cue.timeline_out_frames + delta,
                    }
                )
            )
        if main.captions and hook:
            # The new hook gets captions built from its own transcript words, like the main edit's.
            hook_only = main.model_copy(update={"clips": hook, "captions": [], "transitions": []})
            try:
                cue_ids = {cue.cue_id for cue in captions}
                for cue in build_captions(store, hook_only):
                    cue_id = f"hook_{cue.cue_id}"
                    if cue_id not in cue_ids:
                        captions.append(cue.model_copy(update={"cue_id": cue_id}))
            except EngineException:
                issues.append(_issue("the new hook has no captions: its source has no transcript", "warning"))

    transitions = [t for t in main.transitions if t.after_clip_id not in replaced]
    try:
        timeline = Timeline.model_validate(
            {
                **main.model_dump(mode="json"),
                "revision": variant.revision,
                "parent_revision": None,
                "target_duration_frames": max(1, main.target_duration_frames + delta),
                "clips": [clip.model_dump(mode="json") for clip in clips],
                "captions": [cue.model_dump(mode="json") for cue in captions],
                "transitions": [t.model_dump(mode="json") for t in transitions],
                "provenance": Provenance(
                    author=Author.SYSTEM,
                    tool="variant",
                    summary=f"variant {variant.id} r{variant.revision} on main r{main.revision}",
                ).model_dump(mode="json"),
            }
        )
    except ValidationError as exc:
        issues.extend(_issue(error["msg"]) for error in exc.errors()[:10])
        return None, issues, dropped, dropped_captions
    issues.extend(store.validation_issues(timeline))
    issues.extend(store.removed_asset_issues(timeline))
    return timeline, issues, dropped, dropped_captions


class VariantService:
    def __init__(self, store: ProjectStore):
        self.store = store

    def _rows(self, variant_id: str | None = None) -> list[Variant]:
        with self.store._connect() as db:
            if variant_id is None:
                rows = db.execute(
                    "SELECT data FROM variants v WHERE revision=(SELECT MAX(revision) FROM variants w "
                    "WHERE w.id=v.id) ORDER BY created_at, id"
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT data FROM variants WHERE id=? ORDER BY revision", (variant_id,)
                ).fetchall()
        return [Variant.model_validate_json(row[0]) for row in rows]

    def list(self) -> list[Variant]:
        return self._rows()

    def get(self, variant_id: str, revision: int | None = None) -> Variant:
        history = self._rows(variant_id)
        if not history:
            raise EngineException(ErrorCode.INVALID_ARGUMENT, f"variant {variant_id!r} does not exist")
        if revision is None:
            return history[-1]
        for item in history:
            if item.revision == revision:
                return item
        raise EngineException(
            ErrorCode.INVALID_ARGUMENT, f"variant {variant_id!r} has no revision {revision}"
        )

    def history(self, variant_id: str) -> list[Variant]:
        self.get(variant_id)
        return self._rows(variant_id)

    def view(
        self, variant_id: str, main_revision: int | None = None, variant_revision: int | None = None
    ) -> VariantView:
        variant = self.get(variant_id, variant_revision)
        main = self.store.timeline(main_revision)
        timeline, issues, dropped, dropped_captions = materialize(self.store, main, variant)
        return VariantView(
            variant=variant,
            main_revision=main.revision,
            timeline=timeline,
            issues=issues,
            dropped_clip_ids=dropped,
            dropped_caption_count=dropped_captions,
            pending_requests=self.store.pending_asset_requests(variant_id),
        )

    def _insert(self, db: Any, variant: Variant) -> None:
        db.execute(
            "INSERT INTO variants VALUES (?, ?, ?, ?)",
            (variant.id, variant.revision, variant.model_dump_json(), datetime.now(UTC).isoformat()),
        )
        self.store.write_artifact(
            f"variants/{variant.id}/r{variant.revision}.json", variant.model_dump_json(indent=2)
        )

    def create(
        self,
        name: str,
        kind: VariantKind,
        language: str | None = None,
        note: str = "",
        author: Author = Author.AGENT,
    ) -> Variant:
        """A new variant starts as an exact copy of the main edit (no overrides)."""
        self.store.timeline()  # the main edit must exist
        variant = Variant(
            id=f"variant_{uuid.uuid4().hex[:12]}",
            project_id=self.store.project_id,
            name=name,
            kind=kind,
            language=language,
            note=note,
            revision=1,
            provenance=Provenance(author=author, tool="create_variant", summary=f"created {kind} variant"),
        )
        with self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("SELECT COUNT(DISTINCT id) FROM variants").fetchone()[0]
            if count >= MAX_VARIANTS:
                raise EngineException(
                    ErrorCode.INVALID_ARGUMENT, f"a project can have at most {MAX_VARIANTS} variants"
                )
            self._insert(db, variant)
        return variant

    def update(
        self,
        variant_id: str,
        overrides: VariantOverrides,
        expected_revision: int,
        summary: str = "",
        author: Author = Author.AGENT,
    ) -> VariantView:
        """Save new overrides as the next revision, if they fit the current main edit."""
        overrides = VariantOverrides.model_validate(overrides.model_dump(mode="json"))
        with self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self.get(variant_id)
            if current.revision != expected_revision:
                raise EngineException(
                    ErrorCode.REVISION_CONFLICT,
                    "variant revision changed",
                    current_revision=current.revision,
                )
            candidate = current.model_copy(
                update={
                    "revision": current.revision + 1,
                    "overrides": overrides,
                    "provenance": Provenance(author=author, tool="update_variant", summary=summary[:2000]),
                    "created_at": datetime.now(UTC),
                }
            )
            main = self.store.timeline()
            timeline, issues, dropped, dropped_captions = materialize(self.store, main, candidate)
            errors = [issue for issue in issues if issue.severity == "error"]
            if timeline is None or errors:
                raise EngineException(
                    ErrorCode.INVALID_TIMELINE,
                    "variant does not fit the main edit: " + "; ".join(issue.message for issue in errors[:5]),
                    issues=len(errors),
                )
            self._insert(db, candidate)
        return VariantView(
            variant=candidate,
            main_revision=main.revision,
            timeline=timeline,
            issues=issues,
            dropped_clip_ids=dropped,
            dropped_caption_count=dropped_captions,
            pending_requests=self.store.pending_asset_requests(variant_id),
        )


class PinnedTimelineStore:
    """A project store whose ``timeline()`` returns one fixed timeline (a computed variant), so technical
    QA measures a variant render against the timeline it was rendered from."""

    def __init__(self, store: ProjectStore, timeline: Timeline):
        self._store = store
        self._timeline = timeline

    def timeline(self, revision: int | None = None) -> Timeline:
        return self._timeline

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)
