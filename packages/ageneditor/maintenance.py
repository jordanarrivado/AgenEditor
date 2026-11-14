"""Crash hygiene and preflight checks (Phase 5): disk space, leftover cleanup and project health.

Cleanup only ever deletes engine-owned temporary files. It never deletes or rewrites anything under
``originals/``, completed renders, timelines, transcripts or the project database; those are reported.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from pathlib import Path

from ageneditor.contracts import EngineException, ErrorCode, Job, JobStatus, Timeline
from ageneditor.media.hashing import sha256_file
from ageneditor.storage import ProjectStore

#: Free space kept back for the OS, SQLite WAL and logs.
RESERVE_BYTES = 256 * 1024 * 1024
#: Deliberately generous H.264 estimate (bits per pixel per frame) plus AAC audio.
_BITS_PER_PIXEL = Fraction(3, 10)
_AUDIO_BPS = 320_000
#: Engine-owned temporaries left behind by a crash or kill (relative to the project root).
_TEMP_PATTERNS = (
    re.compile(r"^renders/[^/]+/\.[^/]+\.\d+\.tmp\.mp4$"),
    re.compile(r"^renders/[^/]+/\.captions\.r\d+\.ass$"),
    re.compile(r"^renders/[^/]+/\.captionfonts\.r\d+/[^/]+\.ttf$"),
    re.compile(r"^transcripts/[^/]+\.pending\.json$"),
    re.compile(r"^(timelines|transcripts|renders|reports)/.+\.tmp$"),
)


def free_bytes(path: Path) -> int:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def ensure_free_space(path: Path, needed_bytes: int, purpose: str) -> None:
    """Raise ``INSUFFICIENT_DISK`` before work starts, rather than failing half-way through."""
    try:
        free = free_bytes(path)
    except OSError:
        return  # Unknown free space is not a reason to refuse; the operation reports real I/O errors.
    required = needed_bytes + RESERVE_BYTES
    if free < required:
        raise EngineException(
            ErrorCode.INSUFFICIENT_DISK,
            f"not enough free disk space for {purpose}",
            required_bytes=required,
            free_bytes=free,
        )


def estimate_render_bytes(timeline: Timeline) -> int:
    profile = timeline.render_profile
    seconds = Fraction(timeline.duration_frames, 1) / profile.frame_rate.fraction
    video_bits = profile.width * profile.height * profile.frame_rate.fraction * _BITS_PER_PIXEL * seconds
    return int((video_bits + _AUDIO_BPS * seconds) / 8)


def ensure_render_space(directory: Path, timeline: Timeline) -> None:
    # The temp file is published by rename in the same directory, so one output's worth is needed.
    ensure_free_space(directory, estimate_render_bytes(timeline), "this render")


@dataclass
class Leftover:
    path: str
    size_bytes: int
    reason: str


@dataclass
class CleanupReport:
    removed: list[Leftover] = field(default_factory=list)
    kept_for_review: list[Leftover] = field(default_factory=list)
    dry_run: bool = True

    @property
    def reclaimed_bytes(self) -> int:
        return sum(item.size_bytes for item in self.removed)

    def to_dict(self) -> dict:
        return {**asdict(self), "reclaimed_bytes": self.reclaimed_bytes}


def _jobs(store: ProjectStore) -> dict[str, Job]:
    with store._connect() as db:
        rows = db.execute("SELECT data FROM jobs").fetchall()
    return {job.id: job for row in rows if (job := Job.model_validate_json(row[0]))}


def _redirected(path: Path, root: Path) -> bool:
    """Symlink, NTFS junction, or anything that resolves outside the project."""
    try:
        return path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(root)
    except OSError:
        return True


def _walk(store: ProjectStore, top: str) -> tuple[list[tuple[str, Path]], list[str]]:
    """Files under ``top`` that physically live in the project, plus redirected entries skipped.

    ``os.walk(followlinks=False)`` still descends into Windows junctions, so every directory and file
    is checked explicitly; cleanup must never delete through a link.
    """
    base = store.root / top
    if not base.is_dir() or _redirected(base, store.root):
        return [], ([top] if base.exists() and _redirected(base, store.root) else [])
    found, skipped = [], []
    for directory, dirnames, filenames in os.walk(base, followlinks=False):
        kept = []
        for name in dirnames:
            candidate = Path(directory) / name
            if _redirected(candidate, store.root):
                skipped.append(candidate.relative_to(store.root).as_posix())
            else:
                kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            path = Path(directory) / name
            relative = path.relative_to(store.root).as_posix()
            if _redirected(path, store.root):
                skipped.append(relative)
            else:
                found.append((relative, path))
    return found, skipped


def cleanup_project(store: ProjectStore, apply: bool = False) -> CleanupReport:
    """Remove crash leftovers. Active (queued/running) jobs' directories are never touched."""
    jobs = _jobs(store)
    active = {job_id for job_id, job in jobs.items() if job.status in {JobStatus.QUEUED, JobStatus.RUNNING}}
    report = CleanupReport(dry_run=not apply)
    for top in ("renders", "transcripts", "timelines", "reports"):
        files, redirected = _walk(store, top)
        report.kept_for_review += [
            Leftover(r, 0, "link or junction inside project (not followed)") for r in redirected
        ]
        for relative, path in files:
            parts = relative.split("/")
            if top == "renders" and len(parts) > 2 and parts[1] in active:
                continue
            size = path.stat().st_size
            if any(pattern.match(relative) for pattern in _TEMP_PATTERNS):
                report.removed.append(Leftover(relative, size, "temporary file left by an interrupted job"))
                if apply:
                    path.unlink(missing_ok=True)
                    if path.parent.name.startswith(".captionfonts.r"):
                        try:
                            path.parent.rmdir()  # succeeds once the last font is gone
                        except OSError:
                            pass
            elif top == "renders" and len(parts) > 2 and parts[1] not in jobs:
                report.kept_for_review.append(Leftover(relative, size, "render output with no job record"))
    assets = {asset.relative_path for asset in store.assets().values()}
    originals, redirected = _walk(store, "originals")
    report.kept_for_review += [
        Leftover(r, 0, "link or junction inside project (not followed)") for r in redirected
    ]
    for relative, path in originals:
        if relative not in assets:
            report.kept_for_review.append(
                Leftover(relative, path.stat().st_size, "original with no asset record")
            )
    return report


@dataclass
class HealthCheck:
    check: str
    ok: bool
    detail: str


def check_project(store: ProjectStore, verify_hashes: bool = False) -> list[HealthCheck]:
    """Read-only project health report (``ageneditor doctor``)."""
    checks: list[HealthCheck] = []
    try:
        with store._connect() as db:
            result = db.execute("PRAGMA quick_check").fetchone()[0]
        checks.append(HealthCheck("database", result == "ok", f"SQLite quick_check: {result}"))
    except sqlite3.Error as exc:
        checks.append(HealthCheck("database", False, f"SQLite error: {type(exc).__name__}"))
        return checks
    current = store.current_revision()
    with store._connect() as db:
        revisions = [row[0] for row in db.execute("SELECT revision FROM timelines ORDER BY revision")]
    contiguous = revisions == list(range(len(revisions))) and current in revisions
    checks.append(
        HealthCheck("timeline_history", contiguous, f"{len(revisions)} revisions, current {current}")
    )
    for asset in store.assets().values():
        try:
            path = store.safe_path(asset.relative_path)
        except EngineException as exc:
            checks.append(HealthCheck(f"asset:{asset.id}", False, exc.error.message))
            continue
        if not path.is_file():
            checks.append(HealthCheck(f"asset:{asset.id}", False, "original file is missing"))
            continue
        problems = []
        if os.access(path, os.W_OK):
            problems.append("original is writable (expected read-only)")
        if path.stat().st_size != asset.size_bytes:
            problems.append("size differs from registration")
        elif verify_hashes and sha256_file(path) != asset.sha256:
            problems.append("SHA-256 differs from registration")
        detail = "; ".join(problems) or ("hash verified" if verify_hashes else "present, size matches")
        checks.append(HealthCheck(f"asset:{asset.id}", not problems, detail))
    jobs = _jobs(store).values()
    stuck = [job.id for job in jobs if job.status in {JobStatus.QUEUED, JobStatus.RUNNING}]
    checks.append(
        HealthCheck(
            "jobs_in_flight",
            True,
            f"{len(stuck)} queued/running (marked interrupted when the engine next starts)",
        )
    )
    missing = [
        job.id
        for job in jobs
        if job.status is JobStatus.SUCCEEDED
        and job.output_path
        and not store.root.joinpath(job.output_path).is_file()
    ]
    checks.append(
        HealthCheck(
            "render_outputs", not missing, f"missing outputs: {', '.join(missing)}" if missing else "ok"
        )
    )
    leftovers = cleanup_project(store, apply=False)
    checks.append(
        HealthCheck(
            "leftovers",
            not leftovers.removed,
            f"{len(leftovers.removed)} temporary files ({leftovers.reclaimed_bytes} bytes), "
            f"{len(leftovers.kept_for_review)} items for review",
        )
    )
    return checks
