"""Preview QA (roadmap Phase 2c): frame grid, black-bar and caption-over-silence checks on a render.

Everything is measured on the rendered file with FFmpeg (``select``, ``signalstats``, ``cropdetect``,
``tile`` and ``silencedetect``); nothing is inferred from the timeline alone. The checks only warn: they
point a person or an agent at frames to look at before the final render and never block anything.
"""

from __future__ import annotations

import re
import subprocess
import uuid
from fractions import Fraction
from pathlib import Path

from ageneditor.contracts import (
    CheckKind,
    CheckStatus,
    EngineException,
    ErrorCode,
    FitMode,
    Job,
    JobKind,
    JobStatus,
    PreviewQAReport,
    QACheck,
    Timeline,
    TrackId,
)
from ageneditor.media.probe import probe
from ageneditor.media.tools import find_tool
from ageneditor.storage import ProjectStore

MAX_FRAMES = 12
MIN_FRAMES = 6
#: Luma range below which a sampled frame is treated as flat (black or a single colour).
FLAT_LUMA_RANGE = 12
#: A detected picture smaller than this share of the frame on either axis counts as black bars.
BARS_SHARE = Fraction(92, 100)
#: A caption counts as "over silence" when at least this share of its time is silent in the render.
SILENT_CAPTION_SHARE = Fraction(3, 4)
SILENCE_NOISE_DB = -40
THUMB_HEIGHT = 240


def job_timeline(store: ProjectStore, job: Job) -> Timeline:
    """The exact timeline a render job was made from (main or variant, preview profile for previews)."""
    from ageneditor.jobs import preview_profile

    if job.timeline_revision is None:
        raise EngineException(ErrorCode.QA_FAILED, "the job is not a render")
    if job.variant_id is not None:
        from ageneditor.variants import VariantService

        view = VariantService(store).view(job.variant_id, job.timeline_revision, job.variant_revision)
        if view.timeline is None:
            raise EngineException(ErrorCode.QA_FAILED, "the variant can no longer be computed")
        timeline = view.timeline
    else:
        timeline = store.timeline(job.timeline_revision)
    if job.kind is JobKind.PREVIEW_RENDER:
        timeline = timeline.model_copy(update={"render_profile": preview_profile(timeline.render_profile)})
    return timeline


def sample_frames(timeline: Timeline) -> list[int]:
    """The middle of each primary clip (thinned to 12), topped up with evenly spaced frames to at least 6."""
    total = timeline.duration_frames
    if total <= 0:
        return []
    v1 = sorted((c for c in timeline.clips if c.track is TrackId.V1), key=lambda c: c.timeline_in_frames)
    mids = [(c.timeline_in_frames + c.timeline_out_frames - 1) // 2 for c in v1]
    if len(mids) > MAX_FRAMES:
        mids = [mids[round(i * (len(mids) - 1) / (MAX_FRAMES - 1))] for i in range(MAX_FRAMES)]
    frames = set(mids)
    wanted = min(MIN_FRAMES, total)
    if len(frames) < wanted:
        frames |= {(2 * i + 1) * total // (2 * wanted) for i in range(wanted)}
    return sorted(frames)[:MAX_FRAMES]


def _run(args: list[str], cwd: Path, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise EngineException(ErrorCode.QA_FAILED, "preview check timed out") from exc


def _select(frames: list[int]) -> str:
    return "select='" + "+".join(f"eq(n,{frame})" for frame in frames) + "'"


def _frame_stats(output: Path, frames: list[int]) -> list[dict[str, float]]:
    """signalstats + cropdetect values for each sampled frame, in order."""
    stats = output.parent / f".{output.stem}.preview_stats.txt"
    result = _run(
        [
            str(find_tool("ffmpeg")), "-v", "error", "-y", "-i", output.name, "-an",
            "-vf", f"{_select(frames)},signalstats,cropdetect=limit=24:round=2:reset=1:skip=0,"
            f"metadata=mode=print:file={stats.name}",
            "-f", "null", "-",
        ],
        output.parent,
    )  # fmt: skip
    if result.returncode != 0 or not stats.is_file():
        raise EngineException(ErrorCode.QA_FAILED, "could not read frames from the render")
    blocks: list[dict[str, float]] = []
    for line in stats.read_text(encoding="utf-8").splitlines():
        if line.startswith("frame:"):
            blocks.append({})
        elif "=" in line and blocks:
            key, _, value = line.partition("=")
            try:
                blocks[-1][key.rsplit(".", 1)[-1]] = float(value)
            except ValueError:
                continue
    stats.unlink(missing_ok=True)
    return blocks


def _frame_grid(output: Path, frames: list[int], target: Path) -> bool:
    columns = min(4, len(frames))
    rows = -(-len(frames) // columns)
    result = _run(
        [
            str(find_tool("ffmpeg")), "-v", "error", "-y", "-i", output.name, "-an",
            "-vf", f"{_select(frames)},scale=-2:{THUMB_HEIGHT},tile={columns}x{rows}:padding=4:color=black",
            "-frames:v", "1", "-fps_mode", "passthrough", str(target.relative_to(output.parent)),
        ],
        output.parent,
    )  # fmt: skip
    return result.returncode == 0 and target.is_file()


def _silences(output: Path, duration: Fraction) -> list[tuple[Fraction, Fraction]]:
    result = _run(
        [
            str(find_tool("ffmpeg")), "-v", "info", "-i", output.name, "-vn",
            "-af", f"silencedetect=noise={SILENCE_NOISE_DB}dB:d=0.25", "-f", "null", "-",
        ],
        output.parent,
    )  # fmt: skip
    if result.returncode != 0:
        raise EngineException(ErrorCode.QA_FAILED, "could not read the render's audio")
    spans: list[tuple[Fraction, Fraction]] = []
    start: Fraction | None = None
    for match in re.finditer(r"silence_(start|end): (-?[\d.]+)", result.stderr):
        value = max(Fraction(0), Fraction(match.group(2)))
        if match.group(1) == "start":
            start = value
        elif start is not None:
            spans.append((start, value))
            start = None
    if start is not None:
        spans.append((start, duration))
    return spans


def _seconds(frame: int, fps: Fraction) -> str:
    return f"{float(frame / fps):.2f}s"


def run_preview_qa(store: ProjectStore, job_id: str) -> PreviewQAReport:
    job = store.get_job(job_id)
    if job.status is not JobStatus.SUCCEEDED or not job.output_path:
        raise EngineException(ErrorCode.QA_FAILED, "preview QA needs a finished render")
    timeline = job_timeline(store, job)
    output = store.safe_path(job.output_path)
    if not output.is_file():
        raise EngineException(ErrorCode.QA_FAILED, "render output does not exist")
    profile = timeline.render_profile
    fps = profile.frame_rate.fraction
    metadata = probe(output)
    checks: list[QACheck] = []

    def check(check_id: str, problems: list[str], expected: str, ok: str, message: str) -> None:
        checks.append(
            QACheck(
                check_id=check_id,
                kind=CheckKind.TECHNICAL,
                status=CheckStatus.WARN if problems else CheckStatus.PASS,
                expected=expected[:200],
                measured=("; ".join(problems[:6]) if problems else ok)[:200],
                message=message if problems else "",
            )
        )

    expected_seconds = Fraction(timeline.duration_frames) / fps
    format_problems = []
    if metadata.video is None:
        format_problems.append("no video stream")
    else:
        if (metadata.video.width, metadata.video.height) != (profile.width, profile.height):
            format_problems.append(f"{metadata.video.width}x{metadata.video.height}")
        if metadata.video.frame_rate.fraction != fps:
            format_problems.append(f"{metadata.video.frame_rate.num}/{metadata.video.frame_rate.den} fps")
    duration = metadata.duration_seconds or Fraction(0)
    if abs(duration - expected_seconds) > 1 / fps:
        format_problems.append(f"{float(duration):.2f}s long")
    check(
        "preview_format",
        format_problems,
        f"{profile.width}x{profile.height}, {fps} fps, {float(expected_seconds):.2f}s",
        "matches",
        "the render does not match the timeline it was made from",
    )

    frames = sample_frames(timeline)
    grid_path: str | None = None
    if metadata.video is not None and frames:
        stats = _frame_stats(output, frames)
        grid = output.parent / "frame_grid.png"
        if _frame_grid(output, frames, grid):
            grid_path = str(Path(job.output_path).parent / "frame_grid.png").replace("\\", "/")
        flat, bars = [], []
        for frame, values in zip(frames, stats, strict=False):
            spread = values.get("YMAX", 255) - values.get("YMIN", 0)
            if spread < FLAT_LUMA_RANGE:
                flat.append(_seconds(frame, fps))
                continue
            width, height = values.get("w", profile.width), values.get("h", profile.height)
            covering = [
                c
                for c in timeline.clips
                if c.track in {TrackId.V1, TrackId.V2}
                and c.timeline_in_frames <= frame < c.timeline_out_frames
            ]
            expected_bars = any(c.fit_mode is FitMode.CONTAIN for c in covering)
            if not expected_bars and (
                width < profile.width * BARS_SHARE or height < profile.height * BARS_SHARE
            ):
                bars.append(f"{_seconds(frame, fps)} picture {int(width)}x{int(height)}")
        check(
            "flat_frames",
            flat,
            f"no black or single-colour frames among {len(frames)} sampled",
            f"{len(frames)} frames sampled, none flat",
            "a sampled frame is black or a single colour; look at the frame grid",
        )
        check(
            "black_bars",
            bars,
            "picture fills the frame where clips use cover",
            "no bars detected",
            "black bars detected where the clip should fill the frame; check the crop",
        )
    else:
        checks.append(
            QACheck(check_id="flat_frames", status=CheckStatus.SKIPPED, message="no video frames to sample")
        )

    if not timeline.captions:
        checks.append(
            QACheck(check_id="caption_audio", status=CheckStatus.SKIPPED, message="the edit has no captions")
        )
    elif metadata.audio is None:
        checks.append(
            QACheck(check_id="caption_audio", status=CheckStatus.SKIPPED, message="the render has no audio")
        )
    else:
        silences = _silences(output, duration)
        quiet = []
        for cue in timeline.captions:
            start = Fraction(cue.timeline_in_frames) / fps
            end = Fraction(cue.timeline_out_frames) / fps
            silent = sum((max(Fraction(0), min(end, b) - max(start, a)) for a, b in silences), Fraction(0))
            if silent >= (end - start) * SILENT_CAPTION_SHARE:
                quiet.append(f"{cue.cue_id} at {_seconds(cue.timeline_in_frames, fps)}")
        check(
            "caption_audio",
            quiet,
            f"audio above {SILENCE_NOISE_DB} dB while each caption shows",
            f"{len(timeline.captions)} captions over sound",
            "a caption shows while the render is silent: captions may be out of sync. This measures "
            "sound, not words, so music under a caption also counts as sound",
        )

    report = PreviewQAReport(
        id=f"pqa_{uuid.uuid4().hex[:20]}",
        job_id=job.id,
        project_id=store.project_id,
        timeline_revision=job.timeline_revision or 0,
        variant_id=job.variant_id,
        variant_revision=job.variant_revision,
        frames=frames,
        frame_grid_path=grid_path,
        checks=checks,
    )
    with store._connect() as db:
        db.execute(
            "INSERT OR REPLACE INTO preview_qa_reports VALUES (?, ?)",
            # ``overall`` is computed from the checks, so it is not stored.
            (job.id, report.model_dump_json(exclude={"overall"})),
        )
    store.write_artifact(
        f"{Path(job.output_path).parent.as_posix()}/preview_qa.json", report.model_dump_json(indent=2)
    )
    return report


def get_preview_qa(store: ProjectStore, job_id: str) -> PreviewQAReport | None:
    with store._connect() as db:
        row = db.execute("SELECT data FROM preview_qa_reports WHERE job_id=?", (job_id,)).fetchone()
    return None if row is None else PreviewQAReport.model_validate_json(row[0])
