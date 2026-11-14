"""Convert source frame ranges into exact normalized source timestamps."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from ageneditor.contracts import Asset, AssetKind, Clip, EngineException, ErrorCode
from ageneditor.media.probe import probe, video_frame_times


def source_interval(
    asset: Asset, clip: Clip, path: Path, timeline_rate: Fraction
) -> tuple[Fraction, Fraction]:
    """Return the half-open source interval for a registered clip.

    CFR indices use the source's rational frame grid. VFR indices address actual
    presented frames; the next frame PTS (or stream end) closes the range.
    """
    if asset.kind is AssetKind.IMAGE:
        return Fraction(0), Fraction(clip.timeline_span_frames, 1) / timeline_rate
    if asset.video is None:
        return Fraction(clip.source_in_frames, 1) / timeline_rate, Fraction(
            clip.source_out_frames, 1
        ) / timeline_rate
    if not asset.video.is_vfr:
        rate = asset.video.frame_rate.fraction
        return Fraction(clip.source_in_frames, 1) / rate, Fraction(clip.source_out_frames, 1) / rate
    times = video_frame_times(path)
    if clip.source_in_frames >= len(times) or clip.source_out_frames > len(times):
        raise EngineException(ErrorCode.RANGE_OUT_OF_BOUNDS, "VFR source frame is outside decoded frame map")
    start = times[clip.source_in_frames]
    if clip.source_out_frames < len(times):
        end = times[clip.source_out_frames]
    else:
        duration = probe(path).duration_seconds
        if duration is None:
            raise EngineException(ErrorCode.PROBE_FAILED, "VFR stream has no duration")
        end = duration
    if end <= start:
        raise EngineException(ErrorCode.RANGE_OUT_OF_BOUNDS, "VFR source interval has no duration")
    return start, end
