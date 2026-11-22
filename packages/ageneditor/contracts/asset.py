"""Asset contract: a registered, immutable source file inside a project."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from ageneditor.timebase import FrameRate

from .common import ContractModel, Identifier, ProjectRelativePath, Sha256Hex, utcnow
from .sound_library import LibrarySource


class AssetKind(StrEnum):
    VIDEO = "video"
    AUDIO = "audio"
    IMAGE = "image"
    SUBTITLE = "subtitle"


class AssetRole(StrEnum):
    """What the user registered the asset as. Track rules decide which roles a timeline may use."""

    PRIMARY = "primary"  # talking-head / interview source
    BROLL = "broll"
    MUSIC = "music"
    LOGO = "logo"
    SUBTITLES = "subtitles"
    #: A style or pacing example for the editor. Never allowed on any timeline track.
    REFERENCE = "reference"
    #: A short audio-only sound effect from the user's sound library (track A3).
    SFX = "sfx"
    #: An audio-only voice line (voice-over), recorded by the user or generated in an outside tool
    #: (track A4). Never the primary speaker, never transcribed as source dialogue.
    VOICE = "voice"


class AssetOrigin(StrEnum):
    ORIGINAL = "original"
    GENERATED_EXTERNAL = "generated-external"
    #: Copied from a pack the user installed into the sound library.
    SOUND_LIBRARY = "sound-library"


class AssetProvenance(ContractModel):
    origin: AssetOrigin = AssetOrigin.ORIGINAL
    tool_name: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None
    ) = None
    #: Omitted from serialized output unless set, so other assets keep their existing wire format.
    library: LibrarySource | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def _tool_matches_origin(self) -> AssetProvenance:
        if self.origin is AssetOrigin.GENERATED_EXTERNAL and not self.tool_name:
            raise ValueError("generated-external assets need a tool name")
        if self.origin is not AssetOrigin.GENERATED_EXTERNAL and self.tool_name is not None:
            raise ValueError("only generated-external assets have a generation tool")
        if (self.origin is AssetOrigin.SOUND_LIBRARY) != (self.library is not None):
            raise ValueError("sound-library assets, and only they, record their library source")
        return self


#: A user-supplied shot label for B-roll matching, e.g. ``"coffee pour"``. Plain words only.
ShotLabel = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60, pattern=r"^[\w][\w \-']*$")
]


class VideoStream(ContractModel):
    index: int = Field(ge=0)
    codec: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    #: Nominal rate (ffprobe ``r_frame_rate``).
    frame_rate: FrameRate
    #: Average rate (``avg_frame_rate``); differs from ``frame_rate`` for VFR.
    avg_frame_rate: FrameRate | None = None
    time_base_num: int = Field(gt=0)
    time_base_den: int = Field(gt=0)
    start_pts: int = 0
    duration_frames: int = Field(ge=0)
    pixel_format: str | None = None
    rotation_degrees: int = 0
    is_vfr: bool = False


class AudioStream(ContractModel):
    index: int = Field(ge=0)
    codec: str
    sample_rate: int = Field(gt=0)
    channels: int = Field(gt=0)
    time_base_num: int = Field(gt=0)
    time_base_den: int = Field(gt=0)
    start_pts: int = 0
    duration_samples: int = Field(ge=0)


class Asset(ContractModel):
    # Accept stored v1 assets; new imports serialize as v2.
    schema_version: Literal[1, 2] = 2
    id: Identifier
    project_id: Identifier
    kind: AssetKind
    role: AssetRole
    #: Immutable copy under the project (e.g. ``originals/interview.mp4``).
    relative_path: ProjectRelativePath
    original_filename: str = Field(max_length=255)
    sha256: Sha256Hex
    size_bytes: int = Field(ge=0)
    container: str | None = None
    video: VideoStream | None = None
    audio: AudioStream | None = None
    imported_at: datetime = Field(default_factory=utcnow)
    #: User-provided shot labels (Phase 3 B-roll matching). Never model-generated without consent.
    labels: list[ShotLabel] = Field(default_factory=list, max_length=20)
    provenance: AssetProvenance = Field(default_factory=AssetProvenance)

    @model_validator(mode="after")
    def _streams_match_kind(self) -> Asset:
        if self.kind is AssetKind.VIDEO and self.video is None:
            raise ValueError("video assets need a video stream")
        if self.kind is AssetKind.AUDIO and (self.audio is None or self.video is not None):
            raise ValueError("audio assets need exactly an audio stream")
        if self.kind is AssetKind.IMAGE and (
            self.video is None
            or self.audio is not None
            or self.role not in {AssetRole.BROLL, AssetRole.LOGO, AssetRole.REFERENCE}
        ):
            raise ValueError("image assets need a picture stream and B-roll, logo or reference role")
        if self.relative_path.split("/")[0] != "originals":
            raise ValueError("assets must live under originals/")
        return self

    @property
    def duration_frames(self) -> int | None:
        return self.video.duration_frames if self.video else None
