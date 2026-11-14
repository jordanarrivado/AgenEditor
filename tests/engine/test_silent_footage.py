"""Main footage may have no audio track at all: it must render, and QA must say what it could not measure."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from ageneditor.contracts import AssetRole, CheckStatus, Clip, JobStatus, RenderProfile, TrackId
from ageneditor.jobs import JobManager
from ageneditor.media.qa import run_qa
from ageneditor.storage import ProjectStore
from ageneditor.timebase import FrameRate


@pytest.mark.media
def test_video_without_audio_renders_as_main_footage_and_qa_reports_silence(
    tmp_path: Path, media_dir: Path
) -> None:
    store = ProjectStore.create(tmp_path, "no audio")
    shutil.copyfile(media_dir / "broll_9x16_10s.mp4", store.safe_path("imports/footage.mp4"))
    asset = store.register_import("footage.mp4", AssetRole.PRIMARY)
    assert asset.video is not None and asset.audio is None

    profile = RenderProfile(id="silent_footage", width=180, height=320, frame_rate=FrameRate(num=30))
    seed = store.create_timeline(profile, target_duration_frames=60)
    clip = Clip(
        clip_id="cut_0",
        asset_id=asset.id,
        track=TrackId.V1,
        source_in_frames=0,
        source_out_frames=60,
        timeline_in_frames=0,
    )
    committed = store.commit_timeline(seed.model_copy(update={"clips": [clip]}), expected_revision=0)

    manager = JobManager(store)
    job = manager.start_render(committed.revision)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        job = manager.status(job.id)
        if job.is_terminal:
            break
        time.sleep(0.2)
    assert job.status is JobStatus.SUCCEEDED, job.error

    report = run_qa(store, job.id, committed.revision, store.safe_path(job.output_path))
    checks = {check.check_id: check for check in report.checks}
    assert checks["audio_track"].status is CheckStatus.PASS  # the file still carries a (silent) AAC track
    # Silence is reported as unmeasurable, never as a measured loudness or a pass.
    assert checks["loudness"].status is CheckStatus.WARN
    assert "silent" in checks["loudness"].measured
    assert report.overall is CheckStatus.WARN  # a warning to review, not a technical failure
