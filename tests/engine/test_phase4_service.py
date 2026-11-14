"""Desktop and MCP adapters share one authenticated service and render worker."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest
from mcp import Client

from ageneditor.contracts import RenderProfile
from ageneditor.desktop_service import DesktopService, service_address, service_request
from ageneditor.mcp_server import build_server
from ageneditor.timebase import FrameRate


@pytest.mark.media
def test_shared_service_desktop_import_mcp_edit_and_render(
    tmp_path: Path, media_dir: Path, private_service_home: Path
) -> None:
    service = DesktopService(tmp_path)
    thread = threading.Thread(target=service.serve, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while service_address(tmp_path) is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert service_address(tmp_path) is not None
        with pytest.raises(HTTPError) as denied:
            urlopen(f"http://127.0.0.1:{service.port}/health", timeout=2)
        assert denied.value.code == 403
        profile = RenderProfile(id="desktop_test", width=180, height=320, frame_rate=FrameRate(num=30))
        created = service_request(
            tmp_path,
            "/desktop",
            {
                "operation": "create_project",
                "args": {
                    "name": "Shared edit",
                    "render_profile": profile.model_dump(mode="json"),
                    "target_duration_frames": 30,
                },
            },
        )["result"]
        project_id = created["project_id"]
        assert (
            service_request(tmp_path, "/desktop", {"operation": "list_projects", "args": {}})["result"][0][
                "id"
            ]
            == project_id
        )
        asset = service_request(
            tmp_path,
            "/desktop",
            {
                "operation": "import_file",
                "args": {
                    "project_id": project_id,
                    "source_path": str((media_dir / "talking_head_16x9_30fps_60s.mp4").resolve()),
                    "role": "primary",
                },
            },
        )["result"]

        async def edit() -> str:
            async with Client(build_server(tmp_path, shared_service=True), raise_exceptions=True) as client:
                timeline = (
                    await client.call_tool("get_timeline", {"project_id": project_id})
                ).structured_content
                timeline["clips"] = [
                    {
                        "clip_id": "cut_1",
                        "asset_id": asset["id"],
                        "track": "V1",
                        "source_in_frames": 0,
                        "source_out_frames": 30,
                        "timeline_in_frames": 0,
                    }
                ]
                committed = await client.call_tool(
                    "commit_timeline",
                    {
                        "project_id": project_id,
                        "timeline": timeline,
                        "expected_revision": 0,
                    },
                )
                assert committed.structured_content["revision"] == 1
                conflict = await client.call_tool(
                    "commit_timeline",
                    {
                        "project_id": project_id,
                        "timeline": timeline,
                        "expected_revision": 0,
                    },
                )
                assert conflict.is_error
                assert "REVISION_CONFLICT" in json.dumps([item.text for item in conflict.content])
                concurrent = await asyncio.gather(
                    *[
                        client.call_tool(
                            "commit_timeline",
                            {
                                "project_id": project_id,
                                "timeline": committed.structured_content,
                                "expected_revision": 1,
                            },
                        )
                        for _ in range(2)
                    ]
                )
                assert sum(not result.is_error for result in concurrent) == 1
                assert sum(result.is_error for result in concurrent) == 1
                started = await client.call_tool(
                    "start_render",
                    {
                        "project_id": project_id,
                        "revision": 1,
                        "final": False,
                    },
                )
                return started.structured_content["id"]

        job_id = asyncio.run(edit())
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            jobs = service_request(
                tmp_path, "/desktop", {"operation": "list_jobs", "args": {"project_id": project_id}}
            )["result"]
            job = next(job for job in jobs if job["id"] == job_id)
            if job["status"] in {"succeeded", "failed", "canceled"}:
                break
            time.sleep(0.2)
        assert job["status"] == "succeeded", job
        media = service_request(
            tmp_path,
            "/desktop",
            {"operation": "render_path", "args": {"project_id": project_id, "job_id": job_id}},
        )["result"]
        assert Path(media["path"]).is_file()
    finally:
        service.http.shutdown()
        thread.join(timeout=5)
    assert service_address(tmp_path) is None
