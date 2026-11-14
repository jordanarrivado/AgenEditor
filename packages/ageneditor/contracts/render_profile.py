"""RenderProfile contract: output format. Values are enumerated, never free-form filters."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator

from ageneditor.timebase import FrameRate

from .common import SCHEMA_VERSION, ContractModel, Identifier


class EncoderPreset(StrEnum):
    FAST = "fast"
    BALANCED = "balanced"
    QUALITY = "quality"


class RenderProfile(ContractModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    id: Identifier
    width: int = Field(ge=16, le=7680)
    height: int = Field(ge=16, le=7680)
    frame_rate: FrameRate
    container: Literal["mp4"] = "mp4"
    video_codec: Literal["h264"] = "h264"
    pixel_format: Literal["yuv420p"] = "yuv420p"
    audio_codec: Literal["aac"] = "aac"
    audio_sample_rate: Literal[44100, 48000] = 48000
    audio_channels: Literal[1, 2] = 2
    preset: EncoderPreset = EncoderPreset.BALANCED
    #: Integrated loudness target and true-peak ceiling (spec §10).
    loudness_lufs: float = Field(default=-16.0, ge=-40, le=-5)
    true_peak_dbtp: float = Field(default=-1.0, ge=-10, le=0)

    @field_validator("width", "height")
    @classmethod
    def _even(cls, v: int) -> int:
        if v % 2:
            raise ValueError("yuv420p needs even dimensions")
        return v


REEL_FINAL = RenderProfile(id="reel_1080x1920_30", width=1080, height=1920, frame_rate=FrameRate(num=30))
REEL_PREVIEW = RenderProfile(
    id="reel_preview_720x1280_30",
    width=720,
    height=1280,
    frame_rate=FrameRate(num=30),
    preset=EncoderPreset.FAST,
)
BUILTIN_PROFILES: dict[str, RenderProfile] = {p.id: p for p in (REEL_FINAL, REEL_PREVIEW)}
