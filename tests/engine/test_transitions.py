"""Built-in xfade transitions between V1 clips: catalog, validation, active styles, rendering."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from ageneditor.contracts import (
    TRANSITION_PRESETS,
    AssetRole,
    AudioMix,
    Clip,
    EngineException,
    ErrorCode,
    RemoveClip,
    RenderProfile,
    RevisionPatch,
    TrackId,
    Transition,
    TransitionStyle,
)
from ageneditor.media.probe import probe
from ageneditor.media.render import render_timeline
from ageneditor.media.tools import find_tool
from ageneditor.revisions import apply_patch
from ageneditor.storage import ProjectStore
from ageneditor.timebase import FrameRate
from ageneditor.transitions import TransitionLibrary

PROFILE = RenderProfile(id="tr", width=180, height=320, frame_rate=FrameRate(num=30))


def _frame(path: Path, number: int) -> bytes:
    return subprocess.run(
        [
            str(find_tool("ffmpeg")), "-v", "error", "-i", str(path), "-vf", f"select=eq(n\\,{number})",
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-",
        ],
        capture_output=True,
        check=True,
        timeout=60,
    ).stdout  # fmt: skip


def _difference(a: bytes, b: bytes) -> float:
    assert len(a) == len(b) and a
    return sum(abs(x - y) for x, y in zip(a, b, strict=True)) / len(a)


def _v1(clip_id: str, asset_id: str, source_in: int, length: int, at: int) -> Clip:
    return Clip(
        clip_id=clip_id, asset_id=asset_id, track=TrackId.V1,
        source_in_frames=source_in, source_out_frames=source_in + length, timeline_in_frames=at,
    )  # fmt: skip


@pytest.fixture
def project(tmp_path: Path, media_dir: Path) -> tuple[ProjectStore, str]:
    (tmp_path / "ws").mkdir()
    store = ProjectStore.create(tmp_path / "ws", "transitions")
    asset = store.import_asset(media_dir / "talking_head_16x9_30fps_60s.mp4", AssetRole.PRIMARY)
    store.create_timeline(PROFILE, 120)
    return store, asset.id


def test_catalog_covers_every_bundled_xfade_transition_once() -> None:
    names = [info.xfade for info in TRANSITION_PRESETS.values()]
    assert len(names) == len(set(names)) == 58  # FFmpeg numbers them 0-57
    help_text = subprocess.run(
        [str(find_tool("ffmpeg")), "-hide_banner", "-h", "filter=xfade"],
        capture_output=True, text=True, check=True, timeout=60,
    ).stdout  # fmt: skip
    for name in names:
        assert f" {name} " in help_text, name
    assert {info.style for info in TRANSITION_PRESETS.values()} == set(TransitionStyle)
    assert all(2 <= info.default_frames <= 60 for info in TRANSITION_PRESETS.values())


def test_transition_placement_rules(project: tuple[ProjectStore, str]) -> None:
    store, asset = project
    seed = store.timeline()
    clips = [_v1("a", asset, 0, 60, 0), _v1("b", asset, 300, 20, 60), _v1("c", asset, 600, 40, 80)]

    def build(*transitions: dict) -> None:
        seed.model_validate({**seed.model_dump(mode="json"), "clips": [c.model_dump() for c in clips],
                             "transitions": list(transitions)})  # fmt: skip

    build({"after_clip_id": "a", "preset": "cross_dissolve", "duration_frames": 12})
    with pytest.raises(ValidationError, match="no cut after the last"):
        build({"after_clip_id": "c", "preset": "cross_dissolve", "duration_frames": 12})
    with pytest.raises(ValidationError, match="needs a V1 clip"):
        build({"after_clip_id": "ghost", "preset": "cross_dissolve", "duration_frames": 12})
    with pytest.raises(ValidationError, match="one transition per cut"):
        build(
            *[{"after_clip_id": "a", "preset": p, "duration_frames": 10} for p in ("zoom_in", "slide_left")]
        )
    with pytest.raises(ValidationError):
        build({"after_clip_id": "a", "preset": "page_curl", "duration_frames": 12})  # not in the catalog
    with pytest.raises(ValidationError):
        build({"after_clip_id": "a", "preset": "cross_dissolve", "duration_frames": 61})
    # b is 20 frames: 12 in from a's transition (6) + 30 out to c (15) = 21 > 20.
    with pytest.raises(ValidationError, match="longer than the clip"):
        build(
            {"after_clip_id": "a", "preset": "cross_dissolve", "duration_frames": 12},
            {"after_clip_id": "b", "preset": "slow_fade", "duration_frames": 30},
        )
    build(
        {"after_clip_id": "a", "preset": "cross_dissolve", "duration_frames": 12},
        {"after_clip_id": "b", "preset": "slow_fade", "duration_frames": 28},
    )


def test_only_active_styles_can_be_added(tmp_path: Path, project: tuple[ProjectStore, str]) -> None:
    store, asset = project
    library = TransitionLibrary(tmp_path / "ws")
    assert [s["active"] for s in library.styles()] == [True] * 4
    library.set_active(TransitionStyle.MONTAGE, False)
    catalog = library.catalog()
    assert "montage" not in catalog["active_styles"]
    assert all(item["style"] != "montage" for item in catalog["transitions"])
    assert any(item["id"] == "cross_dissolve" for item in catalog["transitions"])

    seed = store.timeline()
    clips = [_v1("a", asset, 0, 60, 0), _v1("b", asset, 300, 60, 60)]

    def with_transition(preset: str):
        return seed.model_copy(
            update={
                "clips": clips,
                "transitions": [Transition(after_clip_id="a", preset=preset, duration_frames=10)],
            }
        )

    library.check_new_transitions(seed, with_transition("cross_dissolve"))
    with pytest.raises(EngineException) as blocked:
        library.check_new_transitions(seed, with_transition("zoom_in"))
    assert blocked.value.error.code is ErrorCode.PERMISSION_DENIED
    assert "montage" in blocked.value.error.message
    # A transition already in the edit may stay after its style is switched off.
    library.check_new_transitions(with_transition("zoom_in"), with_transition("zoom_in"))
    # Settings persist for the whole workspace.
    assert TransitionLibrary(tmp_path / "ws").load().active_styles == [
        TransitionStyle.CLASSIC, TransitionStyle.MODERN, TransitionStyle.CINEMATIC,
    ]  # fmt: skip


def test_removing_the_last_clip_drops_its_dangling_transition(project: tuple[ProjectStore, str]) -> None:
    store, asset = project
    seed = store.timeline()
    clips = [_v1("a", asset, 0, 60, 0), _v1("b", asset, 300, 60, 60)]
    committed = store.commit_timeline(
        seed.model_copy(
            update={
                "clips": clips,
                "transitions": [Transition(after_clip_id="a", preset="iris_open", duration_frames=10)],
            }
        ),
        seed.revision,
    )
    result = apply_patch(
        store,
        RevisionPatch(project_id=store.project_id, summary="drop the ending", ops=[RemoveClip(clip_id="b")]),
        committed.revision,
    )
    assert result.timeline.transitions == []


@pytest.mark.media
def test_transitions_blend_across_the_cut_without_changing_length(project: tuple[ProjectStore, str]) -> None:
    store, asset = project
    seed = store.timeline()
    # b starts at source frame 0, so it has no handle before it: its first frame is held.
    clips = [_v1("a", asset, 900, 60, 0), _v1("b", asset, 0, 60, 60), _v1("c", asset, 1500, 60, 120)]
    base = seed.model_copy(
        update={"clips": clips, "target_duration_frames": 180, "audio": AudioMix(normalize=False)}
    )
    hard = store.commit_timeline(base, seed.revision)
    soft = store.commit_timeline(
        hard.model_copy(
            update={
                "transitions": [
                    Transition(after_clip_id="a", preset="cross_dissolve", duration_frames=12),
                    Transition(after_clip_id="b", preset="slide_left", duration_frames=10),
                ]
            }
        ),
        hard.revision,
    )
    hard_out, soft_out = store.safe_path("renders/hard.mp4"), store.safe_path("renders/soft.mp4")
    render_timeline(store, hard, hard_out)
    render_timeline(store, soft, soft_out)

    for path in (hard_out, soft_out):
        measured = probe(path)
        assert measured.video and measured.video.duration_frames == 180
        assert measured.audio is not None
    # Inside the dissolve window [54, 66) the picture is a blend, not either hard-cut frame.
    assert _difference(_frame(soft_out, 59), _frame(hard_out, 59)) > 2
    assert _difference(_frame(soft_out, 61), _frame(hard_out, 61)) > 2
    # Away from the transitions, the frames match the hard-cut render.
    for frame in (20, 90, 160):
        assert _difference(_frame(soft_out, frame), _frame(hard_out, frame)) < 2, frame


def test_catalog_names_inactive_styles_without_their_presets(tmp_path: Path) -> None:
    library = TransitionLibrary(tmp_path)
    library.set_active(TransitionStyle.MONTAGE, False)
    catalog = library.catalog()
    assert [item["label"] for item in catalog["inactive_styles"]] == ["Montage"]
    assert "zooms" in catalog["inactive_styles"][0]["contains"]
    assert "zoom_in" not in {item["id"] for item in catalog["transitions"]}
