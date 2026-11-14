"""A generated still image can satisfy a request and render as source-linked V2 B-roll."""

from __future__ import annotations

import subprocess
from fractions import Fraction
from pathlib import Path

import pytest
from pydantic import ValidationError

from ageneditor.contracts import (
    Asset,
    AssetChecklist,
    AssetKind,
    AssetOrigin,
    AssetProvenance,
    AssetRequest,
    AssetRole,
    AudioMix,
    Author,
    Clip,
    Provenance,
    RenderProfile,
    Timeline,
    TrackId,
    VideoStream,
    validate_against_assets,
)
from ageneditor.media.hashing import sha256_file
from ageneditor.media.probe import probe
from ageneditor.media.qa import run_qa
from ageneditor.media.ranges import source_interval
from ageneditor.media.render import render_timeline
from ageneditor.media.tools import find_tool
from ageneditor.storage import ProjectStore
from ageneditor.timebase import FrameRate


def test_still_contract_and_output_timebase() -> None:
    asset = Asset(
        id="asset_still",
        project_id="project_still",
        kind=AssetKind.IMAGE,
        role=AssetRole.BROLL,
        relative_path="originals/still.png",
        original_filename="still.png",
        sha256="a" * 64,
        size_bytes=1,
        video=VideoStream(
            index=0,
            codec="png",
            width=270,
            height=480,
            frame_rate=FrameRate(num=25),
            time_base_num=1,
            time_base_den=25,
            duration_frames=1,
        ),
    )
    with pytest.raises(ValidationError):
        Asset.model_validate({**asset.model_dump(mode="json"), "role": "primary"})
    clip = Clip(
        clip_id="still",
        asset_id=asset.id,
        track=TrackId.V2,
        source_in_frames=0,
        source_out_frames=1,
        timeline_in_frames=15,
        timeline_duration_frames=30,
    )
    timeline = Timeline(
        project_id=asset.project_id,
        revision=0,
        target_duration_frames=60,
        render_profile=RenderProfile(id="still", width=180, height=320, frame_rate=FrameRate(num=30)),
        clips=[clip],
        provenance=Provenance(author=Author.USER),
    )
    assert source_interval(asset, clip, Path("unused.png"), Fraction(30)) == (Fraction(0), Fraction(1))
    assert not [
        issue
        for issue in validate_against_assets(timeline, {asset.id: asset})
        if issue.clip_id == clip.clip_id and issue.severity == "error"
    ]
    invalid = timeline.model_copy(update={"clips": [clip.model_copy(update={"source_out_frames": 2})]})
    assert any("[0, 1)" in issue.message for issue in validate_against_assets(invalid, {asset.id: asset}))


def _frame(path: Path, number: int) -> bytes:
    return subprocess.run(
        [
            str(find_tool("ffmpeg")),
            "-v",
            "error",
            "-i",
            str(path),
            "-vf",
            f"select=eq(n\\,{number})",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        capture_output=True,
        check=True,
        timeout=60,
    ).stdout


def _difference(a: bytes, b: bytes) -> float:
    assert len(a) == len(b) and a
    return sum(abs(x - y) for x, y in zip(a, b, strict=True)) / len(a)


@pytest.mark.media
def test_png_import_checklist_overlay_and_qa(tmp_path: Path, media_dir: Path) -> None:
    store = ProjectStore.create(tmp_path, "still image")
    source = media_dir / "still_storyboard_9x16.png"
    original_hash = sha256_file(source)
    primary = store.import_asset(media_dir / "talking_head_16x9_30fps_60s.mp4", AssetRole.PRIMARY)
    image = store.import_asset(
        source,
        AssetRole.BROLL,
        AssetProvenance(origin=AssetOrigin.GENERATED_EXTERNAL, tool_name="ChatGPT"),
    )
    assert image.kind is AssetKind.IMAGE
    assert image.video and image.video.duration_frames == 1
    assert image.sha256 == original_hash

    profile = RenderProfile(id="still_test", width=180, height=320, frame_rate=FrameRate(num=30))
    seed = store.create_timeline(profile, 60)
    checklist = store.save_asset_checklist(
        AssetChecklist(
            project_id=store.project_id,
            configured=True,
            requests=[AssetRequest(id="gen_frame", kind="image", prompt="A coral portrait frame")],
        ),
        0,
    )
    store.link_asset_request("gen_frame", image.id, checklist.version)
    primary_clip = Clip(
        clip_id="primary",
        asset_id=primary.id,
        track=TrackId.V1,
        source_in_frames=0,
        source_out_frames=60,
        timeline_in_frames=0,
    )
    image_clip = Clip(
        clip_id="image",
        asset_id=image.id,
        track=TrackId.V2,
        source_in_frames=0,
        source_out_frames=1,
        timeline_in_frames=15,
        timeline_duration_frames=30,
    )
    invalid = seed.model_copy(
        update={"clips": [primary_clip, image_clip.model_copy(update={"timeline_duration_frames": None})]}
    )
    assert any("explicit output duration" in issue.message for issue in store.validation_issues(invalid))
    timeline = store.commit_timeline(
        seed.model_copy(update={"clips": [primary_clip, image_clip], "audio": AudioMix(normalize=False)}),
        0,
    )
    output = store.safe_path("renders/still_example.mp4")
    render_timeline(store, timeline, output)
    measured = probe(output)
    assert measured.video and measured.video.duration_frames == 60
    assert measured.audio is not None
    assert _difference(_frame(output, 5), _frame(output, 30)) > 20
    assert _difference(_frame(output, 5), _frame(output, 55)) < 12

    report = run_qa(store, "job_still_example", timeline.revision, output)
    assert [(item.asset_id, item.tool_name) for item in report.generated_assets] == [(image.id, "ChatGPT")]
    assert sha256_file(source) == original_hash
    assert sha256_file(store.safe_path(image.relative_path)) == original_hash


def test_reference_assets_import_but_never_reach_a_track(tmp_path: Path, media_dir: Path) -> None:
    store = ProjectStore.create(tmp_path, "references")
    refs = {
        name: store.import_asset(media_dir / name, AssetRole.REFERENCE)
        for name in ("broll_9x16_10s.mp4", "music_bed_30s.m4a", "still_storyboard_9x16.png")
    }
    assert {asset.kind for asset in refs.values()} == {AssetKind.VIDEO, AssetKind.AUDIO, AssetKind.IMAGE}
    video = refs["broll_9x16_10s.mp4"]
    profile = RenderProfile(id="ref_test", width=180, height=320, frame_rate=FrameRate(num=30))
    seed = store.create_timeline(profile, 30)
    for track in (TrackId.V1, TrackId.V2):
        clip = Clip(
            clip_id="ref",
            asset_id=video.id,
            track=track,
            source_in_frames=0,
            source_out_frames=30,
            timeline_in_frames=0,
        )
        issues = validate_against_assets(seed.model_copy(update={"clips": [clip]}), store.assets())
        assert any("cannot be placed" in issue.message for issue in issues), track
