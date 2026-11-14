"""Bounded, typed revision patches (spec §7 step 11). Natural language is translated by the agent;
the engine only accepts these operations and always produces a new immutable revision."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from .common import ContractModel, Identifier
from .timeline import (
    AudioMix,
    CaptionPreset,
    Clip,
    ColorPreset,
    FitMode,
    Timeline,
    Transform,
    ValidationIssue,
)


class TrimClip(ContractModel):
    op: Literal["trim_clip"] = "trim_clip"
    clip_id: Identifier
    source_in_frames: int = Field(ge=0)
    source_out_frames: int = Field(gt=0)


class RemoveClip(ContractModel):
    op: Literal["remove_clip"] = "remove_clip"
    clip_id: Identifier


class MoveClip(ContractModel):
    """Reorder a primary (V1) clip; later V1 clips ripple so the track stays gap-free."""

    op: Literal["move_clip"] = "move_clip"
    clip_id: Identifier
    to_index: int = Field(ge=0, le=99)


class SetTransform(ContractModel):
    """Punch-in / crop controls. Bounded by ``Transform`` (scale 1.0-2.0, centre 0-1)."""

    op: Literal["set_transform"] = "set_transform"
    clip_id: Identifier
    transform: Transform
    fit_mode: FitMode | None = None


class SetClipAudio(ContractModel):
    op: Literal["set_clip_audio"] = "set_clip_audio"
    clip_id: Identifier
    gain_db: float | None = Field(default=None, ge=-60, le=12)
    fade_in_frames: int | None = Field(default=None, ge=0)
    fade_out_frames: int | None = Field(default=None, ge=0)


class AddOverlay(ContractModel):
    """Place a registered B-roll (V2), music (A2) or sound-effect (A3) asset. V1 uses plans and trims."""

    op: Literal["add_overlay"] = "add_overlay"
    clip: Clip


class CorrectWord(ContractModel):
    """Fix one word's caption text. ``text=None`` removes an earlier correction."""

    op: Literal["correct_word"] = "correct_word"
    transcript_id: Identifier
    word_id: Identifier
    text: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[^\x00-\x1f\x7f]+$")


class BuildCaptions(ContractModel):
    """(Re)derive captions from transcript words mapped through the current cuts.

    ``transcript_ids`` pins which transcript to use per primary asset; otherwise the newest transcript
    of the unchanged source is used. ``enabled=False`` removes captions.
    """

    op: Literal["build_captions"] = "build_captions"
    enabled: bool = True
    transcript_ids: list[Identifier] = Field(default_factory=list, max_length=20)


class SetCaptionPreset(ContractModel):
    op: Literal["set_caption_preset"] = "set_caption_preset"
    preset: CaptionPreset


class SetColorPreset(ContractModel):
    op: Literal["set_color_preset"] = "set_color_preset"
    preset: ColorPreset


class SetAudioMix(ContractModel):
    op: Literal["set_audio_mix"] = "set_audio_mix"
    audio: AudioMix


RevisionOp = Annotated[
    TrimClip
    | RemoveClip
    | MoveClip
    | SetTransform
    | SetClipAudio
    | AddOverlay
    | CorrectWord
    | BuildCaptions
    | SetCaptionPreset
    | SetColorPreset
    | SetAudioMix,
    Field(discriminator="op"),
]


class RevisionPatch(ContractModel):
    project_id: Identifier
    #: Human-readable intent, e.g. the user's request as understood by the agent.
    summary: str = Field(min_length=3, max_length=500)
    ops: list[RevisionOp] = Field(min_length=1, max_length=20)


class TimelineDiff(ContractModel):
    from_revision: int = Field(ge=0)
    to_revision: int = Field(ge=0)
    added_clip_ids: list[str] = Field(default_factory=list)
    removed_clip_ids: list[str] = Field(default_factory=list)
    changed_clip_ids: list[str] = Field(default_factory=list)
    captions_changed: bool = False
    corrected_word_ids: list[str] = Field(default_factory=list)
    settings_changed: list[str] = Field(default_factory=list)
    duration_frames_before: int = Field(ge=0)
    duration_frames_after: int = Field(ge=0)


class RevisionResult(ContractModel):
    timeline: Timeline
    diff: TimelineDiff
    issues: list[ValidationIssue] = Field(default_factory=list)
