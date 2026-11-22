"""Phase 6: OpenTimelineIO export/import, including the reference library parser."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest
from mcp import Client

from ageneditor.contracts import AssetRole, Clip, RenderProfile, TrackId
from ageneditor.contracts.transition import Transition
from ageneditor.desktop_service import DesktopService
from ageneditor.mcp_server import build_server
from ageneditor.media.tools import find_tool
from ageneditor.otio import export_otio, import_otio
from ageneditor.storage import ProjectStore
from ageneditor.timebase import FrameRate

PROFILE = RenderProfile(id="otio", width=180, height=320, frame_rate=FrameRate(num=30))


def _beep(path: Path) -> Path:
    subprocess.run(
        [str(find_tool("ffmpeg")), "-v", "error", "-y", "-f", "lavfi",
         "-i", "sine=frequency=900:sample_rate=48000:duration=1", "-ac", "2", str(path)],
        check=True, timeout=60,
    )  # fmt: skip
    return path


@pytest.fixture
def project(tmp_path: Path, media_dir: Path) -> dict:
    store = ProjectStore.create(tmp_path, "otio")
    primary = store.import_asset(media_dir / "talking_head_16x9_30fps_60s.mp4", AssetRole.PRIMARY)
    broll = store.import_asset(media_dir / "broll_9x16_10s.mp4", AssetRole.BROLL)
    music = store.import_asset(media_dir / "music_bed_30s.m4a", AssetRole.MUSIC)
    sfx = store.import_asset(_beep(tmp_path / "beep.wav"), AssetRole.SFX)
    voice = store.import_asset(_beep(tmp_path / "voice.wav"), AssetRole.VOICE)
    seed = store.create_timeline(PROFILE, 90)

    def clip(clip_id, asset, track, src, length, at, **extra):
        return Clip(clip_id=clip_id, asset_id=asset.id, track=track, source_in_frames=src,
                    source_out_frames=src + length, timeline_in_frames=at, **extra)  # fmt: skip

    clips = [
        clip("a", primary, TrackId.V1, 60, 45, 0, rationale="opening line"),
        clip("b", primary, TrackId.V1, 300, 45, 45, gain_db=-3.0),
        clip("shot", broll, TrackId.V2, 0, 20, 30),
        clip("bed", music, TrackId.A2, 0, 90, 0, gain_db=-12.0),
        clip("hit1", sfx, TrackId.A3, 0, 15, 40),
        clip("hit2", sfx, TrackId.A3, 0, 15, 50),  # overlaps hit1
        clip("line", voice, TrackId.A4, 0, 20, 60),
    ]
    transition = Transition(after_clip_id="a", preset="cross_dissolve", duration_frames=10)
    main = store.commit_timeline(seed.model_copy(update={"clips": clips, "transitions": [transition]}), 0)
    return {"store": store, "main": main, "primary": primary}


def _seconds(rt: dict) -> Fraction:
    return Fraction(str(rt["value"])) / Fraction(str(rt["rate"]))


def test_export_writes_otio_tracks_with_exact_timing(project: dict) -> None:
    store: ProjectStore = project["store"]
    document = json.loads(export_otio(store, project["main"], "Launch"))
    assert document["OTIO_SCHEMA"] == "Timeline.1" and document["name"] == "Launch"
    tracks = document["tracks"]["children"]
    assert document["tracks"]["OTIO_SCHEMA"] == "Stack.1"
    assert [(t["name"], t["kind"]) for t in tracks] == [
        ("V1 primary", "Video"),
        ("V2 overlays", "Video"),
        ("A1 speaker (V1 audio)", "Audio"),
        ("A2 music", "Audio"),
        ("A4 voice", "Audio"),
        ("A3 sound effects 1", "Audio"),
        ("A3 sound effects 2", "Audio"),  # overlapping effects need two lanes
    ]
    for track in tracks:
        assert track["OTIO_SCHEMA"] == "Track.1"
        for item in track["children"]:
            assert item["OTIO_SCHEMA"] in {"Clip.2", "Gap.1", "Transition.1"}
            if item["OTIO_SCHEMA"] == "Clip.2":
                reference = item["media_references"][item["active_media_reference_key"]]
                assert reference["OTIO_SCHEMA"] == "ExternalReference.1"
                assert reference["target_url"].startswith("file:///")

    v1 = tracks[0]["children"]
    assert [i["OTIO_SCHEMA"] for i in v1] == ["Clip.2", "Transition.1", "Clip.2"]
    assert _seconds(v1[0]["source_range"]["start_time"]) == 2  # source frame 60 at 30 fps
    assert _seconds(v1[0]["source_range"]["duration"]) == Fraction(45, 30)
    assert _seconds(v1[1]["in_offset"]) + _seconds(v1[1]["out_offset"]) == Fraction(10, 30)
    assert v1[0]["metadata"]["ageneditor"]["rationale"] == "opening line"
    v2 = tracks[1]["children"]
    assert [i["OTIO_SCHEMA"] for i in v2] == ["Gap.1", "Clip.2"]
    assert _seconds(v2[0]["source_range"]["duration"]) == 1  # B-roll starts at frame 30


@pytest.mark.media
def test_export_opens_with_reference_opentimelineio(project: dict) -> None:
    """Check the actual interchange parser, not just our own JSON reader."""
    otio = pytest.importorskip("opentimelineio")
    document = export_otio(project["store"], project["main"], "Launch")
    parsed = otio.adapters.read_from_string(document, "otio_json")
    assert isinstance(parsed, otio.schema.Timeline)
    assert len(parsed.tracks) == 7
    assert parsed.tracks[0][0].name == "a"
    assert parsed.tracks[0].duration().to_seconds() == 3
    assert isinstance(parsed.tracks[0][1], otio.schema.Transition)
    assert parsed.tracks[0][0].media_reference.target_url.startswith("file:///")
    rewritten = otio.adapters.write_to_string(parsed, "otio_json")
    candidate, issues = import_otio(project["store"], rewritten, project["main"])
    assert candidate is not None, issues
    assert not any(issue.severity == "error" for issue in issues)


@pytest.mark.media
def test_import_accepts_timeline_authored_by_reference_library(project: dict) -> None:
    otio = pytest.importorskip("opentimelineio")
    store = project["store"]
    primary = project["primary"]
    source = store.safe_path(primary.relative_path).resolve()
    source_range = otio.opentime.TimeRange(
        otio.opentime.RationalTime(90, 30), otio.opentime.RationalTime(60, 30)
    )
    clip = otio.schema.Clip(
        name="Reference library clip",
        media_reference=otio.schema.ExternalReference(target_url=source.as_uri()),
        source_range=source_range,
    )
    track = otio.schema.Track(name="Video", kind=otio.schema.TrackKind.Video)
    track.append(clip)
    timeline = otio.schema.Timeline(name="Reference library timeline", tracks=[track])
    candidate, issues = import_otio(
        store, otio.adapters.write_to_string(timeline, "otio_json"), project["main"]
    )
    assert candidate is not None, issues
    assert len(candidate.clips) == 1
    imported = candidate.clips[0]
    assert (imported.asset_id, imported.source_in_frames, imported.source_out_frames) == (primary.id, 90, 150)


@pytest.mark.media
def test_an_exported_timeline_imports_back_to_the_same_cut(project: dict, tmp_path: Path) -> None:
    store: ProjectStore = project["store"]
    main = project["main"]
    candidate, issues = import_otio(store, export_otio(store, main), main)
    assert [i for i in issues if i.severity == "error"] == []
    assert candidate is not None

    def placed(timeline):
        return sorted(
            (c.clip_id, c.asset_id, c.track.value, c.source_in_frames, c.source_out_frames,
             c.timeline_in_frames, c.timeline_span_frames, c.gain_db)
            for c in timeline.clips
        )  # fmt: skip

    assert placed(candidate) == placed(main)
    assert candidate.transitions == main.transitions

    service = DesktopService(tmp_path)
    try:
        text = service._desktop_call("export_otio", {"project_id": store.project_id})["text"]
        result = service._desktop_call(
            "import_otio",
            {"project_id": store.project_id, "text": text, "expected_revision": main.revision},
        )
    finally:
        service.http.server_close()
    assert result["committed_revision"] == main.revision + 1
    assert store.timeline().provenance.tool == "import_otio"


@pytest.mark.media
def test_a_foreign_otio_matches_registered_media_by_path_and_refuses_unknown_media(
    project: dict, tmp_path: Path, media_dir: Path
) -> None:
    store: ProjectStore = project["store"]
    primary = project["primary"]

    def foreign(url: str) -> str:
        rt = lambda v: {"OTIO_SCHEMA": "RationalTime.1", "rate": 30.0, "value": v}  # noqa: E731
        clip = {
            "OTIO_SCHEMA": "Clip.2", "name": "Interview 1", "metadata": {}, "effects": [], "markers": [],
            "enabled": True,
            "source_range": {"OTIO_SCHEMA": "TimeRange.1", "start_time": rt(90.0), "duration": rt(60.0)},
            "media_references": {"DEFAULT_MEDIA": {"OTIO_SCHEMA": "ExternalReference.1", "target_url": url,
                                                   "metadata": {}}},
            "active_media_reference_key": "DEFAULT_MEDIA",
        }  # fmt: skip
        track = {
            "OTIO_SCHEMA": "Track.1",
            "name": "Video 1",
            "kind": "Video",
            "children": [clip],
            "metadata": {},
        }
        return json.dumps(
            {"OTIO_SCHEMA": "Timeline.1", "name": "From another editor",
             "tracks": {"OTIO_SCHEMA": "Stack.1", "children": [track]}}
        )  # fmt: skip

    path = store.safe_path(primary.relative_path).resolve()
    candidate, issues = import_otio(store, foreign(path.as_uri()), store.timeline())
    assert candidate is not None, issues
    [clip] = candidate.clips
    assert (clip.asset_id, clip.source_in_frames, clip.source_out_frames) == (primary.id, 90, 150)
    assert clip.clip_id == "interview_1"

    elsewhere = tmp_path / "other" / "talking_head_16x9_30fps_60s.mp4"
    elsewhere.parent.mkdir()
    shutil.copyfile(media_dir / "talking_head_16x9_30fps_60s.mp4", elsewhere)
    by_name, _ = import_otio(store, foreign(elsewhere.as_uri()), store.timeline())
    assert by_name is not None and by_name.clips[0].asset_id == primary.id  # unique file name match

    unknown, issues = import_otio(store, foreign((tmp_path / "missing.mov").as_uri()), store.timeline())
    assert unknown is None
    assert any("not registered" in issue.message for issue in issues)


@pytest.mark.media
def test_the_agent_can_write_an_otio_file_into_the_project(project: dict, tmp_path: Path) -> None:
    store: ProjectStore = project["store"]

    async def run() -> None:
        async with Client(build_server(tmp_path), raise_exceptions=True) as client:
            result = await client.call_tool("export_otio", {"project_id": store.project_id})
            assert result.structured_content["path"] == f"exports/r{project['main'].revision}.otio"

    asyncio.run(run())
    written = json.loads(
        store.safe_path(f"exports/r{project['main'].revision}.otio").read_text(encoding="utf-8")
    )
    assert written["OTIO_SCHEMA"] == "Timeline.1"
