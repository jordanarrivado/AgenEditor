"""Subject-position reframing suggestions (Phase 6) for crops of wide footage into tall frames.

This is **not face detection**: AgenEditor ships no vision model. It measures where the picture has
detail and movement (FFmpeg ``edgedetect`` and frame differences on a 64x36 thumbnail, about two frames a
second) and takes the weighted centre. That usually follows a talking head on a plain background and fails
on busy backgrounds; the confidence says how concentrated the detail is. Suggestions are only suggestions:
the agent applies them as a normal ``set_transform`` revision and the person checks the preview.
"""

from __future__ import annotations

import math
import subprocess
from fractions import Fraction

from ageneditor.contracts import EngineException, ErrorCode, FitMode, Timeline, TrackId
from ageneditor.media.ranges import source_interval
from ageneditor.media.tools import find_tool
from ageneditor.storage import ProjectStore

GRID_W, GRID_H = 64, 36
#: Below this, detail is too spread out to say where the subject is.
MIN_CONFIDENCE = 0.3
#: Do not suggest moves smaller than this share of the slack.
MIN_SHIFT = 0.05
METHOD = "edge_motion_heuristic"


def _energy(path, start: Fraction, end: Fraction, timeout: int) -> list[float]:
    """Summed edge + motion energy per cell of a 64x36 grid over the range."""
    duration = max(end - start, Fraction(1, 2))
    args = [
        str(find_tool("ffmpeg")), "-v", "error", "-ss", f"{float(start):.3f}", "-t", f"{float(duration):.3f}",
        "-i", str(path), "-an",
        "-filter_complex",
        f"[0:v]fps=2,scale={GRID_W}:{GRID_H}:flags=area,format=gray,split[a][b];"
        "[a]edgedetect=low=0.08:high=0.25[e];[b]tblend=all_mode=difference[m];"
        "[e][m]blend=all_expr='A+2*B'",
        "-frames:v", "40", "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]  # fmt: skip
    try:
        result = subprocess.run(args, capture_output=True, timeout=min(timeout, 120), check=False)
    except subprocess.TimeoutExpired as exc:
        raise EngineException(ErrorCode.INVALID_ARGUMENT, "subject measurement timed out") from exc
    size = GRID_W * GRID_H
    data = result.stdout
    if result.returncode != 0 or len(data) < size:
        raise EngineException(ErrorCode.INVALID_ARGUMENT, "could not read frames for reframing")
    totals = [0.0] * size
    for offset in range(0, len(data) - size + 1, size):
        frame = data[offset : offset + size]
        for index, value in enumerate(frame):
            totals[index] += value
    return totals


def _centre(weights: list[float], length: int) -> tuple[float, float]:
    """Weighted centre (0..1) and confidence (1 = all detail in one place, 0 = spread evenly)."""
    total = sum(weights)
    if total <= 0:
        return 0.5, 0.0
    positions = [(i + 0.5) / length for i in range(length)]
    mean = sum(p * w for p, w in zip(positions, weights, strict=True)) / total
    spread = math.sqrt(sum(w * (p - mean) ** 2 for p, w in zip(positions, weights, strict=True)) / total)
    uniform = 1 / math.sqrt(12)  # spread of evenly distributed detail
    return mean, max(0.0, min(1.0, 1 - spread / uniform))


def _crop_value(subject: float, kept: float) -> float:
    """The renderer's crop offset (share of the slack) that centres the crop on ``subject``."""
    if kept >= 1:
        return 0.5
    return min(1.0, max(0.0, (subject - kept / 2) / (1 - kept)))


def suggest_reframe(store: ProjectStore, timeline: Timeline, timeout: int = 120) -> list[dict[str, object]]:
    """One suggestion per V1 clip whose crop cuts the picture and whose subject is off the crop centre."""
    assets = store.assets()
    profile = timeline.render_profile
    fps = profile.frame_rate.fraction
    out_aspect = Fraction(profile.width, profile.height)
    suggestions: list[dict[str, object]] = []
    measured: dict[tuple[str, Fraction, Fraction], tuple[float, float, float, float]] = {}
    primary = sorted((c for c in timeline.clips if c.track is TrackId.V1), key=lambda c: c.timeline_in_frames)
    for clip in primary:
        asset = assets[clip.asset_id]
        if asset.video is None or clip.fit_mode is not FitMode.COVER:
            continue
        src_aspect = Fraction(asset.video.width, asset.video.height)
        scale = clip.transform.scale
        kept_x = float(min(Fraction(1), out_aspect / src_aspect)) / scale
        kept_y = float(min(Fraction(1), src_aspect / out_aspect)) / scale
        if kept_x >= 0.98 and kept_y >= 0.98:
            continue  # nothing is cropped away
        path = store.safe_path(asset.relative_path)
        start, end = source_interval(asset, clip, path, fps)
        key = (asset.id, start, end)
        if key not in measured:
            grid = _energy(path, start, end, timeout)
            columns = [sum(grid[r * GRID_W + c] for r in range(GRID_H)) for c in range(GRID_W)]
            rows = [sum(grid[r * GRID_W : (r + 1) * GRID_W]) for r in range(GRID_H)]
            measured[key] = (*_centre(columns, GRID_W), *_centre(rows, GRID_H))
        subject_x, confidence_x, subject_y, confidence_y = measured[key]
        suggestion: dict[str, object] = {"clip_id": clip.clip_id}
        if kept_x < 0.98:
            target = _crop_value(subject_x, kept_x)
            if confidence_x >= MIN_CONFIDENCE and abs(target - clip.transform.center_x) >= MIN_SHIFT:
                suggestion.update(
                    current_center_x=clip.transform.center_x,
                    suggested_center_x=round(target, 3),
                    confidence_x=round(confidence_x, 2),
                    kept_width=round(kept_x, 3),
                )
        if kept_y < 0.98:
            target = _crop_value(subject_y, kept_y)
            if confidence_y >= MIN_CONFIDENCE and abs(target - clip.transform.center_y) >= MIN_SHIFT:
                suggestion.update(
                    current_center_y=clip.transform.center_y,
                    suggested_center_y=round(target, 3),
                    confidence_y=round(confidence_y, 2),
                    kept_height=round(kept_y, 3),
                )
        if len(suggestion) > 1:
            suggestions.append(suggestion)
    return suggestions
