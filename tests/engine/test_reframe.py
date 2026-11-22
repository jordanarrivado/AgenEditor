"""Phase 6: subject-position reframing suggestions (edge/motion heuristic, not face detection)."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from mcp import Client

from ageneditor.contracts import AssetRole, Clip, RenderProfile, TrackId
from ageneditor.mcp_server import build_server
from ageneditor.media.tools import find_tool
from ageneditor.reframe import _crop_value
from ageneditor.storage import ProjectStore
from ageneditor.timebase import FrameRate

PROFILE = RenderProfile(id="reframe", width=180, height=320, frame_rate=FrameRate(num=30))


def _wide(path: Path, video: str) -> Path:
    """2 s of 320x180 (16:9) footage with a tone, from an FFmpeg lavfi video description."""
    subprocess.run(
        [str(find_tool("ffmpeg")), "-v", "error", "-y", "-f", "lavfi", "-i", video,
         "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=48000:duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True, timeout=120,
    )  # fmt: skip
    return path


def _moving_box(x: str) -> str:
    return (
        "color=black:size=320x180:rate=30:duration=2,"
        f"drawbox=x='{x}+12*sin(t*6)':y=60:w=40:h=60:color=white:t=fill"
    )


def test_crop_value_centres_the_crop_on_the_subject() -> None:
    assert _crop_value(0.5, 0.316) == pytest.approx(0.5)
    assert _crop_value(0.75, 0.316) == pytest.approx((0.75 - 0.158) / 0.684)
    assert _crop_value(0.05, 0.316) == 0.0  # clamped: the crop cannot leave the frame
    assert _crop_value(0.3, 1.0) == 0.5


@pytest.mark.media
def test_suggestions_follow_the_subject_and_skip_busy_frames(tmp_path: Path) -> None:
    right = _wide(tmp_path / "right.mp4", _moving_box("220"))
    left = _wide(tmp_path / "left.mp4", _moving_box("50"))
    busy = _wide(tmp_path / "busy.mp4", "color=gray:size=320x180:rate=30:duration=2,noise=alls=80:allf=t")
    store = ProjectStore.create(tmp_path, "reframe")
    assets = [store.import_asset(path, AssetRole.PRIMARY) for path in (right, left, busy)]
    seed = store.create_timeline(PROFILE, 90)
    clips = [
        Clip(clip_id=name, asset_id=asset.id, track=TrackId.V1, source_in_frames=0, source_out_frames=30,
             timeline_in_frames=30 * n)
        for n, (name, asset) in enumerate(zip(("right", "left", "busy"), assets, strict=True))
    ]  # fmt: skip
    timeline = store.commit_timeline(seed.model_copy(update={"clips": clips}), 0)

    async def run() -> dict:
        async with Client(build_server(tmp_path), raise_exceptions=True) as client:
            result = await client.call_tool("suggest_reframe", {"project_id": store.project_id})
            return result.structured_content

    result = asyncio.run(run())
    assert result["method"] == "edge_motion_heuristic" and result["revision"] == timeline.revision
    by_clip = {item["clip_id"]: item for item in result["suggestions"]}
    # The box is centred at about x = 240/320 = 0.75 (right) and 70/320 = 0.22 (left) of the frame.
    kept = by_clip["right"]["kept_width"]
    assert kept == pytest.approx(0.316, abs=0.01)
    assert by_clip["right"]["suggested_center_x"] == pytest.approx(_crop_value(0.75, kept), abs=0.08)
    assert by_clip["left"]["suggested_center_x"] == pytest.approx(_crop_value(0.22, kept), abs=0.08)
    assert by_clip["right"]["confidence_x"] >= 0.3
    assert "busy" not in by_clip  # detail everywhere: no confident subject position
