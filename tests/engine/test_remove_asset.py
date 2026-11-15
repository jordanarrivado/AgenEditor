"""Removing an asset hides it from the project without deleting anything."""

from __future__ import annotations

from pathlib import Path

import pytest

from ageneditor.contracts import AssetRole, Clip, EngineException, ErrorCode, RenderProfile, TrackId
from ageneditor.desktop_service import DesktopService
from ageneditor.storage import ProjectStore
from ageneditor.timebase import FrameRate


def _clip(asset_id: str, clip_id: str = "c1") -> Clip:
    return Clip(
        clip_id=clip_id,
        asset_id=asset_id,
        track=TrackId.V1,
        source_in_frames=0,
        source_out_frames=30,
        timeline_in_frames=0,
    )


@pytest.fixture
def project(tmp_path: Path, media_dir: Path) -> tuple[ProjectStore, str, str]:
    store = ProjectStore.create(tmp_path, "remove")
    keep = store.import_asset(media_dir / "talking_head_16x9_30fps_60s.mp4", AssetRole.PRIMARY)
    extra = store.import_asset(media_dir / "broll_9x16_10s.mp4", AssetRole.PRIMARY)
    store.create_timeline(RenderProfile(id="r", width=180, height=320, frame_rate=FrameRate(num=30)), 30)
    return store, keep.id, extra.id


def test_removed_asset_is_hidden_but_kept_on_disk(project: tuple[ProjectStore, str, str]) -> None:
    store, keep, extra = project
    copy = store.safe_path(store.assets()[extra].relative_path)
    store.remove_asset(extra)
    assert set(store.active_assets()) == {keep}
    assert extra in store.assets()  # earlier revisions can still resolve it
    assert copy.is_file()
    with pytest.raises(EngineException) as again:
        store.remove_asset(extra)
    assert again.value.error.code is ErrorCode.ASSET_NOT_FOUND


def test_new_revisions_cannot_use_a_removed_asset(project: tuple[ProjectStore, str, str]) -> None:
    store, _, extra = project
    store.remove_asset(extra)
    seed = store.timeline()
    candidate = seed.model_copy(update={"clips": [_clip(extra)]})
    assert any("was removed" in issue.message for issue in store.removed_asset_issues(candidate))
    with pytest.raises(EngineException) as info:
        store.commit_timeline(candidate, seed.revision)
    assert info.value.error.code is ErrorCode.INVALID_TIMELINE


def test_asset_in_the_current_edit_cannot_be_removed(project: tuple[ProjectStore, str, str]) -> None:
    store, keep, extra = project
    seed = store.timeline()
    committed = store.commit_timeline(seed.model_copy(update={"clips": [_clip(keep)]}), seed.revision)
    with pytest.raises(EngineException) as info:
        store.remove_asset(keep)
    assert info.value.error.code is ErrorCode.PERMISSION_DENIED
    assert f"revision {committed.revision}" in info.value.error.message
    # Once a newer revision drops it, removal is allowed and the old revision stays readable.
    store.commit_timeline(committed.model_copy(update={"clips": [_clip(extra)]}), committed.revision)
    store.remove_asset(keep)
    assert store.timeline(committed.revision).clips[0].asset_id == keep


def test_desktop_summary_and_remove_operation(project: tuple[ProjectStore, str, str], tmp_path: Path) -> None:
    store, keep, extra = project
    service = DesktopService(tmp_path)
    try:
        assert service._desktop_call("remove_asset", {"project_id": store.project_id, "asset_id": extra}) == {
            "removed": extra
        }
        summary = service._desktop_call("project_summary", {"project_id": store.project_id})
        assert [asset["id"] for asset in summary["assets"]] == [keep]
    finally:
        service.http.server_close()


def test_an_asset_used_in_history_can_come_back_through_a_revert(
    project: tuple[ProjectStore, str, str],
) -> None:
    store, keep, extra = project
    first = store.commit_timeline(store.timeline().model_copy(update={"clips": [_clip(keep)]}), 0)
    second = store.commit_timeline(first.model_copy(update={"clips": [_clip(extra)]}), first.revision)
    store.remove_asset(keep)  # revision 2 no longer uses it
    # Reverting to revision 1 cites the removed asset again; history must stay restorable.
    restored = store.commit_timeline(first.model_copy(update={"clips": [_clip(keep)]}), second.revision)
    assert restored.clips[0].asset_id == keep
    assert store.removed_asset_issues(restored) == []


def test_a_removed_asset_that_no_revision_used_is_still_refused(
    project: tuple[ProjectStore, str, str],
) -> None:
    store, _, extra = project
    store.remove_asset(extra)
    candidate = store.timeline().model_copy(update={"clips": [_clip(extra)]})
    assert [issue.message for issue in store.removed_asset_issues(candidate)] == [
        f"asset {extra!r} was removed from the project"
    ]
