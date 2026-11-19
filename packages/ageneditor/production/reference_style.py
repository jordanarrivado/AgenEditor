"""Reference intelligence (spec §10): measured style notes from a project's reference media.

Starts where the spec says to: stored probe metadata, measured audio loudness, and sampled shot structure
(FFmpeg scene detection). Every note produced here is ``basis="measured"`` and cites its source asset.
Subjective impressions are never produced here; a person adds those, labelled as theirs.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
from collections.abc import Sequence
from itertools import pairwise

from ageneditor.contracts import AssetKind, AssetRole, EngineException, ErrorCode, StyleObservation
from ageneditor.contracts.asset import Asset
from ageneditor.media.tools import find_tool
from ageneditor.storage import ProjectStore

#: Only the start of a long reference is decoded, so measuring fits in one desktop request.
MAX_ANALYSIS_US = 300_000_000
#: Scene-change score above which a frame starts a new shot (0 to 1; FFmpeg ``scene``).
SCENE_THRESHOLD = "0.30"
ANALYSIS_TIMEOUT_S = 110
_PTS = re.compile(rb"pts_time:\s*([0-9]+(?:\.[0-9]+)?)")
_LOUDNORM_JSON = re.compile(rb"\{[^{}]*\"input_i\"[^{}]*\}", re.S)


def _us(text: bytes | str) -> int:
    raw = text.decode("ascii") if isinstance(text, bytes) else text
    whole, _, frac = raw.partition(".")
    return int(whole) * 1_000_000 + int((frac + "000000")[:6])


def _seconds_arg(us: int) -> str:
    return f"{us // 1_000_000}.{us % 1_000_000:06d}"


def _seconds_text(us: int) -> str:
    return f"{us // 1_000_000}.{(us % 1_000_000) // 100_000} s"


def parse_cut_times(stderr: bytes) -> list[int]:
    """Cut times in microseconds from ``showinfo`` lines after a scene ``select``."""
    return sorted({_us(match) for match in _PTS.findall(stderr)})


def shot_lengths(cuts: Sequence[int], duration_us: int) -> list[int]:
    """Lengths of the shots between cuts, ignoring cuts at or beyond the analysed duration."""
    bounds = [0, *[c for c in cuts if 0 < c < duration_us], duration_us]
    return [b - a for a, b in pairwise(bounds) if b > a]


def asset_duration_us(asset: Asset) -> int | None:
    if asset.kind is AssetKind.IMAGE:
        return None
    if asset.video is not None and asset.video.duration_frames:
        rate = asset.video.frame_rate
        return asset.video.duration_frames * rate.den * 1_000_000 // rate.num
    if asset.audio is not None and asset.audio.duration_samples:
        return asset.audio.duration_samples * 1_000_000 // asset.audio.sample_rate
    return None


def _frame_text(width: int, height: int) -> str:
    divisor = math.gcd(width, height)
    ratio = f"{width // divisor}:{height // divisor}"
    shape = "vertical" if height > width else "horizontal" if width > height else "square"
    return f"{width}x{height} ({shape}, {ratio})"


def _rate_text(num: int, den: int) -> str:
    if num % den == 0:
        return f"{num // den} fps"
    return f"{num / den:.2f} fps"  # display only; the stored rate stays rational


def _note(asset: Asset, key: str, label: str, value: str) -> StyleObservation:
    return StyleObservation(
        id=f"ref-{key}-{asset.id}"[:64],
        label=label,
        value=value[:300],
        basis="measured",
        source_asset_id=asset.id,
    )


def _measure_media(
    store: ProjectStore, asset: Asset, limit_us: int
) -> tuple[list[int] | None, dict[str, str]]:
    """One decode pass over at most ``limit_us``: scene cuts (video) and loudness (audio)."""
    args = [str(find_tool("ffmpeg")), "-hide_banner", "-nostats", "-nostdin"]
    args += ["-t", _seconds_arg(limit_us), "-i", str(store.safe_path(asset.relative_path))]
    has_video = asset.video is not None and asset.kind is AssetKind.VIDEO
    if has_video:
        args += ["-map", "0:v:0", "-vf", f"scale=160:-2,select='gt(scene,{SCENE_THRESHOLD})',showinfo"]
    if asset.audio is not None:
        args += ["-map", "0:a:0", "-af", "loudnorm=print_format=json"]
    args += ["-f", "null", "-"]
    try:
        proc = subprocess.run(args, capture_output=True, timeout=ANALYSIS_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired as exc:
        raise EngineException(ErrorCode.QA_FAILED, "measuring the reference timed out") from exc
    if proc.returncode != 0:
        raise EngineException(ErrorCode.QA_FAILED, proc.stderr.decode("utf-8", "replace")[-500:])
    loud: dict[str, str] = {}
    if asset.audio is not None and (match := _LOUDNORM_JSON.search(proc.stderr)):
        try:
            loud = json.loads(match.group(0))
        except json.JSONDecodeError:
            loud = {}
    return (parse_cut_times(proc.stderr) if has_video else None), loud


def _finite(value: str | None) -> str | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if math.isinf(number) or math.isnan(number) else f"{number:.1f}"


def measure_reference(store: ProjectStore, asset_id: str) -> list[StyleObservation]:
    """Measured style notes for one active reference asset of a project."""
    asset = store.active_assets().get(asset_id)
    if asset is None:
        raise EngineException(ErrorCode.ASSET_NOT_FOUND, "reference file is not in this project")
    if asset.role is not AssetRole.REFERENCE:
        raise EngineException(ErrorCode.INVALID_ARGUMENT, "only reference files are measured for style")

    notes: list[StyleObservation] = []
    if asset.video is not None:
        notes.append(_note(asset, "frame", "Frame", _frame_text(asset.video.width, asset.video.height)))
    if asset.kind is AssetKind.IMAGE:
        return notes
    if asset.video is not None:
        rate = asset.video.frame_rate
        vfr = " (variable)" if asset.video.is_vfr else ""
        notes.append(_note(asset, "rate", "Frame rate", _rate_text(rate.num, rate.den) + vfr))
    duration = asset_duration_us(asset)
    if duration is None:
        return notes
    analysed = min(duration, MAX_ANALYSIS_US)
    scope = "" if analysed == duration else f" (first {_seconds_text(analysed)} measured)"
    notes.append(_note(asset, "duration", "Reference length", _seconds_text(duration)))

    cuts, loud = _measure_media(store, asset, analysed)
    if cuts is not None:
        shots = shot_lengths(cuts, analysed)
        notes.append(_note(asset, "cuts", "Cuts", f"{len(shots) - 1} detected{scope}"))
        notes.append(
            _note(asset, "shot", "Average shot length", _seconds_text(sum(shots) // len(shots)) + scope)
        )
        notes.append(
            _note(
                asset,
                "shot-range",
                "Shot length range",
                f"{_seconds_text(min(shots))} to {_seconds_text(max(shots))}{scope}",
            )
        )
    if (lufs := _finite(loud.get("input_i"))) is not None:
        notes.append(_note(asset, "loudness", "Integrated loudness", f"{lufs} LUFS{scope}"))
    if (peak := _finite(loud.get("input_tp"))) is not None:
        notes.append(_note(asset, "peak", "True peak", f"{peak} dBTP{scope}"))
    return notes


def reference_assets(store: ProjectStore) -> list[dict[str, object]]:
    """Active reference files a person may measure."""
    return [
        {
            "id": asset.id,
            "name": asset.original_filename,
            "kind": asset.kind.value,
            "duration_us": asset_duration_us(asset),
        }
        for asset in store.active_assets().values()
        if asset.role is AssetRole.REFERENCE
    ]
