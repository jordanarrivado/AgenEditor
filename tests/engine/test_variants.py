"""Variants (roadmap Phase 2c): overrides on the shared main edit, computed per main revision."""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path

import pytest
from mcp import Client

from ageneditor.contracts import (
    AssetChecklist,
    AssetRequest,
    AssetRole,
    CaptionCue,
    CheckStatus,
    Clip,
    EngineException,
    ErrorCode,
    JobStatus,
    RenderProfile,
    TrackId,
    VariantKind,
    VariantOverrides,
)
from ageneditor.jobs import JobManager
from ageneditor.mcp_server import build_server
from ageneditor.media.qa import run_qa
from ageneditor.storage import ProjectStore
from ageneditor.timebase import FrameRate
from ageneditor.variants import PinnedTimelineStore, VariantService

PROFILE = RenderProfile(id="variants", width=180, height=320, frame_rate=FrameRate(num=30))


def _clip(clip_id: str, asset_id: str, track: TrackId, src: int, length: int, at: int, **extra) -> Clip:
    return Clip(
        clip_id=clip_id,
        asset_id=asset_id,
        track=track,
        source_in_frames=src,
        source_out_frames=src + length,
        timeline_in_frames=at,
        **extra,
    )


@pytest.fixture
def project(tmp_path: Path, media_dir: Path) -> dict:
    """Main edit r1: three 1-second V1 clips, B-roll in the opening and in the body, a music bed and two
    manual captions (one in the opening, one in the body)."""
    store = ProjectStore.create(tmp_path, "variants")
    primary = store.import_asset(media_dir / "talking_head_16x9_30fps_60s.mp4", AssetRole.PRIMARY)
    broll = store.import_asset(media_dir / "broll_9x16_10s.mp4", AssetRole.BROLL)
    copy = tmp_path / "broll_localized.mp4"
    shutil.copyfile(media_dir / "broll_9x16_10s.mp4", copy)
    localized = store.import_asset(copy, AssetRole.BROLL)
    music = store.import_asset(media_dir / "music_bed_30s.m4a", AssetRole.MUSIC)
    seed = store.create_timeline(PROFILE, 90)
    clips = [
        _clip("c1", primary.id, TrackId.V1, 0, 30, 0),
        _clip("c2", primary.id, TrackId.V1, 120, 30, 30),
        _clip("c3", primary.id, TrackId.V1, 240, 30, 60),
        _clip("b_open", broll.id, TrackId.V2, 0, 10, 10),
        _clip("b_body", broll.id, TrackId.V2, 30, 20, 50),
        _clip("bed", music.id, TrackId.A2, 0, 90, 0, gain_db=-12),
    ]
    captions = [
        CaptionCue(cue_id="cue_open", text="Hello", timeline_in_frames=5, timeline_out_frames=15,
                   source_asset_id=primary.id, corrected=True),
        CaptionCue(cue_id="cue_body", text="Body", timeline_in_frames=40, timeline_out_frames=55,
                   source_asset_id=primary.id, corrected=True),
    ]  # fmt: skip
    main = store.commit_timeline(seed.model_copy(update={"clips": clips, "captions": captions}), 0)
    return {"store": store, "main": main, "primary": primary, "broll": broll, "localized": localized}


def _hook(project: dict, length: int = 45) -> VariantOverrides:
    return VariantOverrides(
        hook_replaces_clips=1,
        hook_clips=[_clip("hook_a", project["primary"].id, TrackId.V1, 600, length, 0)],
    )


def test_a_new_variant_is_the_main_edit_and_a_hook_reshapes_only_the_opening(project: dict) -> None:
    store: ProjectStore = project["store"]
    service = VariantService(store)
    variant = service.create("Hook B", VariantKind.HOOK)
    plain = service.view(variant.id)
    assert plain.renderable and plain.timeline is not None
    assert plain.timeline.clips == project["main"].clips
    assert plain.timeline.duration_frames == 90

    view = service.update(variant.id, _hook(project), variant.revision, "longer hook")
    assert view.variant.revision == 2
    timeline = view.timeline
    placed = {c.clip_id: (c.timeline_in_frames, c.timeline_out_frames) for c in timeline.clips}
    # The 45-frame hook replaces c1 (30 frames): the body moves 15 frames later.
    assert placed["hook_a"] == (0, 45)
    assert placed["c2"] == (45, 75) and placed["c3"] == (75, 105)
    assert placed["b_body"] == (65, 85)
    assert "c1" not in placed and "b_open" not in placed
    assert placed["bed"] == (0, 90)  # the music bed keeps playing from the start
    assert view.dropped_clip_ids == ["b_open"]
    assert timeline.duration_frames == 105 and timeline.target_duration_frames == 105
    # Captions: the opening cue goes with the old hook, the body cue moves with the body.
    assert [(c.cue_id, c.timeline_in_frames) for c in timeline.captions] == [("cue_body", 55)]
    assert view.dropped_caption_count == 1
    assert any("hook has no captions" in issue.message for issue in view.issues)
    # The main edit itself is untouched.
    assert store.timeline() == project["main"]


def test_body_edits_reach_every_variant_without_a_new_variant_revision(project: dict) -> None:
    store: ProjectStore = project["store"]
    service = VariantService(store)
    variant = service.create("Hook B", VariantKind.HOOK)
    service.update(variant.id, _hook(project), variant.revision)
    main = project["main"]
    louder = [c.model_copy(update={"gain_db": 3.0}) if c.clip_id == "c3" else c for c in main.clips]
    main2 = store.commit_timeline(main.model_copy(update={"clips": louder}), main.revision)

    view = service.view(variant.id)
    assert view.main_revision == main2.revision and view.variant.revision == 2
    assert next(c for c in view.timeline.clips if c.clip_id == "c3").gain_db == 3.0
    # The earlier pairing can still be computed exactly.
    old = service.view(variant.id, main_revision=main.revision)
    assert next(c for c in old.timeline.clips if c.clip_id == "c3").gain_db == 0.0


def test_overrides_that_do_not_fit_the_main_edit_are_refused(project: dict) -> None:
    store: ProjectStore = project["store"]
    service = VariantService(store)
    variant = service.create("Bad", VariantKind.OTHER)
    too_many = _hook(project).model_copy(update={"hook_replaces_clips": 5})
    with pytest.raises(EngineException, match="only 3"):
        service.update(variant.id, too_many, variant.revision)
    clash = VariantOverrides(
        hook_replaces_clips=1, hook_clips=[_clip("c2", project["primary"].id, TrackId.V1, 600, 30, 0)]
    )
    with pytest.raises(EngineException, match="already used"):
        service.update(variant.id, clash, variant.revision)
    with pytest.raises(EngineException) as stale:
        service.update(variant.id, _hook(project), variant.revision + 3)
    assert stale.value.error.code is ErrorCode.REVISION_CONFLICT
    assert service.get(variant.id).revision == 1  # nothing was saved
    with pytest.raises(ValueError):
        VariantOverrides(hook_replaces_clips=1)  # replacing clips needs a hook


def test_asset_swaps_and_captions_off(project: dict) -> None:
    store: ProjectStore = project["store"]
    service = VariantService(store)
    variant = service.create("Spanish", VariantKind.LANGUAGE, language="es")
    swaps = VariantOverrides(asset_swaps={project["broll"].id: project["localized"].id}, captions="off")
    view = service.update(variant.id, swaps, variant.revision)
    assert {c.clip_id: c.asset_id for c in view.timeline.clips if c.track is TrackId.V2} == {
        "b_open": project["localized"].id,
        "b_body": project["localized"].id,
    }
    assert view.timeline.captions == []
    unused = VariantOverrides(asset_swaps={project["primary"].id: project["localized"].id})
    warned = service.update(variant.id, unused, view.variant.revision)
    assert any("matches no overlay" in issue.message for issue in warned.issues)


def test_variant_requests_gate_only_their_variant(project: dict) -> None:
    store: ProjectStore = project["store"]
    variant = VariantService(store).create("Localized", VariantKind.LOCALIZED)
    with pytest.raises(EngineException, match="unknown variant"):
        store.save_asset_checklist(
            AssetChecklist(
                project_id=store.project_id,
                configured=True,
                requests=[AssetRequest(id="shot", kind="video", prompt="x", variant_id="variant_nope")],
            ),
            0,
        )
    store.save_asset_checklist(
        AssetChecklist(
            project_id=store.project_id,
            configured=True,
            requests=[AssetRequest(id="shot_es", kind="video", prompt="Market", variant_id=variant.id)],
        ),
        0,
    )
    store.require_asset_requests_ready()  # the main edit is not blocked
    main = project["main"]
    store.commit_timeline(main.model_copy(update={"target_duration_frames": 91}), main.revision)
    assert store.pending_asset_requests(variant.id) == ["shot_es"]
    assert VariantService(store).view(variant.id).renderable is False
    with pytest.raises(EngineException, match="pending"):
        JobManager(store).start_render(store.current_revision(), final=False, variant_id=variant.id)


def test_variant_tools_through_mcp(project: dict, tmp_path: Path) -> None:
    store: ProjectStore = project["store"]

    async def run() -> None:
        async with Client(build_server(tmp_path), raise_exceptions=True) as client:
            created = await client.call_tool(
                "create_variant", {"project_id": store.project_id, "name": "Hook B", "kind": "hook"}
            )
            variant_id = created.structured_content["id"]
            updated = await client.call_tool(
                "update_variant",
                {
                    "project_id": store.project_id,
                    "variant_id": variant_id,
                    "overrides": _hook(project).model_dump(mode="json"),
                    "expected_revision": 1,
                    "summary": "longer hook",
                },
            )
            assert updated.structured_content["variant"]["revision"] == 2
            listed = await client.call_tool("list_variants", {"project_id": store.project_id})
            [item] = listed.structured_content["variants"]
            assert item["renderable"] is True and item["variant"]["id"] == variant_id

    asyncio.run(run())


@pytest.mark.media
def test_a_variant_renders_from_the_shared_body_and_passes_technical_qa(project: dict) -> None:
    store: ProjectStore = project["store"]
    service = VariantService(store)
    variant = service.create("Hook B", VariantKind.HOOK)
    view = service.update(variant.id, _hook(project), variant.revision)
    manager = JobManager(store)
    job = manager.start_render(store.current_revision(), final=True, variant_id=variant.id)
    assert (job.variant_id, job.variant_revision, job.timeline_revision) == (variant.id, 2, 1)
    deadline = time.monotonic() + 300
    while manager.status(job.id).status in {JobStatus.QUEUED, JobStatus.RUNNING}:
        assert time.monotonic() < deadline
        time.sleep(0.2)
    done = manager.status(job.id)
    assert done.status is JobStatus.SUCCEEDED, done.error
    pinned = PinnedTimelineStore(store, view.timeline)
    report = run_qa(pinned, job.id, job.timeline_revision, store.safe_path(done.output_path))
    checks = {check.check_id: check.status for check in report.checks}
    assert checks["duration"] is CheckStatus.PASS  # 105 frames: the variant, not the 90-frame main edit
    assert checks["decode"] is CheckStatus.PASS
