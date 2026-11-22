"""Preview QA (roadmap Phase 2c): measured frame-grid, black-bar and caption-over-silence checks."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from ageneditor.contracts import (
    AssetRole,
    AudioMix,
    CaptionCue,
    CheckStatus,
    Clip,
    JobStatus,
    RenderProfile,
    TrackId,
)
from ageneditor.desktop_service import DesktopService
from ageneditor.jobs import JobManager
from ageneditor.media.preview_qa import get_preview_qa, run_preview_qa, sample_frames
from ageneditor.media.tools import find_tool
from ageneditor.storage import ProjectStore
from ageneditor.timebase import FrameRate

PROFILE = RenderProfile(id="pqa", width=180, height=320, frame_rate=FrameRate(num=30))


def _letterboxed_then_black(path: Path) -> Path:
    """2 s, 180x320: first second a test pattern with black bars and a tone, then black and silent."""
    subprocess.run(
        [
            str(find_tool("ffmpeg")), "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=180x240:rate=30:duration=1",
            "-f", "lavfi", "-i", "color=black:size=180x320:rate=30:duration=1",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=1",
            "-filter_complex",
            "[0]pad=180:320:0:40:black,setsar=1[top];[1]setsar=1[black];[top][black]concat=n=2:v=1:a=0[v];"
            "[2][3]concat=n=2:v=0:a=1,aformat=channel_layouts=stereo[a]",
            "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
        ],
        check=True,
        timeout=120,
    )  # fmt: skip
    return path


def _render(store: ProjectStore) -> str:
    manager = JobManager(store)
    job = manager.start_render(store.current_revision(), final=False)
    deadline = time.monotonic() + 300
    while manager.status(job.id).status in {JobStatus.QUEUED, JobStatus.RUNNING}:
        assert time.monotonic() < deadline
        time.sleep(0.2)
    assert manager.status(job.id).status is JobStatus.SUCCEEDED, manager.status(job.id).error
    return job.id


def test_sampling_takes_clip_middles_and_tops_up_to_six_frames() -> None:
    from ageneditor.contracts import Timeline
    from ageneditor.contracts.timeline import Author, Provenance

    def timeline(spans: list[int]) -> Timeline:
        clips, at = [], 0
        for n, span in enumerate(spans):
            clips.append(
                Clip(clip_id=f"c{n}", asset_id="a", track=TrackId.V1, source_in_frames=0,
                     source_out_frames=span, timeline_in_frames=at)
            )  # fmt: skip
            at += span
        return Timeline(
            project_id="p", revision=1, target_duration_frames=at, render_profile=PROFILE, clips=clips,
            provenance=Provenance(author=Author.AGENT),
        )  # fmt: skip

    assert sample_frames(timeline([60])) == [5, 15, 25, 29, 35, 45, 55]
    many = sample_frames(timeline([10] * 30))
    assert len(many) == 12 and many[0] == 4 and many[-1] == 294


@pytest.mark.media
def test_preview_qa_flags_bars_flat_frames_and_captions_over_silence(tmp_path: Path) -> None:
    (tmp_path / "ws").mkdir()
    store = ProjectStore.create(tmp_path / "ws", "preview qa")
    source = store.import_asset(_letterboxed_then_black(tmp_path / "boxed.mp4"), AssetRole.PRIMARY)
    seed = store.create_timeline(PROFILE, 60)
    clip = Clip(
        clip_id="only", asset_id=source.id, track=TrackId.V1,
        source_in_frames=0, source_out_frames=60, timeline_in_frames=0,
    )  # fmt: skip
    captions = [
        CaptionCue(cue_id="loud", text="Tone", timeline_in_frames=5, timeline_out_frames=25,
                   source_asset_id=source.id, corrected=True),
        # Between sampled frames 45 and 55, so the burned-in text does not stop those frames being flat.
        CaptionCue(cue_id="quiet", text="Nothing", timeline_in_frames=48, timeline_out_frames=54,
                   source_asset_id=source.id, corrected=True),
    ]  # fmt: skip
    store.commit_timeline(
        seed.model_copy(update={"clips": [clip], "captions": captions, "audio": AudioMix(normalize=False)}), 0
    )
    job_id = _render(store)

    report = run_preview_qa(store, job_id)
    checks = {check.check_id: check for check in report.checks}
    assert checks["preview_format"].status is CheckStatus.PASS
    assert checks["black_bars"].status is CheckStatus.WARN
    assert "picture 180x240" in checks["black_bars"].measured
    assert checks["flat_frames"].status is CheckStatus.WARN
    assert checks["flat_frames"].measured == "1.17s; 1.50s; 1.83s"
    assert checks["caption_audio"].status is CheckStatus.WARN
    assert checks["caption_audio"].measured == "quiet at 1.60s"
    assert report.overall is CheckStatus.WARN
    assert report.frame_grid_path is not None and store.safe_path(report.frame_grid_path).is_file()
    assert get_preview_qa(store, job_id) == report


@pytest.mark.media
def test_a_clean_preview_passes_and_reaches_the_desktop(tmp_path: Path, media_dir: Path) -> None:
    store = ProjectStore.create(tmp_path, "clean preview")
    primary = store.import_asset(media_dir / "talking_head_16x9_30fps_60s.mp4", AssetRole.PRIMARY)
    seed = store.create_timeline(PROFILE, 60)
    clips = [
        Clip(clip_id="a", asset_id=primary.id, track=TrackId.V1, source_in_frames=0, source_out_frames=30,
             timeline_in_frames=0),
        Clip(clip_id="b", asset_id=primary.id, track=TrackId.V1, source_in_frames=300, source_out_frames=330,
             timeline_in_frames=30),
    ]  # fmt: skip
    store.commit_timeline(seed.model_copy(update={"clips": clips}), 0)
    job_id = _render(store)

    service = DesktopService(tmp_path)
    try:
        args = {"project_id": store.project_id, "job_id": job_id}
        assert service._desktop_call("preview_qa", args) == {"report": None, "grid_path": None}
        result = service._desktop_call("preview_qa", {**args, "run": True})
    finally:
        service.http.server_close()
    statuses = {check["check_id"]: check["status"] for check in result["report"]["checks"]}
    assert statuses == {
        "preview_format": "pass",
        "flat_frames": "pass",
        "black_bars": "pass",
        "caption_audio": "skipped",
    }
    assert result["report"]["overall"] == "pass"
    assert Path(result["grid_path"]).is_file()
