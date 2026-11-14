"""Measured output analysis for QA: integrated loudness, true peak, black and silent intervals.

One FFmpeg decode pass; every value reported here was measured from the rendered file.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ageneditor.contracts import EngineException, ErrorCode

from .silence import AudioSilence, parse_silencedetect
from .tools import find_tool

ANALYSIS_TIMEOUT_S = 900
SILENCE_FLOOR_DB = -50
MIN_GAP_US = 1_500_000
MIN_BLACK_S = "0.5"
_BLACK = re.compile(rb"black_start:\s*([0-9.]+)\s+black_end:\s*([0-9.]+)")
_LOUDNORM_JSON = re.compile(rb"\{[^{}]*\"input_i\"[^{}]*\}", re.S)


@dataclass
class OutputAnalysis:
    integrated_lufs: float | None
    true_peak_dbtp: float | None
    black: list[tuple[int, int]] = field(default_factory=list)
    silence: list[AudioSilence] = field(default_factory=list)


def _number(value: str) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number not in (float("inf"), float("-inf")) else None


def _us(raw: bytes) -> int:
    whole, _, frac = raw.decode("ascii").partition(".")
    return int(whole) * 1_000_000 + int((frac + "000000")[:6])


def analyze_output(path: Path, duration_us: int, has_video: bool = True) -> OutputAnalysis:
    args = [str(find_tool("ffmpeg")), "-hide_banner", "-nostats", "-i", str(path)]
    if has_video:
        args += ["-map", "0:v:0", "-vf", f"blackdetect=d={MIN_BLACK_S}:pix_th=0.10"]
    args += [
        "-map",
        "0:a:0",
        "-af",
        f"silencedetect=noise={SILENCE_FLOOR_DB}dB:d=1.5,loudnorm=print_format=json",
        "-f",
        "null",
        "-",
    ]
    try:
        proc = subprocess.run(args, capture_output=True, timeout=ANALYSIS_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired as exc:
        raise EngineException(ErrorCode.QA_FAILED, "output analysis timed out") from exc
    if proc.returncode != 0:
        raise EngineException(ErrorCode.QA_FAILED, proc.stderr.decode("utf-8", "replace")[-500:])
    stderr = proc.stderr
    loud: dict = {}
    if match := _LOUDNORM_JSON.search(stderr):
        try:
            loud = json.loads(match.group(0))
        except json.JSONDecodeError:
            loud = {}
    return OutputAnalysis(
        integrated_lufs=_number(loud.get("input_i")),
        true_peak_dbtp=_number(loud.get("input_tp")),
        black=[(_us(a), _us(b)) for a, b in _BLACK.findall(stderr)],
        silence=[s for s in parse_silencedetect(stderr, duration_us) if s.end_us - s.start_us >= MIN_GAP_US],
    )
