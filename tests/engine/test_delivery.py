"""Delivery of a project's finals (roadmap Phase 2c): client file names and a known-issues note."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from ageneditor.contracts import (
    AssetChecklist,
    AssetOrigin,
    AssetProvenance,
    AssetRequest,
    AssetRole,
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
from ageneditor.delivery import aspect_label, delivery_plan, name_part
from ageneditor.desktop_service import DesktopService
from ageneditor.jobs import JobManager
from ageneditor.media.preview_qa import run_preview_qa
from ageneditor.media.qa import run_qa
from ageneditor.storage import ProjectStore
from ageneditor.timebase import FrameRate
from ageneditor.variants import PinnedTimelineStore, VariantService

PROFILE = RenderProfile(id="delivery", width=180, height=320, frame_rate=FrameRate(num=30))


def _clip(clip_id: str, asset_id: str, track: TrackId, src: int, length: int, at: int) -> Clip:
    return Clip(
        clip_id=clip_id,
        asset_id=asset_id,
        track=track,
        source_in_frames=src,
        source_out_frames=src + length,
        timeline_in_frames=at,
    )


def _render(store: ProjectStore, final: bool, variant_id: str | None = None) -> str:
    manager = JobManager(store)
    job = manager.start_render(store.current_revision(), final=final, variant_id=variant_id)
    deadline = time.monotonic() + 300
    while manager.status(job.id).status in {JobStatus.QUEUED, JobStatus.RUNNING}:
        assert time.monotonic() < deadline
        time.sleep(0.2)
    assert manager.status(job.id).status is JobStatus.SUCCEEDED, manager.status(job.id).error
    return job.id


def test_name_parts_are_plain_ascii_and_ratios_reduce() -> None:
    assert name_part("Acme Co. / Spring!", "x") == "AcmeCoSpring"
    assert name_part("../..", "Project") == "Project"
    assert name_part("hook b", "x") == "HookB"
    assert aspect_label(1080, 1920) == "9x16"
    assert aspect_label(1920, 1080) == "16x9"
    assert aspect_label(1080, 1080) == "1x1"
    assert aspect_label(1081, 1920) == "1081x1920"


@pytest.mark.media
def test_finals_get_client_names_and_a_note_built_from_the_project_records(
    tmp_path: Path, media_dir: Path
) -> None:
    store = ProjectStore.create(tmp_path, "Spring launch")
    primary = store.import_asset(media_dir / "talking_head_16x9_30fps_60s.mp4", AssetRole.PRIMARY)
    copy = tmp_path / "city_shot.mp4"
    shutil.copyfile(media_dir / "broll_9x16_10s.mp4", copy)
    city = store.import_asset(
        copy, AssetRole.BROLL, AssetProvenance(origin=AssetOrigin.GENERATED_EXTERNAL, tool_name="Google Flow")
    )
    checklist = store.save_asset_checklist(
        AssetChecklist(
            project_id=store.project_id,
            configured=True,
            requests=[AssetRequest(id="city", kind="video", prompt="a city street at dusk")],
        ),
        0,
    )
    linked = store.link_asset_request("city", city.id, checklist.version)
    store.accept_asset_issue("city", "the shop sign text is garbled", linked.version)

    seed = store.create_timeline(PROFILE, 60)
    clips = [
        _clip("c1", primary.id, TrackId.V1, 0, 30, 0),
        _clip("c2", primary.id, TrackId.V1, 120, 30, 30),
        _clip("city", city.id, TrackId.V2, 0, 15, 35),
    ]
    store.commit_timeline(seed.model_copy(update={"clips": clips}), 0)
    variants = VariantService(store)
    variant = variants.create("Hook B", VariantKind.HOOK)
    hook = VariantOverrides(
        hook_replaces_clips=1, hook_clips=[_clip("hook", primary.id, TrackId.V1, 600, 45, 0)]
    )
    view = variants.update(variant.id, hook, variant.revision)

    preview = _render(store, final=False)
    run_preview_qa(store, preview)
    main = _render(store, final=True)
    run_qa(store, main, 1, store.safe_path(store.get_job(main).output_path))
    hooked = _render(store, final=True, variant_id=variant.id)
    pinned = PinnedTimelineStore(store, view.timeline)
    run_qa(pinned, hooked, 1, store.safe_path(store.get_job(hooked).output_path))
    unchecked = _render(store, final=True)

    plan = delivery_plan(store, [main, hooked, preview, unchecked, main], client="Acme Co.")
    assert [(f.name, f.label) for f in plan.files] == [
        ("AcmeCo_SpringLaunch_Main_9x16_2s_r1", "Main edit"),
        ("AcmeCo_SpringLaunch_HookB_9x16_3s_r1v2", "Hook B"),  # 75 frames = 2.5 s, rounded half up
    ]
    assert plan.folder.startswith("AcmeCo_SpringLaunch_delivery_")
    assert {(item.job_id, item.reason) for item in plan.excluded} == {
        (preview, "only final renders are delivered"),
        (unchecked, "run the technical check first"),
    }
    both = [f.name for f in plan.files]
    accepted = [issue for issue in plan.issues if issue.source == "accepted"]
    assert [(issue.message, issue.files) for issue in accepted] == [
        ("city_shot.mp4: the shop sign text is garbled", both)
    ]
    assert [(g.original_filename, g.tool_name, g.files) for g in plan.generated] == [
        ("city_shot.mp4", "Google Flow", both)
    ]
    # Only checks that did not pass are listed, each from the report of the file it concerns.
    for issue in plan.issues:
        if issue.source in {"technical_qa", "editorial_qa"}:
            assert " (pass)" not in issue.message
    note = plan.note
    assert note.startswith("# Delivery notes: Spring launch\n\nFor: Acme Co.\n")
    assert "it is not a full review" in note
    hook_line = "- AcmeCo_SpringLaunch_HookB_9x16_3s_r1v2.mp4: Hook B, variant revision 2 on main revision 1"
    assert hook_line in note
    assert "### Accepted by the editor\n\n- city_shot.mp4: the shop sign text is garbled (all files)" in note
    assert "## AI-generated (external) material\n\n- city_shot.mp4: made with Google Flow" in note
    assert "## Not included" not in note and unchecked not in note  # reported in the app instead

    # The client name is remembered for the next delivery; an empty name removes it.
    again = delivery_plan(store, [main])
    assert again.files[0].name == "AcmeCo_SpringLaunch_Main_9x16_2s_r1"
    assert "No known issues" not in again.note
    plain = delivery_plan(store, [main], client=" ")
    assert plain.files[0].name == "SpringLaunch_Main_9x16_2s_r1"
    assert "For:" not in plain.note

    # Through the desktop operation, as the native shell calls it.
    service = DesktopService(tmp_path)
    try:
        result = service._desktop_call(
            "delivery_plan", {"project_id": store.project_id, "job_ids": [hooked], "client": "Acme"}
        )
        settings = service._desktop_call("project_settings", {"project_id": store.project_id})
    finally:
        service.http.server_close()
    assert [f["name"] for f in result["files"]] == ["Acme_SpringLaunch_HookB_9x16_3s_r1v2"]
    assert settings["client_name"] == "Acme"


@pytest.mark.media
def test_nothing_deliverable_is_an_error_and_a_clean_render_says_so(tmp_path: Path, media_dir: Path) -> None:
    store = ProjectStore.create(tmp_path, "clean")
    primary = store.import_asset(media_dir / "talking_head_16x9_30fps_60s.mp4", AssetRole.PRIMARY)
    seed = store.create_timeline(PROFILE, 30)
    store.commit_timeline(
        seed.model_copy(update={"clips": [_clip("c1", primary.id, TrackId.V1, 0, 30, 0)]}), 0
    )
    final = _render(store, final=True)
    with pytest.raises(EngineException) as nothing:
        delivery_plan(store, [final])
    assert nothing.value.error.code is ErrorCode.QA_FAILED
    assert "run the technical check first" in nothing.value.error.message
    with pytest.raises(EngineException) as empty:
        delivery_plan(store, [])
    assert empty.value.error.code is ErrorCode.INVALID_ARGUMENT

    report = run_qa(store, final, 1, store.safe_path(store.get_job(final).output_path))
    plan = delivery_plan(store, [final])
    assert plan.files[0].name == "Clean_Main_9x16_1s_r1"
    warned = {c.check_id for c in report.checks if c.status is not CheckStatus.PASS}
    assert {issue.message.split(" (")[0] for issue in plan.issues} == warned
    assert ("No known issues were recorded for these files." in plan.note) == (not warned)
    assert "## AI-generated" not in plan.note
