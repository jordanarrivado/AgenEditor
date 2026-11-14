"""Audio-level silence detection (VAD candidates) via FFmpeg ``silencedetect``.

Results are *candidates* for an agent or editor to consider; nothing here removes pauses.
Times are integer microseconds on the source clock (see docs/TIMEBASE.md).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path

from ageneditor.contracts import EngineException, ErrorCode

from .tools import find_tool

SILENCE_TIMEOUT_S = 900
_START = re.compile(rb"silence_start:\s*(-?[0-9.]+)")
_END = re.compile(rb"silence_end:\s*(-?[0-9.]+)")


@dataclass(frozen=True)
class AudioSilence:
    start_us: int
    end_us: int


def _us(raw: bytes) -> int:
    try:
        value = Decimal(raw.decode("ascii"))
    except (InvalidOperation, UnicodeDecodeError) as exc:
        raise EngineException(ErrorCode.PROBE_FAILED, "unparseable silencedetect timestamp") from exc
    return max(0, int((value * 1_000_000).to_integral_value(rounding=ROUND_HALF_EVEN)))


def parse_silencedetect(stderr: bytes, duration_us: int) -> list[AudioSilence]:
    """Pair ``silence_start``/``silence_end`` log lines; an unterminated run closes at ``duration_us``."""
    ranges: list[AudioSilence] = []
    start: int | None = None
    for line in stderr.splitlines():
        if m := _START.search(line):
            start = _us(m.group(1))
        elif (m := _END.search(line)) and start is not None:
            end = min(_us(m.group(1)), duration_us)
            if end > start:
                ranges.append(AudioSilence(start, end))
            start = None
    if start is not None and duration_us > start:
        ranges.append(AudioSilence(start, duration_us))
    return ranges


def detect_silence(
    path: Path,
    duration_us: int,
    *,
    noise_floor_db: int = -45,
    min_silence_us: int = 300_000,
    audio_stream: int = 0,
) -> list[AudioSilence]:
    """Return audio ranges quieter than ``noise_floor_db`` for at least ``min_silence_us``."""
    if not -90 <= noise_floor_db <= -10:
        raise EngineException(ErrorCode.INVALID_ARGUMENT, "noise floor must be between -90 and -10 dB")
    if not 50_000 <= min_silence_us <= 10_000_000:
        raise EngineException(ErrorCode.INVALID_ARGUMENT, "minimum silence must be 0.05-10 seconds")
    if not 0 <= audio_stream <= 31 or duration_us <= 0:
        raise EngineException(ErrorCode.INVALID_ARGUMENT, "invalid audio stream or duration")
    if not path.is_file():
        raise EngineException(ErrorCode.ASSET_NOT_FOUND, "file does not exist", path=str(path))
    seconds = Decimal(min_silence_us) / 1_000_000
    args = [
        str(find_tool("ffmpeg")),
        "-hide_banner",
        "-nostats",
        "-i",
        str(path),
        "-map",
        f"0:a:{audio_stream}",
        "-af",
        f"silencedetect=noise={noise_floor_db}dB:d={seconds}",
        "-f",
        "null",
        "-",
    ]
    try:
        proc = subprocess.run(args, capture_output=True, timeout=SILENCE_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired as exc:
        raise EngineException(ErrorCode.PROBE_FAILED, "silence detection timed out", path=str(path)) from exc
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace")[-500:]
        raise EngineException(ErrorCode.PROBE_FAILED, tail or "silence detection failed", path=str(path))
    return parse_silencedetect(proc.stderr, duration_us)
