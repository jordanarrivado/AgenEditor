"""Timeline contract (spec §5.2): versioned, source-linked, renderable only from validated data."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from itertools import pairwise
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from ageneditor.timebase import seconds_to_frames_round

from .asset import Asset, AssetKind, AssetRole
from .common import SCHEMA_VERSION, ContractModel, EngineError, ErrorCode, Identifier, utcnow
from .render_profile import RenderProfile
from .transition import Transition


class TrackId(StrEnum):
    V1 = "V1"  # primary speaker video
    V2 = "V2"  # supplied B-roll overlays
    A1 = "A1"  # dialogue
    A2 = "A2"  # supplied music
    A3 = "A3"  # sound effects from the user's sound library; clips may overlap
    T1 = "T1"  # captions


VIDEO_TRACKS = {TrackId.V1, TrackId.V2}
AUDIO_TRACKS = {TrackId.A1, TrackId.A2, TrackId.A3}
#: Which asset roles may be placed on which track (only registered assets of the right kind).
TRACK_ROLES: dict[TrackId, set[AssetRole]] = {
    TrackId.V1: {AssetRole.PRIMARY},
    TrackId.V2: {AssetRole.BROLL, AssetRole.LOGO},
    TrackId.A1: {AssetRole.PRIMARY},
    TrackId.A2: {AssetRole.MUSIC},
    TrackId.A3: {AssetRole.SFX},
}
#: Each placed sound effect is one FFmpeg input; keep renders bounded.
MAX_SFX_CLIPS = 100


class FitMode(StrEnum):
    COVER = "cover"
    CONTAIN = "contain"


class Transform(ContractModel):
    #: 1.0 = fit per ``fit_mode``; >1 is a punch-in. Bounded to avoid unusable crops.
    scale: float = Field(default=1.0, ge=1.0, le=2.0)
    #: Crop centre, 0..1 of the source frame (0.5 = centred).
    center_x: float = Field(default=0.5, ge=0.0, le=1.0)
    center_y: float = Field(default=0.5, ge=0.0, le=1.0)


class Clip(ContractModel):
    clip_id: Identifier
    asset_id: Identifier
    track: TrackId
    #: Half-open source range on the asset's own video frame grid
    #: (audio-only assets: the timeline frame grid). See docs/TIMEBASE.md.
    source_in_frames: int = Field(ge=0)
    source_out_frames: int = Field(gt=0)
    timeline_in_frames: int = Field(ge=0)
    #: Output-grid span when the source frame rate differs from the timeline rate.
    timeline_duration_frames: int | None = Field(default=None, gt=0)
    fit_mode: FitMode = FitMode.COVER
    transform: Transform = Field(default_factory=Transform)
    gain_db: float = Field(default=0.0, ge=-60, le=12)
    fade_in_frames: int = Field(default=0, ge=0)
    fade_out_frames: int = Field(default=0, ge=0)
    rationale: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _range(self) -> Clip:
        if self.source_out_frames <= self.source_in_frames:
            raise ValueError("source_out_frames must be greater than source_in_frames")
        if self.fade_in_frames + self.fade_out_frames > self.timeline_span_frames:
            raise ValueError("fades longer than the clip")
        return self

    @property
    def duration_frames(self) -> int:
        return self.source_out_frames - self.source_in_frames

    @property
    def timeline_span_frames(self) -> int:
        return self.timeline_duration_frames or self.duration_frames

    @property
    def timeline_out_frames(self) -> int:
        return self.timeline_in_frames + self.timeline_span_frames


class CaptionPreset(StrEnum):
    """Enumerated burn-in styles (two lines max, phone-safe margins). Never free-form ASS/filters."""

    CLEAN_WHITE = "clean_white"
    BOLD_YELLOW = "bold_yellow"
    BOXED = "boxed"


class ColorPreset(StrEnum):
    """Fixed, gentle grade presets applied after compositing. ``none`` leaves pixels untouched."""

    NONE = "none"
    NATURAL = "natural"
    WARM = "warm"
    COOL = "cool"


#: Caption text: printable characters; a single newline may separate two lines.
CaptionText = Annotated[
    str, StringConstraints(min_length=1, max_length=200, pattern=r"^[^\x00-\x09\x0b-\x1f\x7f]+$")
]


class CaptionCue(ContractModel):
    cue_id: Identifier
    text: CaptionText
    timeline_in_frames: int = Field(ge=0)
    timeline_out_frames: int = Field(gt=0)
    #: Provenance: transcript word IDs this cue is built from (empty only for manual corrections).
    source_asset_id: Identifier
    transcript_id: Identifier | None = None
    word_ids: list[Identifier] = Field(default_factory=list)
    corrected: bool = False

    @model_validator(mode="after")
    def _range(self) -> CaptionCue:
        if self.timeline_out_frames <= self.timeline_in_frames:
            raise ValueError("caption out must be after in")
        if not self.word_ids and not self.corrected:
            raise ValueError("captions must cite transcript word_ids unless marked as a manual correction")
        if self.word_ids and self.transcript_id is None:
            raise ValueError("captions citing word_ids must name their transcript_id")
        if self.text.count("\n") > 1:
            raise ValueError("captions are limited to two lines")
        return self


class WordCorrection(ContractModel):
    """A versioned manual fix to one transcript word's caption text. Timing stays source-derived."""

    transcript_id: Identifier
    word_id: Identifier
    text: str = Field(min_length=1, max_length=100, pattern=r"^[^\x00-\x1f\x7f]+$")


class AudioMix(ContractModel):
    """Bounded mix settings compiled into fixed FFmpeg filters (spec §10 audio target)."""

    #: Single-pass EBU R128 normalization to the render profile's loudness/true-peak target.
    normalize: bool = True
    #: Short fades at every V1 cut so joins don't click. Original speech is otherwise untouched.
    edge_fade_ms: int = Field(default=12, ge=0, le=200)
    #: Duck A2 music under dialogue with a sidechain compressor.
    duck_music: bool = True
    duck_threshold_db: float = Field(default=-30.0, ge=-60, le=-6)
    duck_ratio: float = Field(default=8.0, ge=1, le=20)
    duck_attack_ms: int = Field(default=20, ge=1, le=2000)
    duck_release_ms: int = Field(default=400, ge=10, le=9000)


class Author(StrEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class Provenance(ContractModel):
    author: Author
    tool: str | None = Field(default=None, max_length=100)  # e.g. MCP tool or UI action
    summary: str = Field(default="", max_length=2000)
    created_at: datetime = Field(default_factory=utcnow)


class Timeline(ContractModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    project_id: Identifier
    revision: int = Field(ge=0)
    parent_revision: int | None = Field(default=None, ge=0)
    target_duration_frames: int = Field(gt=0)
    render_profile: RenderProfile
    clips: list[Clip] = Field(default_factory=list)
    captions: list[CaptionCue] = Field(default_factory=list)
    caption_preset: CaptionPreset = CaptionPreset.CLEAN_WHITE
    word_corrections: list[WordCorrection] = Field(default_factory=list, max_length=2000)
    color_preset: ColorPreset = ColorPreset.NONE
    audio: AudioMix = Field(default_factory=AudioMix)
    #: Transitions across V1 cuts; a cut without one stays a hard cut.
    transitions: list[Transition] = Field(default_factory=list, max_length=200)
    provenance: Provenance

    @model_validator(mode="after")
    def _structure(self) -> Timeline:
        if self.parent_revision is not None and self.parent_revision >= self.revision:
            raise ValueError("parent_revision must precede revision")
        ids = [c.clip_id for c in self.clips]
        if len(ids) != len(set(ids)):
            raise ValueError("clip_id values must be unique")
        if any(c.track is TrackId.T1 for c in self.clips):
            raise ValueError("captions belong in `captions`, not as clips on T1")
        cue_ids = [cue.cue_id for cue in self.captions]
        if len(cue_ids) != len(set(cue_ids)):
            raise ValueError("cue_id values must be unique")
        fixes = [(fix.transcript_id, fix.word_id) for fix in self.word_corrections]
        if len(fixes) != len(set(fixes)):
            raise ValueError("at most one correction per transcript word")
        # Primary video must be gap-free and non-overlapping (hard cuts back to back).
        v1 = sorted((c for c in self.clips if c.track is TrackId.V1), key=lambda c: c.timeline_in_frames)
        expected = 0
        for c in v1:
            if c.timeline_in_frames != expected:
                kind = "overlap" if c.timeline_in_frames < expected else "gap"
                raise ValueError(f"V1 {kind} at frame {min(expected, c.timeline_in_frames)}")
            expected = c.timeline_out_frames
        self._check_transitions(v1)
        if sum(c.track is TrackId.A3 for c in self.clips) > MAX_SFX_CLIPS:
            raise ValueError(f"at most {MAX_SFX_CLIPS} sound effects per timeline")
        # A3 is deliberately absent: sound effects may overlap each other.
        for track in (TrackId.V2, TrackId.A1, TrackId.A2):
            spans = sorted(
                (c.timeline_in_frames, c.timeline_out_frames) for c in self.clips if c.track is track
            )
            for (_, a_end), (b_start, _) in pairwise(spans):
                if b_start < a_end:
                    raise ValueError(f"overlapping clips on {track}")
        return self

    def _check_transitions(self, v1: list[Clip]) -> None:
        position = {clip.clip_id: index for index, clip in enumerate(v1)}
        seen: set[str] = set()
        into = [0] * len(v1)  # frames each clip gives to transitions at its start
        out_of = [0] * len(v1)  # ... and at its end
        for transition in self.transitions:
            index = position.get(transition.after_clip_id)
            if index is None:
                raise ValueError(f"transition after {transition.after_clip_id!r} needs a V1 clip")
            if index == len(v1) - 1:
                raise ValueError("there is no cut after the last V1 clip")
            if transition.after_clip_id in seen:
                raise ValueError(f"only one transition per cut ({transition.after_clip_id!r})")
            seen.add(transition.after_clip_id)
            out_of[index] = transition.incoming_frames
            into[index + 1] = transition.outgoing_frames
        for index, clip in enumerate(v1):
            if into[index] + out_of[index] > clip.timeline_span_frames:
                raise ValueError(f"transitions around V1 clip {clip.clip_id!r} are longer than the clip")

    @property
    def duration_frames(self) -> int:
        return max((c.timeline_out_frames for c in self.clips if c.track is TrackId.V1), default=0)


class ValidationIssue(EngineError):
    severity: Literal["error", "warning"] = "error"
    clip_id: str | None = None


def validate_against_assets(
    timeline: Timeline, assets: dict[str, Asset], duration_tolerance_frames: int | None = None
) -> list[ValidationIssue]:
    """Semantic checks that need the asset registry (structure is checked by the model).

    Returns every issue rather than stopping at the first, so an agent can fix a plan in one pass.
    """
    issues: list[ValidationIssue] = []

    def add(
        code: ErrorCode,
        msg: str,
        clip_id: str | None = None,
        severity: Literal["error", "warning"] = "error",
    ) -> None:
        issues.append(ValidationIssue(code=code, message=msg, clip_id=clip_id, severity=severity))

    for c in timeline.clips:
        a = assets.get(c.asset_id)
        if a is None or a.project_id != timeline.project_id:
            add(
                ErrorCode.ASSET_NOT_FOUND,
                f"asset {c.asset_id!r} is not registered in this project",
                c.clip_id,
            )
            continue
        if a.role not in TRACK_ROLES.get(c.track, set()):
            add(ErrorCode.INVALID_TIMELINE, f"{a.role} asset cannot be placed on {c.track}", c.clip_id)
        if c.track in VIDEO_TRACKS and a.video is None:
            add(ErrorCode.INVALID_TIMELINE, f"{c.track} needs a video stream", c.clip_id)
        if a.kind is AssetKind.IMAGE:
            if c.track is not TrackId.V2:
                add(ErrorCode.INVALID_TIMELINE, "still images can only appear on V2", c.clip_id)
            if (c.source_in_frames, c.source_out_frames) != (0, 1):
                add(ErrorCode.RANGE_OUT_OF_BOUNDS, "still image source range must be [0, 1)", c.clip_id)
            if c.timeline_duration_frames is None:
                add(ErrorCode.INVALID_TIMELINE, "still image needs an explicit output duration", c.clip_id)
        if c.track in AUDIO_TRACKS and a.audio is None:
            add(ErrorCode.INVALID_TIMELINE, f"{c.track} needs an audio stream", c.clip_id)
        limit = a.video.duration_frames if a.video else None
        if a.kind is AssetKind.AUDIO and a.audio:
            fps = timeline.render_profile.frame_rate.fraction
            limit = int(a.audio.duration_samples * fps / a.audio.sample_rate)
        if limit is not None and c.source_out_frames > limit:
            add(
                ErrorCode.RANGE_OUT_OF_BOUNDS,
                f"source range [{c.source_in_frames}, {c.source_out_frames}) exceeds asset length {limit}",
                c.clip_id,
            )
        if a.video and a.kind is not AssetKind.IMAGE and not a.video.is_vfr:
            expected_span = seconds_to_frames_round(
                c.duration_frames / a.video.frame_rate.fraction,
                timeline.render_profile.frame_rate,
            )
            if c.timeline_span_frames != expected_span:
                add(
                    ErrorCode.INVALID_TIMELINE,
                    f"clip needs {expected_span} output frames; set timeline_duration_frames",
                    c.clip_id,
                )
        if c.track is not TrackId.V1 and c.timeline_out_frames > timeline.duration_frames:
            add(
                ErrorCode.INVALID_TIMELINE,
                "clip extends past the end of the primary video",
                c.clip_id,
                "warning",
            )

    if not any(c.track is TrackId.V1 for c in timeline.clips):
        add(ErrorCode.INVALID_TIMELINE, "timeline has no primary (V1) clips")
    for cue in timeline.captions:
        if cue.source_asset_id not in assets:
            add(
                ErrorCode.ASSET_NOT_FOUND, f"caption {cue.cue_id} cites unknown asset {cue.source_asset_id!r}"
            )
        if cue.timeline_out_frames > timeline.duration_frames:
            add(
                ErrorCode.INVALID_TIMELINE,
                f"caption {cue.cue_id} runs past the end of the video",
                severity="warning",
            )

    tol = duration_tolerance_frames
    if tol is not None and abs(timeline.duration_frames - timeline.target_duration_frames) > tol:
        add(
            ErrorCode.INVALID_TIMELINE,
            f"duration {timeline.duration_frames} frames is outside target "
            f"{timeline.target_duration_frames} ± {tol}",
            severity="warning",
        )
    return issues
