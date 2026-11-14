"""Canonical timebase (ARCH-001). See docs/TIMEBASE.md for the rationale.

Conventions
-----------
* Frame rates are exact rationals (``FrameRate(30000, 1001)``), never floats.
* Timeline positions are integer *output frame indices*. Frame ``i`` covers the
  half-open interval ``[i / fps, (i + 1) / fps)``.
* Ranges are half-open: ``[in_frames, out_frames)``; duration = out - in.
* Source timestamps are normalised as ``(pts - stream_start_pts) * time_base``
  — i.e. seconds since the first presented sample of that stream, as an exact
  ``Fraction``. Container ``start_time`` offsets never leak into frame math.
* Mapping a source time onto a frame grid uses ``floor`` (the frame being
  displayed at that instant). Mapping a *duration* uses round-half-even.
* VFR sources are resampled onto a CFR output grid with sample-and-hold: output
  frame ``k`` shows the last source frame whose normalised PTS is ``<= k / fps``.
* Audio is sample-accurate: frame boundaries convert to samples with
  round-half-even, so 29.97 fps at 48 kHz (1601.6 samples/frame) never drifts
  by more than half a sample at any boundary.
"""

from __future__ import annotations

import bisect
from fractions import Fraction
from itertools import pairwise
from math import floor

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FrameRate(BaseModel):
    """Exact rational frame rate, e.g. 30/1 or 30000/1001."""

    model_config = ConfigDict(frozen=True)

    num: int = Field(gt=0, le=1_000_000)
    den: int = Field(default=1, gt=0, le=1_000_000)

    @property
    def fraction(self) -> Fraction:
        return Fraction(self.num, self.den)

    @classmethod
    def parse(cls, value: str | int | Fraction) -> FrameRate:
        """Parse ffprobe-style ``"30000/1001"``, ``"30"`` or a Fraction."""
        f = Fraction(value) if not isinstance(value, Fraction) else value
        if f <= 0:
            raise ValueError(f"frame rate must be positive, got {value!r}")
        return cls(num=f.numerator, den=f.denominator)

    def __str__(self) -> str:
        return f"{self.num}/{self.den}"


def _as_fraction(rate: FrameRate | Fraction | int) -> Fraction:
    if isinstance(rate, FrameRate):
        return rate.fraction
    return Fraction(rate)


def frames_to_seconds(frames: int, rate: FrameRate | Fraction | int) -> Fraction:
    """Exact start time of frame ``frames``."""
    return Fraction(frames) / _as_fraction(rate)


def seconds_to_frame_floor(seconds: Fraction | int, rate: FrameRate | Fraction | int) -> int:
    """Index of the frame on screen at instant ``seconds`` (floor)."""
    return floor(Fraction(seconds) * _as_fraction(rate))


def seconds_to_frames_round(seconds: Fraction | int, rate: FrameRate | Fraction | int) -> int:
    """Nearest whole number of frames for a *duration* (round-half-even)."""
    return round(Fraction(seconds) * _as_fraction(rate))


def normalize_pts(pts: int, time_base: Fraction, stream_start_pts: int = 0) -> Fraction:
    """Seconds since the stream's first presented sample, exactly."""
    if time_base <= 0:
        raise ValueError("time_base must be positive")
    return (pts - stream_start_pts) * time_base


def rescale_frames(frames: int, src: FrameRate | Fraction, dst: FrameRate | Fraction) -> int:
    """Convert a frame *position* between rates using the floor rule."""
    return seconds_to_frame_floor(frames_to_seconds(frames, src), dst)


def frames_to_samples(frames: int, rate: FrameRate | Fraction | int, sample_rate: int) -> int:
    """Sample index of a frame boundary (round-half-even, no cumulative drift)."""
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    return round(frames_to_seconds(frames, rate) * sample_rate)


def cfr_frame_map(source_times: list[Fraction], out_rate: FrameRate | Fraction, out_frames: int) -> list[int]:
    """Sample-and-hold map from a (possibly VFR) source onto a CFR output grid.

    ``source_times`` are normalised presentation times of source frames in
    ascending order. Returns, for each output frame ``k``, the index of the
    source frame on screen at ``k / out_rate``. Output frames before the first
    source frame map to source frame 0.
    """
    if any(b < a for a, b in pairwise(source_times)):
        raise ValueError("source_times must be sorted ascending")
    if not source_times:
        raise ValueError("source_times is empty")
    result: list[int] = []
    for k in range(out_frames):
        t = frames_to_seconds(k, out_rate)
        i = bisect.bisect_right(source_times, t) - 1
        result.append(max(i, 0))
    return result


def format_timecode(frames: int, rate: FrameRate | Fraction | int) -> str:
    """Non-drop-frame ``HH:MM:SS:FF`` using the nominal (rounded-up) frame rate.

    Display only — all arithmetic uses exact frames. 29.97 fps displays with a
    nominal base of 30 (non-drop-frame, so it drifts from wall-clock by design).
    """
    if frames < 0:
        raise ValueError("frames must be >= 0")
    nominal = -(-_as_fraction(rate).numerator // _as_fraction(rate).denominator)  # ceil
    ff = frames % nominal
    total_seconds = frames // nominal
    return f"{total_seconds // 3600:02d}:{total_seconds // 60 % 60:02d}:{total_seconds % 60:02d}:{ff:02d}"


class RationalTime(BaseModel):
    """A value counted in units of ``rate`` (frames or samples)."""

    model_config = ConfigDict(frozen=True)

    value: int = Field(ge=0)
    rate: FrameRate

    @field_validator("value")
    @classmethod
    def _int(cls, v: int) -> int:
        return int(v)

    @property
    def seconds(self) -> Fraction:
        return frames_to_seconds(self.value, self.rate)
