"""B-roll review: a linked file counts as ready only after the person approves it."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ageneditor.contracts import (
    AssetChecklist,
    AssetOrigin,
    AssetProvenance,
    AssetRequest,
    AssetRole,
    Clip,
    EngineException,
    ErrorCode,
    RenderProfile,
    TrackId,
)
from ageneditor.desktop_service import DesktopService
from ageneditor.storage import ProjectStore
from ageneditor.timebase import FrameRate

EXTERNAL = AssetProvenance(origin=AssetOrigin.GENERATED_EXTERNAL, tool_name="ChatGPT")


def _cut(store: ProjectStore, media_dir: Path):
    """A valid one-clip timeline on the project's main footage, ready to commit."""
    primary = store.import_asset(media_dir / "talking_head_16x9_30fps_60s.mp4", AssetRole.PRIMARY)
    seed = store.timeline()
    clip = Clip(
        clip_id="c1",
        asset_id=primary.id,
        track=TrackId.V1,
        source_in_frames=0,
        source_out_frames=30,
        timeline_in_frames=0,
    )
    return seed.model_copy(update={"clips": [clip]})


def _requests() -> list[AssetRequest]:
    return [
        AssetRequest(id="broll_01_storyboard", kind="image", purpose="storyboard", prompt="A grid"),
        AssetRequest(id="broll_01", kind="video", purpose="street shot", prompt="Camera pushes in"),
    ]


@pytest.fixture
def project(tmp_path: Path, media_dir: Path) -> tuple[ProjectStore, str, str]:
    store = ProjectStore.create(tmp_path, "review")
    store.create_timeline(RenderProfile(id="r", width=180, height=320, frame_rate=FrameRate(num=30)), 30)
    storyboard = store.import_asset(media_dir / "still_storyboard_9x16.png", AssetRole.REFERENCE, EXTERNAL)
    video = store.import_asset(media_dir / "broll_9x16_10s.mp4", AssetRole.BROLL, EXTERNAL)
    store.save_asset_checklist(
        AssetChecklist(
            project_id=store.project_id, configured=True, review_required=True, requests=_requests()
        ),
        0,
    )
    return store, storyboard.id, video.id


def _version(store: ProjectStore) -> int:
    return store.asset_checklist().version


def test_a_linked_file_is_not_ready_until_approved(
    project: tuple[ProjectStore, str, str], media_dir: Path
) -> None:
    store, storyboard, _ = project
    seed = _cut(store, media_dir)
    store.link_asset_request("broll_01_storyboard", storyboard, _version(store))
    with pytest.raises(EngineException) as pending:
        store.commit_timeline(seed, 0)
    assert "pending" in pending.value.error.message  # the video request still has no file
    store.save_asset_checklist(
        store.asset_checklist().model_copy(
            update={"links": {**store.asset_checklist().links, "broll_01": "dropped"}}
        ),
        _version(store),
    )
    with pytest.raises(EngineException) as waiting:
        store.commit_timeline(seed, 0)
    assert "approve" in waiting.value.error.message
    store.approve_asset_request("broll_01_storyboard", _version(store))
    assert store.commit_timeline(seed, 0).revision == 1


def test_video_waits_for_its_storyboard_to_be_approved(project: tuple[ProjectStore, str, str]) -> None:
    store, storyboard, video = project
    with pytest.raises(EngineException) as early:
        store.link_asset_request("broll_01", video, _version(store))
    assert "storyboard" in early.value.error.message
    store.link_asset_request("broll_01_storyboard", storyboard, _version(store))
    with pytest.raises(EngineException):  # linked but not yet approved
        store.link_asset_request("broll_01", video, _version(store))
    store.approve_asset_request("broll_01_storyboard", _version(store))
    linked = store.link_asset_request("broll_01", video, _version(store))
    assert linked.links["broll_01"] == video


def test_skipping_the_storyboard_unlocks_the_video(project: tuple[ProjectStore, str, str]) -> None:
    store, _, video = project
    store.save_asset_checklist(
        store.asset_checklist().model_copy(update={"links": {"broll_01_storyboard": "dropped"}}),
        _version(store),
    )
    assert store.link_asset_request("broll_01", video, _version(store)).links["broll_01"] == video


def test_reset_clears_link_and_approval_but_not_below_a_made_video(
    project: tuple[ProjectStore, str, str],
) -> None:
    store, storyboard, video = project
    store.link_asset_request("broll_01_storyboard", storyboard, _version(store))
    store.approve_asset_request("broll_01_storyboard", _version(store))
    store.link_asset_request("broll_01", video, _version(store))
    with pytest.raises(EngineException) as blocked:
        store.reset_asset_request("broll_01_storyboard", _version(store))
    assert "reset the video first" in blocked.value.error.message
    reset = store.reset_asset_request("broll_01", _version(store))
    assert "broll_01" not in reset.links
    reset = store.reset_asset_request("broll_01_storyboard", _version(store))
    assert reset.links == {} and reset.approved == []
    # The old file is no longer linked, so the person can remove it from the project.
    store.remove_asset(storyboard)
    assert storyboard not in store.active_assets()


def test_only_a_linked_request_can_be_approved(project: tuple[ProjectStore, str, str]) -> None:
    store, _, _ = project
    with pytest.raises(EngineException) as info:
        store.approve_asset_request("broll_01_storyboard", _version(store))
    assert info.value.error.code is ErrorCode.INVALID_ARGUMENT
    with pytest.raises(ValidationError):
        AssetChecklist(
            project_id=store.project_id,
            configured=True,
            requests=_requests(),
            approved=["broll_01"],
        )


def test_checklists_without_review_keep_the_old_behaviour(tmp_path: Path, media_dir: Path) -> None:
    store = ProjectStore.create(tmp_path, "legacy")
    profile = RenderProfile(id="r", width=180, height=320, frame_rate=FrameRate(num=30))
    store.create_timeline(profile, 30)
    seed = _cut(store, media_dir)
    still = store.import_asset(media_dir / "still_storyboard_9x16.png", AssetRole.REFERENCE, EXTERNAL)
    store.save_asset_checklist(
        AssetChecklist(
            project_id=store.project_id,
            configured=True,
            requests=[AssetRequest(id="gen_still", kind="image", prompt="A picture")],
        ),
        0,
    )
    store.link_asset_request("gen_still", still.id, 1)
    assert store.commit_timeline(seed, 0).revision == 1  # linked is enough; no approval asked


def test_desktop_operations_approve_and_reset(tmp_path: Path, media_dir: Path) -> None:
    store = ProjectStore.create(tmp_path, "desktop")
    still = store.import_asset(media_dir / "still_storyboard_9x16.png", AssetRole.REFERENCE, EXTERNAL)
    store.save_asset_checklist(
        AssetChecklist(
            project_id=store.project_id,
            configured=True,
            review_required=True,
            requests=[AssetRequest(id="gen_still", kind="image", prompt="A picture")],
        ),
        0,
    )
    store.link_asset_request("gen_still", still.id, 1)
    service = DesktopService(tmp_path)
    try:
        args = {"project_id": store.project_id, "request_id": "gen_still", "expected_version": 2}
        approved = service._desktop_call("approve_asset_request", args)
        assert approved["approved"] == ["gen_still"]
        reset = service._desktop_call("reset_asset_request", {**args, "expected_version": 3})
        assert reset["links"] == {} and reset["approved"] == []
    finally:
        service.http.server_close()


def test_a_removed_file_cannot_be_linked(project: tuple[ProjectStore, str, str]) -> None:
    store, storyboard, _ = project
    store.remove_asset(storyboard)
    with pytest.raises(EngineException) as info:
        store.link_asset_request("broll_01_storyboard", storyboard, _version(store))
    assert info.value.error.code is ErrorCode.ASSET_NOT_FOUND
    assert "removed" in info.value.error.message
    assert store.asset_checklist().links == {}


def test_a_video_cannot_be_saved_approved_without_its_storyboard(
    project: tuple[ProjectStore, str, str],
) -> None:
    store, storyboard, video = project
    with pytest.raises(ValidationError, match="storyboard approved or skipped first"):
        AssetChecklist(
            project_id=store.project_id,
            configured=True,
            review_required=True,
            requests=_requests(),
            links={"broll_01_storyboard": storyboard, "broll_01": video},
            approved=["broll_01"],
        )
    assert AssetChecklist(  # fine once the storyboard is approved or skipped
        project_id=store.project_id,
        configured=True,
        review_required=True,
        requests=_requests(),
        links={"broll_01_storyboard": "dropped", "broll_01": video},
        approved=["broll_01"],
    )


def test_replacing_a_file_is_one_step_and_failure_keeps_the_old_one(
    project: tuple[ProjectStore, str, str], media_dir: Path
) -> None:
    store, storyboard, video = project
    store.link_asset_request("broll_01_storyboard", storyboard, _version(store))
    store.approve_asset_request("broll_01_storyboard", _version(store))
    # A rejected replacement (a video file on an image request) must leave the approved file untouched.
    with pytest.raises(EngineException):
        store.replace_asset_request_link("broll_01_storyboard", video, _version(store))
    kept = store.asset_checklist()
    assert kept.links["broll_01_storyboard"] == storyboard
    assert kept.approved == ["broll_01_storyboard"]
    # A valid replacement swaps the file and clears the approval in the same transaction.
    other = store.import_asset(media_dir / "still_storyboard_9x16.png", AssetRole.REFERENCE, EXTERNAL)
    swapped = store.replace_asset_request_link("broll_01_storyboard", other.id, _version(store))
    assert swapped.links["broll_01_storyboard"] == other.id
    assert swapped.approved == []


def test_replacing_respects_the_storyboard_rules(project: tuple[ProjectStore, str, str]) -> None:
    store, storyboard, video = project
    with pytest.raises(EngineException) as early:  # video before its storyboard is approved
        store.replace_asset_request_link("broll_01", video, _version(store))
    assert "storyboard" in early.value.error.message
    store.link_asset_request("broll_01_storyboard", storyboard, _version(store))
    store.approve_asset_request("broll_01_storyboard", _version(store))
    store.link_asset_request("broll_01", video, _version(store))
    with pytest.raises(EngineException) as made:  # the storyboard cannot change under a finished video
        store.replace_asset_request_link("broll_01_storyboard", storyboard, _version(store))
    assert "reset the video first" in made.value.error.message


def test_resetting_an_unlinked_request_changes_nothing(project: tuple[ProjectStore, str, str]) -> None:
    store, _, _ = project
    before = store.asset_checklist()
    assert store.reset_asset_request("broll_01_storyboard", before.version) == before


def test_desktop_operation_replaces_a_file(tmp_path: Path, media_dir: Path) -> None:
    store = ProjectStore.create(tmp_path, "desktop-replace")
    first = store.import_asset(media_dir / "still_storyboard_9x16.png", AssetRole.REFERENCE, EXTERNAL)
    second = store.import_asset(media_dir / "still_storyboard_9x16.png", AssetRole.REFERENCE, EXTERNAL)
    store.save_asset_checklist(
        AssetChecklist(
            project_id=store.project_id,
            configured=True,
            review_required=True,
            requests=[AssetRequest(id="gen_still", kind="image", prompt="A picture")],
        ),
        0,
    )
    store.link_asset_request("gen_still", first.id, 1)
    service = DesktopService(tmp_path)
    try:
        saved = service._desktop_call(
            "replace_asset_request_link",
            {
                "project_id": store.project_id,
                "request_id": "gen_still",
                "asset_id": second.id,
                "expected_version": 2,
            },
        )
        assert saved["links"] == {"gen_still": second.id}
    finally:
        service.http.server_close()


# ---- review notes (roadmap Phase 2c: asset review against the brief) -----------------------------


@pytest.mark.media
def test_review_note_is_saved_with_the_approval_and_belongs_to_that_file(
    project: tuple[ProjectStore, str, str], media_dir: Path, tmp_path: Path
) -> None:
    store, storyboard, _ = project
    store.link_asset_request("broll_01_storyboard", storyboard, _version(store))
    approved = store.approve_asset_request("broll_01_storyboard", _version(store), "  Frames 1-3 only  ")
    assert approved.approved == ["broll_01_storyboard"]
    assert approved.review_notes["broll_01_storyboard"].model_dump() == {
        "asset_id": storyboard,
        "text": "Frames 1-3 only",
    }
    assert ProjectStore(store.root).asset_checklist().review_notes == approved.review_notes

    # Approving again without a note changes nothing; with a note it replaces the note.
    assert store.approve_asset_request("broll_01_storyboard", approved.version) == approved
    edited = store.approve_asset_request("broll_01_storyboard", approved.version, "Frame 2 is best")
    assert edited.version == approved.version + 1
    assert edited.review_notes["broll_01_storyboard"].text == "Frame 2 is best"

    # Replacing the file drops the note with the approval; so does a reset.
    other = store.import_asset(media_dir / "still_storyboard_9x16.png", AssetRole.REFERENCE, EXTERNAL)
    replaced = store.replace_asset_request_link("broll_01_storyboard", other.id, edited.version)
    assert replaced.review_notes == {} and replaced.approved == []
    noted = store.approve_asset_request("broll_01_storyboard", replaced.version, "Fine")
    reset = store.reset_asset_request("broll_01_storyboard", noted.version)
    assert reset.review_notes == {}

    # A note cannot be attached to a file that is not approved, or to a different file.
    with pytest.raises(ValidationError, match="review note"):
        AssetChecklist.model_validate(
            {**noted.model_dump(mode="json"), "approved": [], "review_notes": noted.review_notes}
        )


@pytest.mark.media
def test_review_note_through_the_desktop_bridge(
    project: tuple[ProjectStore, str, str], tmp_path: Path
) -> None:
    store, storyboard, _ = project
    store.link_asset_request("broll_01_storyboard", storyboard, _version(store))
    service = DesktopService(tmp_path)
    try:
        saved = service._desktop_call(
            "approve_asset_request",
            {
                "project_id": store.project_id,
                "request_id": "broll_01_storyboard",
                "expected_version": _version(store),
                "note": "Use frames 1-3",
            },
        )
        assert saved["review_notes"] == {
            "broll_01_storyboard": {"asset_id": storyboard, "text": "Use frames 1-3"}
        }
    finally:
        service.http.server_close()
