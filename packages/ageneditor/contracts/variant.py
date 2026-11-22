"""Variant contract (roadmap Phase 2c): alternative versions of the main edit that share its body.

A variant does not copy the main timeline. It stores *overrides*, and its timeline is computed from the
main edit's current revision each time, so changes to the shared body reach every variant:

* a replacement hook: the first ``hook_replaces_clips`` V1 clips of the main edit are replaced by
  ``hook_clips`` (laid out from frame 0); everything after the hook shifts by the length difference;
* ``asset_swaps``: overlay assets (V2, A2, A3, A4) replaced by another registered asset, for localized shots
  or voice lines in another language;
* ``hook_overlays``: overlays placed in the variant's own output time, usually over the new hook;
* ``captions``: keep the main captions (the hook gets captions built from its transcript words) or turn
  them off, e.g. for a language variant whose captions would be in the wrong language.

Variant revisions are immutable like timeline revisions; each change is a new revision.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from .common import ContractModel, Identifier, utcnow
from .timeline import Clip, Provenance, Timeline, TrackId, ValidationIssue

#: Keeps a project's variant list and each render bounded.
MAX_VARIANTS = 20
MAX_HOOK_CLIPS = 20
MAX_HOOK_OVERLAYS = 50
MAX_ASSET_SWAPS = 50
SWAPPABLE_TRACKS = {TrackId.V2, TrackId.A2, TrackId.A3, TrackId.A4}


class VariantKind(StrEnum):
    HOOK = "hook"
    LANGUAGE = "language"
    LOCALIZED = "localized"
    OTHER = "other"


#: A BCP 47-style language tag such as ``es`` or ``pt-BR``.
LanguageTag = Annotated[str, StringConstraints(pattern=r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8}){0,3}$")]


class VariantOverrides(ContractModel):
    hook_replaces_clips: int = Field(default=0, ge=0, le=MAX_HOOK_CLIPS)
    #: V1 clips for the new hook, in playback order. Their timeline positions are ignored and laid out
    #: back to back from frame 0.
    hook_clips: list[Clip] = Field(default_factory=list, max_length=MAX_HOOK_CLIPS)
    hook_overlays: list[Clip] = Field(default_factory=list, max_length=MAX_HOOK_OVERLAYS)
    asset_swaps: dict[Identifier, Identifier] = Field(default_factory=dict, max_length=MAX_ASSET_SWAPS)
    captions: Literal["keep", "off"] = "keep"

    @model_validator(mode="after")
    def _shape(self) -> VariantOverrides:
        if any(clip.track is not TrackId.V1 for clip in self.hook_clips):
            raise ValueError("hook_clips are V1 clips")
        if self.hook_replaces_clips and not self.hook_clips:
            raise ValueError("a hook that replaces main clips needs hook_clips")
        if any(clip.track not in SWAPPABLE_TRACKS for clip in self.hook_overlays):
            raise ValueError("hook_overlays are V2, A2, A3 or A4 clips")
        ids = [clip.clip_id for clip in [*self.hook_clips, *self.hook_overlays]]
        if len(ids) != len(set(ids)):
            raise ValueError("hook clip IDs must be unique")
        if any(source == target for source, target in self.asset_swaps.items()):
            raise ValueError("an asset swap must name a different asset")
        return self


class Variant(ContractModel):
    schema_version: Literal[1] = 1
    id: Identifier
    project_id: Identifier
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    kind: VariantKind
    language: LanguageTag | None = None
    note: str = Field(default="", max_length=500)
    revision: int = Field(ge=1)
    overrides: VariantOverrides = Field(default_factory=VariantOverrides)
    provenance: Provenance
    created_at: datetime = Field(default_factory=utcnow)


class VariantView(ContractModel):
    """A variant computed against one main revision, with what changed and whether it can render."""

    variant: Variant
    main_revision: int = Field(ge=0)
    #: ``None`` when the overrides no longer fit the main edit (see ``issues``).
    timeline: Timeline | None = None
    issues: list[ValidationIssue] = Field(default_factory=list)
    #: Main clips left out because they sat in the replaced hook (music beds are kept).
    dropped_clip_ids: list[Identifier] = Field(default_factory=list)
    dropped_caption_count: int = Field(default=0, ge=0)
    #: Asset requests for this variant (or the main edit) that are not ready yet.
    pending_requests: list[Identifier] = Field(default_factory=list)

    @property
    def renderable(self) -> bool:
        return (
            self.timeline is not None
            and not self.pending_requests
            and not any(issue.severity == "error" for issue in self.issues)
        )
