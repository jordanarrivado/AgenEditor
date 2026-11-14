"""Project-owned request checklist and execution gate."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from mcp import Client
from pydantic import ValidationError

from ageneditor.contracts import (
    AssetChecklist,
    AssetOrigin,
    AssetProvenance,
    AssetRequest,
    AssetRole,
    EngineException,
    ErrorCode,
    RenderProfile,
    VideoStream,
)
from ageneditor.desktop_service import DesktopService
from ageneditor.jobs import JobManager
from ageneditor.mcp_server import build_server
from ageneditor.media.probe import ProbeResult
from ageneditor.storage import ProjectStore
from ageneditor.timebase import FrameRate


def _request(request_id: str = "gen_music") -> AssetRequest:
    return AssetRequest(
        id=request_id, kind="video", purpose="cover a cut", prompt="A quiet street", aspect="9:16"
    )


def test_checklist_is_project_owned_versioned_and_gates_execution(tmp_path: Path) -> None:
    first = ProjectStore.create(tmp_path, "first")
    second = ProjectStore.create(tmp_path, "second")
    profile = RenderProfile(id="checklist_test", width=180, height=320, frame_rate=FrameRate(num=30))
    seed = first.create_timeline(profile, 30)
    assert not first.asset_checklist().configured
    assert second.asset_checklist().version == 0

    pending = first.save_asset_checklist(
        AssetChecklist(project_id=first.project_id, configured=True, requests=[_request()]), 0
    )
    assert pending.version == 1
    assert ProjectStore(first.root).asset_checklist() == pending
    assert second.asset_checklist().version == 0
    with pytest.raises(EngineException) as stale:
        first.save_asset_checklist(pending, 0)
    assert stale.value.error.code is ErrorCode.REVISION_CONFLICT
    with pytest.raises(EngineException) as commit:
        first.commit_timeline(seed, 0)
    assert commit.value.error.code is ErrorCode.INVALID_ARGUMENT
    with pytest.raises(EngineException) as render:
        JobManager(first).start_render(0)
    assert render.value.error.code is ErrorCode.INVALID_ARGUMENT

    dropped = first.save_asset_checklist(
        AssetChecklist(
            project_id=first.project_id,
            configured=True,
            requests=[_request()],
            links={"gen_music": "dropped"},
        ),
        1,
    )
    assert dropped.version == 2
    first.require_asset_requests_ready()
    assert ProjectStore(first.root).asset_checklist().links == {"gen_music": "dropped"}

    async def read_through_mcp() -> None:
        async with Client(build_server(tmp_path), raise_exceptions=True) as client:
            result = await client.call_tool("get_asset_checklist", {"project_id": first.project_id})
            assert not result.is_error
            assert result.structured_content["version"] == 2
            assert result.structured_content["links"] == {"gen_music": "dropped"}

    asyncio.run(read_through_mcp())


def test_checklist_links_only_matching_registered_generated_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"synthetic metadata-only import")
    monkeypatch.setattr(
        "ageneditor.storage.probe",
        lambda path: ProbeResult(
            path=str(path),
            container="mov,mp4,m4a",
            duration_seconds=None,
            size_bytes=source.stat().st_size,
            video=VideoStream(
                index=0,
                codec="h264",
                width=180,
                height=320,
                frame_rate=FrameRate(num=30),
                time_base_num=1,
                time_base_den=30,
                duration_frames=30,
            ),
        ),
    )
    store = ProjectStore.create(tmp_path, "links")
    store.save_asset_checklist(
        AssetChecklist(
            project_id=store.project_id,
            configured=True,
            requests=[AssetRequest(id="gen_audio", kind="image", prompt="an image")],
        ),
        0,
    )
    original = store.import_asset(source, AssetRole.BROLL)
    generated = store.import_asset(
        source,
        AssetRole.BROLL,
        AssetProvenance(origin=AssetOrigin.GENERATED_EXTERNAL, tool_name="External tool"),
    )
    with pytest.raises(EngineException) as missing:
        store.link_asset_request("gen_audio", "asset_missing", 1)
    assert missing.value.error.code is ErrorCode.ASSET_NOT_FOUND
    with pytest.raises(EngineException) as not_generated:
        store.link_asset_request("gen_audio", original.id, 1)
    assert not_generated.value.error.code is ErrorCode.INVALID_ARGUMENT
    with pytest.raises(EngineException) as wrong_kind:
        store.link_asset_request("gen_audio", generated.id, 1)
    assert wrong_kind.value.error.code is ErrorCode.INVALID_ARGUMENT

    video_request = AssetChecklist(
        project_id=store.project_id,
        configured=True,
        requests=[AssetRequest(id="gen_music", kind="video", prompt="a street")],
    )
    store.save_asset_checklist(video_request, 1)
    generated_primary = store.import_asset(
        source,
        AssetRole.PRIMARY,
        AssetProvenance(origin=AssetOrigin.GENERATED_EXTERNAL, tool_name="External tool"),
    )
    with pytest.raises(EngineException) as wrong_role:
        store.link_asset_request("gen_music", generated_primary.id, 2)
    assert wrong_role.value.error.code is ErrorCode.INVALID_ARGUMENT
    ready = store.link_asset_request("gen_music", generated.id, 2)
    assert ready.links == {"gen_music": generated.id}
    assert ProjectStore(store.root).asset_checklist().links == ready.links
    store.require_asset_requests_ready()
    with pytest.raises(EngineException) as stale:
        store.link_asset_request("gen_music", generated.id, 2)
    assert stale.value.error.code is ErrorCode.REVISION_CONFLICT

    service = DesktopService.__new__(DesktopService)
    service.workspace = tmp_path
    read = service._desktop_call("get_asset_checklist", {"project_id": store.project_id})
    assert read["links"] == {"gen_music": generated.id}
    saved = service._desktop_call(
        "save_asset_checklist",
        {
            "project_id": store.project_id,
            "expected_version": ready.version,
            "checklist": {
                "configured": True,
                "requests": [video_request.requests[0].model_dump(mode="json")],
                "links": {"gen_music": "dropped"},
            },
        },
    )
    assert saved["version"] == ready.version + 1
    assert saved["links"] == {"gen_music": "dropped"}


def test_checklist_rejects_duplicate_requests_and_dangling_links() -> None:
    with pytest.raises(ValidationError):
        AssetChecklist(project_id="project_a", configured=True, requests=[_request(), _request()])
    with pytest.raises(ValidationError):
        AssetChecklist(project_id="project_a", configured=True, links={"missing": "dropped"})


def test_accepted_issue_needs_a_linked_file_and_clears_on_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"synthetic metadata-only import")
    monkeypatch.setattr(
        "ageneditor.storage.probe",
        lambda path: ProbeResult(
            path=str(path),
            container="mov,mp4,m4a",
            duration_seconds=None,
            size_bytes=source.stat().st_size,
            video=VideoStream(
                index=0,
                codec="h264",
                width=180,
                height=320,
                frame_rate=FrameRate(num=30),
                time_base_num=1,
                time_base_den=30,
                duration_frames=30,
            ),
        ),
    )
    store = ProjectStore.create(tmp_path, "accept")
    checklist = store.save_asset_checklist(
        AssetChecklist(project_id=store.project_id, configured=True, requests=[_request()]), 0
    )
    with pytest.raises(EngineException) as unlinked:
        store.accept_asset_issue("gen_music", "logo is slightly off", checklist.version)
    assert unlinked.value.error.code is ErrorCode.INVALID_ARGUMENT

    generated = store.import_asset(
        source,
        AssetRole.BROLL,
        AssetProvenance(origin=AssetOrigin.GENERATED_EXTERNAL, tool_name="External tool"),
    )
    linked = store.link_asset_request("gen_music", generated.id, checklist.version)
    accepted = store.accept_asset_issue("gen_music", "  hand looks odd  ", linked.version)
    assert accepted.accepted["gen_music"].asset_id == generated.id
    assert accepted.accepted["gen_music"].note == "hand looks odd"
    assert ProjectStore(store.root).asset_checklist().accepted == accepted.accepted
    store.require_asset_requests_ready()
    with pytest.raises(EngineException) as stale:
        store.accept_asset_issue("gen_music", "again", linked.version)
    assert stale.value.error.code is ErrorCode.REVISION_CONFLICT

    # A reset that leaves the acceptance behind is rejected; the log must follow the link.
    with pytest.raises(ValidationError):
        AssetChecklist(
            project_id=store.project_id,
            configured=True,
            requests=[_request()],
            accepted=accepted.accepted,
        )
    cleared = store.accept_asset_issue("gen_music", None, accepted.version)
    assert cleared.accepted == {}
    with pytest.raises(ValidationError):
        store.accept_asset_issue("gen_music", "   ", cleared.version)
