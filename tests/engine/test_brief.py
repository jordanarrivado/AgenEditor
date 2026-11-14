"""Client brief: person-written, versioned, read-only for agents, reported honestly in QA."""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path

import pytest
from mcp import Client
from pydantic import ValidationError

from ageneditor.contracts import (
    AssetRole,
    BriefItem,
    CheckKind,
    CheckStatus,
    Clip,
    EngineException,
    ErrorCode,
    JobStatus,
    ProjectBrief,
    RenderProfile,
    TrackId,
)
from ageneditor.desktop_service import DesktopService
from ageneditor.jobs import JobManager
from ageneditor.mcp_server import build_server
from ageneditor.media.qa import run_qa
from ageneditor.storage import ProjectStore
from ageneditor.timebase import FrameRate


def _brief(store: ProjectStore, **update: object) -> ProjectBrief:
    base = {
        "project_id": store.project_id,
        "brief_text": "30 s ad for a travel adapter.",
        "script_text": "Never hunt for a socket again.",
        "reference_note": "Competitor ad, link in the client email",
        "checklist": [
            BriefItem(id="item_product", text="Product on screen by 0:05"),
            BriefItem(id="item_eu", text="European sockets shown", status="met"),
        ],
    }
    return ProjectBrief.model_validate({**base, **update})


def test_brief_is_versioned_per_project_and_survives_reopen(tmp_path: Path) -> None:
    first = ProjectStore.create(tmp_path, "first")
    second = ProjectStore.create(tmp_path, "second")
    assert first.brief().version == 0
    saved = first.save_brief(_brief(first), 0)
    assert saved.version == 1
    assert ProjectStore(first.root).brief() == saved
    assert second.brief().version == 0
    with pytest.raises(EngineException) as stale:
        first.save_brief(_brief(first), 0)
    assert stale.value.error.code is ErrorCode.REVISION_CONFLICT
    with pytest.raises(EngineException) as foreign:
        first.save_brief(_brief(second), 1)
    assert foreign.value.error.code is ErrorCode.PERMISSION_DENIED


def test_brief_rejects_duplicate_or_empty_items() -> None:
    item = BriefItem(id="item_a", text="x")
    with pytest.raises(ValidationError):
        ProjectBrief(project_id="project_a", checklist=[item, item])
    with pytest.raises(ValidationError):
        BriefItem(id="item_b", text="   ")
    with pytest.raises(ValidationError):
        BriefItem(id="item_c", text="ok", status="passed")  # type: ignore[arg-type]


def test_agents_can_read_the_brief_but_not_write_it(tmp_path: Path) -> None:
    store = ProjectStore.create(tmp_path, "mcp")
    store.save_brief(_brief(store), 0)

    async def go() -> None:
        async with Client(build_server(tmp_path), raise_exceptions=True) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            assert "get_brief" in names
            assert not {name for name in names if "brief" in name and name != "get_brief"}
            result = await client.call_tool("get_brief", {"project_id": store.project_id})
            assert result.structured_content["brief_text"].startswith("30 s ad")
            assert result.structured_content["checklist"][1]["status"] == "met"

    asyncio.run(go())


def test_desktop_operations_round_trip_with_version_check(tmp_path: Path) -> None:
    store = ProjectStore.create(tmp_path, "desktop")
    service = DesktopService.__new__(DesktopService)
    service.workspace = tmp_path
    body = _brief(store).model_dump(mode="json", exclude={"project_id", "version", "schema_version"})
    saved = service._desktop_call(
        "save_brief", {"project_id": store.project_id, "expected_version": 0, "brief": body}
    )
    assert saved["version"] == 1
    assert service._desktop_call("get_brief", {"project_id": store.project_id})["version"] == 1
    with pytest.raises(EngineException):
        service._desktop_call(
            "save_brief", {"project_id": store.project_id, "expected_version": 0, "brief": body}
        )


@pytest.mark.media
def test_qa_reports_brief_items_a_person_confirmed_not_machine_checks(
    tmp_path: Path, media_dir: Path
) -> None:
    store = ProjectStore.create(tmp_path, "qa")
    shutil.copyfile(media_dir / "talking_head_16x9_30fps_60s.mp4", store.safe_path("imports/source.mp4"))
    asset = store.register_import("source.mp4", AssetRole.PRIMARY)
    profile = RenderProfile(id="brief_qa", width=180, height=320, frame_rate=FrameRate(num=30))
    seed = store.create_timeline(profile, target_duration_frames=30)
    clip = Clip(
        clip_id="cut_0",
        asset_id=asset.id,
        track=TrackId.V1,
        source_in_frames=0,
        source_out_frames=30,
        timeline_in_frames=0,
    )
    committed = store.commit_timeline(seed.model_copy(update={"clips": [clip]}), expected_revision=0)
    manager = JobManager(store)
    job = manager.start_render(committed.revision)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        job = manager.status(job.id)
        if job.is_terminal:
            break
        time.sleep(0.2)
    assert job.status is JobStatus.SUCCEEDED, job.error
    output = store.safe_path(job.output_path)

    # No brief: the check is absent, not a pass.
    assert all(
        c.check_id != "brief_checklist" for c in run_qa(store, job.id, committed.revision, output).checks
    )

    store.save_brief(_brief(store), 0)
    report = run_qa(store, job.id, committed.revision, output)
    check = next(c for c in report.checks if c.check_id == "brief_checklist")
    assert check.kind is CheckKind.EDITORIAL
    assert check.status is CheckStatus.WARN
    assert check.measured == "1 of 2 confirmed"
    assert "item_product" in check.message and "did not check the video" in check.message
    assert report.overall is not CheckStatus.FAIL  # an open item warns; it never fails a render

    waived = [BriefItem(id="item_product", text="Product on screen by 0:05", status="waived")]
    store.save_brief(_brief(store, checklist=waived), 1)
    passed = run_qa(store, job.id, committed.revision, output)
    assert next(c for c in passed.checks if c.check_id == "brief_checklist").status is CheckStatus.PASS
