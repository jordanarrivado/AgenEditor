"""Transitions between primary (V1) clips, rendered with FFmpeg's built-in ``xfade`` filter.

Nothing is downloaded: every preset maps to a transition compiled into the bundled FFmpeg.
Agents choose a preset by ID; the filter name is never taken from agent input.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import Field

from .common import ContractModel, Identifier


class TransitionStyle(StrEnum):
    CLASSIC = "classic"  # dissolves, dips and wipes
    MODERN = "modern"  # slides, pushes, covers and reveals
    MONTAGE = "montage"  # fast, graphic, energetic
    CINEMATIC = "cinematic"  # slow fades, blurs and iris/bar reveals


@dataclass(frozen=True)
class TransitionPresetInfo:
    label: str
    style: TransitionStyle
    xfade: str
    #: Suggested length at 30 fps.
    default_frames: int


def _directions(prefix: str, label: str, style: TransitionStyle, xfade: str, frames: int) -> dict:
    names = {"left": "left", "right": "right", "up": "up", "down": "down"}
    return {
        f"{prefix}_{d}": TransitionPresetInfo(f"{label} {d}", style, f"{xfade}{names[d]}", frames)
        for d in names
    }


_C, _M, _X, _N = (
    TransitionStyle.CLASSIC,
    TransitionStyle.MODERN,
    TransitionStyle.MONTAGE,
    TransitionStyle.CINEMATIC,
)

#: Every transition the renderer can draw. Keys are the IDs agents use.
TRANSITION_PRESETS: dict[str, TransitionPresetInfo] = {
    # Classic
    "cross_dissolve": TransitionPresetInfo("Cross dissolve", _C, "fade", 15),
    "fast_dissolve": TransitionPresetInfo("Fast dissolve", _C, "fadefast", 10),
    "dip_to_black": TransitionPresetInfo("Dip to black", _C, "fadeblack", 18),
    "dip_to_white": TransitionPresetInfo("Dip to white", _C, "fadewhite", 18),
    "grain_dissolve": TransitionPresetInfo("Grain dissolve", _C, "dissolve", 15),
    **_directions("wipe", "Wipe", _C, "wipe", 15),
    # Modern
    **_directions("slide", "Slide", _M, "slide", 12),
    **_directions("smooth", "Smooth wipe", _M, "smooth", 15),
    **_directions("cover", "Cover", _M, "cover", 12),
    **_directions("reveal", "Reveal", _M, "reveal", 12),
    "squeeze_horizontal": TransitionPresetInfo("Squeeze horizontal", _M, "squeezeh", 12),
    "squeeze_vertical": TransitionPresetInfo("Squeeze vertical", _M, "squeezev", 12),
    # Montage
    "zoom_in": TransitionPresetInfo("Zoom in", _X, "zoomin", 12),
    "pixelize": TransitionPresetInfo("Pixelize", _X, "pixelize", 12),
    "radial_sweep": TransitionPresetInfo("Radial sweep", _X, "radial", 15),
    "circle_crop": TransitionPresetInfo("Circle crop", _X, "circlecrop", 15),
    "rect_crop": TransitionPresetInfo("Rectangle crop", _X, "rectcrop", 15),
    "diagonal_top_left": TransitionPresetInfo("Diagonal top-left", _X, "diagtl", 12),
    "diagonal_top_right": TransitionPresetInfo("Diagonal top-right", _X, "diagtr", 12),
    "diagonal_bottom_left": TransitionPresetInfo("Diagonal bottom-left", _X, "diagbl", 12),
    "diagonal_bottom_right": TransitionPresetInfo("Diagonal bottom-right", _X, "diagbr", 12),
    "corner_wipe_top_left": TransitionPresetInfo("Corner wipe top-left", _X, "wipetl", 12),
    "corner_wipe_top_right": TransitionPresetInfo("Corner wipe top-right", _X, "wipetr", 12),
    "corner_wipe_bottom_left": TransitionPresetInfo("Corner wipe bottom-left", _X, "wipebl", 12),
    "corner_wipe_bottom_right": TransitionPresetInfo("Corner wipe bottom-right", _X, "wipebr", 12),
    "slice_left": TransitionPresetInfo("Slices left", _X, "hlslice", 12),
    "slice_right": TransitionPresetInfo("Slices right", _X, "hrslice", 12),
    "slice_up": TransitionPresetInfo("Slices up", _X, "vuslice", 12),
    "slice_down": TransitionPresetInfo("Slices down", _X, "vdslice", 12),
    "wind_left": TransitionPresetInfo("Wind left", _X, "hlwind", 15),
    "wind_right": TransitionPresetInfo("Wind right", _X, "hrwind", 15),
    "wind_up": TransitionPresetInfo("Wind up", _X, "vuwind", 15),
    "wind_down": TransitionPresetInfo("Wind down", _X, "vdwind", 15),
    # Cinematic
    "slow_fade": TransitionPresetInfo("Slow fade", _N, "fadeslow", 30),
    "fade_through_gray": TransitionPresetInfo("Fade through grey", _N, "fadegrays", 24),
    "blur_dissolve": TransitionPresetInfo("Blur dissolve", _N, "hblur", 18),
    "distance_morph": TransitionPresetInfo("Distance morph", _N, "distance", 20),
    "iris_open": TransitionPresetInfo("Iris open", _N, "circleopen", 20),
    "iris_close": TransitionPresetInfo("Iris close", _N, "circleclose", 20),
    "bars_open_horizontal": TransitionPresetInfo("Bars open horizontal", _N, "horzopen", 20),
    "bars_close_horizontal": TransitionPresetInfo("Bars close horizontal", _N, "horzclose", 20),
    "bars_open_vertical": TransitionPresetInfo("Bars open vertical", _N, "vertopen", 20),
    "bars_close_vertical": TransitionPresetInfo("Bars close vertical", _N, "vertclose", 20),
}

TransitionName = StrEnum("TransitionName", {key.upper(): key for key in TRANSITION_PRESETS})  # type: ignore[misc]

#: Longest single transition (2 s at 30 fps).
MAX_TRANSITION_FRAMES = 60


class Transition(ContractModel):
    """A transition across the cut after one V1 clip. Total duration is unchanged: the
    transition uses half its length from just past the outgoing clip and half from just
    before the incoming clip (source handles when available, otherwise a held frame)."""

    after_clip_id: Identifier
    preset: TransitionName
    duration_frames: int = Field(ge=2, le=MAX_TRANSITION_FRAMES)
    rationale: str | None = Field(default=None, max_length=1000)

    @property
    def outgoing_frames(self) -> int:
        """Frames of the transition drawn after the cut point from the outgoing clip."""
        return self.duration_frames // 2

    @property
    def incoming_frames(self) -> int:
        return self.duration_frames - self.outgoing_frames

    @property
    def info(self) -> TransitionPresetInfo:
        return TRANSITION_PRESETS[self.preset.value]


class TransitionSettings(ContractModel):
    """Workspace-wide: which styles the editor agent may use. Only the person changes it."""

    schema_version: Literal[1] = 1
    active_styles: list[TransitionStyle] = Field(default_factory=lambda: list(TransitionStyle))
