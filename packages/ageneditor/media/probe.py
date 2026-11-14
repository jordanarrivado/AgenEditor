"""FFprobe wrapper producing typed stream metadata (ASSET-001 groundwork).

The probe never trusts the container's ``start_time`` for frame math: stream
``start_pts`` is recorded so timestamps can be normalised (docs/TIMEBASE.md).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from ageneditor.contracts import AudioStream, EngineException, ErrorCode, VideoStream
from ageneditor.timebase import FrameRate, seconds_to_frames_round

from .tools import find_tool

PROBE_TIMEOUT_S = 60


@dataclass(frozen=True)
class ProbeResult:
    path: str
    container: str
    duration_seconds: Fraction | None
    size_bytes: int
    video: VideoStream | None = None
    audio: AudioStream | None = None
    extra_streams: int = 0

    def to_dict(self) -> dict[str, Any]:
        d = self.duration_seconds
        return {
            "path": self.path,
            "container": self.container,
            "duration_seconds": None
            if d is None
            else {"exact": f"{d.numerator}/{d.denominator}", "approx": float(d)},
            "size_bytes": self.size_bytes,
            "video": self.video.model_dump(mode="json") if self.video else None,
            "audio": self.audio.model_dump(mode="json") if self.audio else None,
            "extra_streams": self.extra_streams,
        }


def _fraction(value: str | None) -> Fraction | None:
    if value in (None, "", "0/0", "N/A"):
        return None
    try:
        f = Fraction(value)
    except (ValueError, ZeroDivisionError):
        return None
    return f if f > 0 else None


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def run_ffprobe(path: Path, extra_args: list[str] | None = None) -> dict[str, Any]:
    if not path.is_file():
        raise EngineException(ErrorCode.ASSET_NOT_FOUND, "file does not exist", path=str(path))
    args = [
        str(find_tool("ffprobe")),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        *(extra_args or []),
        str(path),
    ]
    try:
        proc = subprocess.run(args, capture_output=True, timeout=PROBE_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired as e:
        raise EngineException(ErrorCode.PROBE_FAILED, "ffprobe timed out", path=str(path)) from e
    if proc.returncode != 0:
        msg = proc.stderr.decode("utf-8", "replace").strip()[:500]
        raise EngineException(ErrorCode.PROBE_FAILED, msg or "ffprobe failed", path=str(path))
    return json.loads(proc.stdout.decode("utf-8"))


def _video_stream(s: dict[str, Any], container_duration: Fraction | None) -> VideoStream:
    r_rate = _fraction(s.get("r_frame_rate"))
    avg_rate = _fraction(s.get("avg_frame_rate"))
    rate = r_rate or avg_rate
    if rate is None:
        raise EngineException(ErrorCode.UNSUPPORTED_CODEC, "video stream has no usable frame rate")
    tb = _fraction(s.get("time_base")) or Fraction(1, 90000)
    duration = _fraction(s.get("duration")) or container_duration
    nb_frames = _int(s.get("nb_frames"), -1)
    if nb_frames > 0:
        frames = nb_frames
    elif duration is not None:
        frames = seconds_to_frames_round(duration, avg_rate or rate)
    else:
        frames = 0
    rotation = 0
    for side in s.get("side_data_list", []) or []:
        if "rotation" in side:
            rotation = _int(side["rotation"])
    return VideoStream(
        index=_int(s.get("index")),
        codec=str(s.get("codec_name", "unknown")),
        width=_int(s.get("width")),
        height=_int(s.get("height")),
        frame_rate=FrameRate.parse(rate),
        avg_frame_rate=FrameRate.parse(avg_rate) if avg_rate else None,
        time_base_num=tb.numerator,
        time_base_den=tb.denominator,
        start_pts=_int(s.get("start_pts")),
        duration_frames=frames,
        pixel_format=s.get("pix_fmt"),
        rotation_degrees=rotation,
        # r_frame_rate is the lowest rate that represents all timestamps; a different average means VFR.
        is_vfr=bool(r_rate and avg_rate and r_rate != avg_rate),
    )


def _audio_stream(s: dict[str, Any], container_duration: Fraction | None) -> AudioStream:
    sr = _int(s.get("sample_rate"))
    if sr <= 0:
        raise EngineException(ErrorCode.UNSUPPORTED_CODEC, "audio stream has no sample rate")
    tb = _fraction(s.get("time_base")) or Fraction(1, sr)
    duration = _fraction(s.get("duration")) or container_duration or Fraction(0)
    return AudioStream(
        index=_int(s.get("index")),
        codec=str(s.get("codec_name", "unknown")),
        sample_rate=sr,
        channels=max(1, _int(s.get("channels"), 1)),
        time_base_num=tb.numerator,
        time_base_den=tb.denominator,
        start_pts=_int(s.get("start_pts")),
        duration_samples=round(duration * sr),
    )


def probe(path: Path) -> ProbeResult:
    """Inspect a media file. Picks the first video and first audio stream."""
    data = run_ffprobe(path)
    fmt = data.get("format", {})
    container_duration = _fraction(fmt.get("duration"))
    video = audio = None
    extra = 0
    for s in data.get("streams", []):
        kind = s.get("codec_type")
        disposition = s.get("disposition", {}) or {}
        if kind == "video" and video is None and not disposition.get("attached_pic"):
            video = _video_stream(s, container_duration)
        elif kind == "audio" and audio is None:
            audio = _audio_stream(s, container_duration)
        else:
            extra += 1
    if video is None and audio is None:
        raise EngineException(ErrorCode.UNSUPPORTED_CODEC, "no audio or video streams", path=str(path))
    return ProbeResult(
        path=str(path),
        container=str(fmt.get("format_name", "unknown")),
        duration_seconds=container_duration,
        size_bytes=_int(fmt.get("size"), path.stat().st_size),
        video=video,
        audio=audio,
        extra_streams=extra,
    )


def video_frame_times(path: Path, stream_index: int = 0) -> list[Fraction]:
    """Normalised presentation times of every video frame (for VFR → CFR mapping)."""
    args = [
        str(find_tool("ffprobe")),
        "-v",
        "error",
        "-select_streams",
        f"v:{stream_index}",
        "-show_entries",
        "stream=time_base,start_pts:packet=pts",
        "-print_format",
        "json",
        str(path),
    ]
    try:
        proc = subprocess.run(args, capture_output=True, timeout=PROBE_TIMEOUT_S * 5, check=False)
    except subprocess.TimeoutExpired as e:
        raise EngineException(ErrorCode.PROBE_FAILED, "ffprobe timed out", path=str(path)) from e
    if proc.returncode != 0:
        raise EngineException(ErrorCode.PROBE_FAILED, proc.stderr.decode("utf-8", "replace")[:500])
    data = json.loads(proc.stdout)
    stream = data["streams"][0]
    tb = Fraction(stream["time_base"])
    pts = sorted(_int(p["pts"]) for p in data.get("packets", []) if "pts" in p)
    if not pts:
        return []
    start = _int(stream.get("start_pts"), pts[0])
    return [(p - start) * tb for p in pts]
