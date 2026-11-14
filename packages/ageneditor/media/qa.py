"""Technical QA and editorial flags that report only measurements actually performed."""

from __future__ import annotations

import subprocess
import uuid
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path

from ageneditor.contracts import (
    AssetOrigin,
    CheckKind,
    CheckStatus,
    EngineException,
    ErrorCode,
    GeneratedAsset,
    QACheck,
    QAReport,
)
from ageneditor.media.hashing import sha256_file
from ageneditor.media.probe import probe
from ageneditor.media.tools import find_tool
from ageneditor.storage import ProjectStore


def run_qa(store: ProjectStore, job_id: str, revision: int, output: Path) -> QAReport:
    if not output.is_file():
        raise EngineException(ErrorCode.QA_FAILED, "render output does not exist")
    metadata = probe(output)
    try:
        decoded = subprocess.run(
            [str(find_tool("ffmpeg")), "-v", "error", "-i", str(output), "-f", "null", "-"],
            capture_output=True,
            timeout=600,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise EngineException(ErrorCode.QA_FAILED, "full decode check timed out") from exc
    timeline = store.timeline(revision)
    profile = timeline.render_profile
    duration = metadata.duration_seconds or Fraction(0)
    expected_duration = Fraction(timeline.duration_frames * profile.frame_rate.den, profile.frame_rate.num)
    checks = [
        QACheck(
            check_id="decode",
            status=CheckStatus.PASS if decoded.returncode == 0 else CheckStatus.FAIL,
            expected="full decode succeeds",
            measured="success"
            if decoded.returncode == 0
            else decoded.stderr.decode("utf-8", "replace")[-300:],
            message="FFmpeg decoded the complete output"
            if decoded.returncode == 0
            else "FFmpeg reported decode errors",
        ),
        QACheck(
            check_id="resolution",
            status=CheckStatus.PASS
            if metadata.video
            and (metadata.video.width, metadata.video.height) == (profile.width, profile.height)
            else CheckStatus.FAIL,
            expected=f"{profile.width}x{profile.height}",
            measured=f"{metadata.video.width}x{metadata.video.height}"
            if metadata.video
            else "no video stream",
        ),
        QACheck(
            check_id="container",
            status=CheckStatus.PASS if "mp4" in metadata.container.split(",") else CheckStatus.FAIL,
            expected="mp4",
            measured=metadata.container,
        ),
        QACheck(
            check_id="frame_rate",
            status=CheckStatus.PASS
            if metadata.video and metadata.video.frame_rate == profile.frame_rate
            else CheckStatus.FAIL,
            expected=f"{profile.frame_rate.num}/{profile.frame_rate.den}",
            measured=f"{metadata.video.frame_rate.num}/{metadata.video.frame_rate.den}"
            if metadata.video
            else "no video stream",
        ),
        QACheck(
            check_id="frame_count",
            status=CheckStatus.PASS
            if metadata.video and metadata.video.duration_frames == timeline.duration_frames
            else CheckStatus.FAIL,
            expected=str(timeline.duration_frames),
            measured=str(metadata.video.duration_frames) if metadata.video else "no video stream",
        ),
        QACheck(
            check_id="video_codec",
            status=CheckStatus.PASS
            if metadata.video and metadata.video.codec == "h264"
            else CheckStatus.FAIL,
            expected="h264",
            measured=metadata.video.codec if metadata.video else "no video stream",
        ),
        QACheck(
            check_id="audio_track",
            status=CheckStatus.PASS if metadata.audio and metadata.audio.codec == "aac" else CheckStatus.FAIL,
            expected="AAC audio stream",
            measured=metadata.audio.codec if metadata.audio else "no audio stream",
        ),
        QACheck(
            check_id="duration",
            status=CheckStatus.PASS
            if abs(duration - expected_duration) <= Fraction(1, 4)
            else CheckStatus.FAIL,
            expected=str(expected_duration),
            measured=str(duration),
        ),
    ]
    if decoded.returncode == 0 and metadata.audio is not None:
        from ageneditor.editorial import editorial_flags, technical_checks
        from ageneditor.media.analysis import analyze_output

        duration_us = round(duration * 1_000_000)
        analysis = analyze_output(output, max(1, duration_us), has_video=metadata.video is not None)
        checks.extend(technical_checks(store, timeline, analysis))
        checks.extend(editorial_flags(store, timeline))
    else:
        checks.append(
            QACheck(
                check_id="output_analysis",
                status=CheckStatus.SKIPPED,
                message="loudness, gap and caption checks need a decodable output with audio",
            )
        )
    assets = store.assets()
    source_hash_ok = True
    for asset in assets.values():
        try:
            source_hash_ok &= sha256_file(store.safe_path(asset.relative_path)) == asset.sha256
        except (OSError, EngineException):
            source_hash_ok = False
    checks.append(
        QACheck(
            check_id="source_hashes",
            status=CheckStatus.PASS if source_hash_ok else CheckStatus.FAIL,
            expected="all registered originals unchanged",
            measured="match" if source_hash_ok else "mismatch",
        )
    )
    brief_items = store.brief().checklist
    if brief_items:
        open_items = [item.id for item in brief_items if item.status == "open"]
        checks.append(
            QACheck(
                check_id="brief_checklist",
                kind=CheckKind.EDITORIAL,
                status=CheckStatus.WARN if open_items else CheckStatus.PASS,
                expected="every brief item confirmed met or waived by a person",
                measured=f"{len(brief_items) - len(open_items)} of {len(brief_items)} confirmed",
                message=("Open items: " + ", ".join(open_items) + ". " if open_items else "")
                + "Confirmed by a person; AgenEditor did not check the video against the brief.",
            )
        )
    used_ids = {clip.asset_id for clip in timeline.clips}
    generated_assets = [
        GeneratedAsset(
            asset_id=asset.id,
            original_filename=asset.original_filename,
            tool_name=asset.provenance.tool_name or "",
        )
        for asset in sorted(assets.values(), key=lambda item: item.id)
        if asset.id in used_ids and asset.provenance.origin is AssetOrigin.GENERATED_EXTERNAL
    ]
    report = QAReport(
        id=f"qa_{uuid.uuid4().hex[:20]}",
        job_id=job_id,
        project_id=store.project_id,
        timeline_revision=revision,
        checks=checks,
        generated_assets=generated_assets,
        created_at=datetime.now(UTC),
    )
    with store._connect() as db:
        db.execute("INSERT INTO qa_reports VALUES (?, ?)", (report.id, report.model_dump_json()))
    store.write_artifact(f"renders/{job_id}/qa.json", report.model_dump_json(indent=2))
    return report
