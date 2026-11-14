"""Phase One integration: immutable imports, revision locks, render, QA and MCP discovery."""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

from ageneditor.contracts import (
    Asset,
    AssetOrigin,
    AssetProvenance,
    AssetRole,
    AudioStream,
    Author,
    CheckKind,
    CheckStatus,
    Clip,
    EngineException,
    ErrorCode,
    FitMode,
    Job,
    JobKind,
    JobStatus,
    Provenance,
    RenderProfile,
    TrackId,
    Transform,
)
from ageneditor.jobs import JobManager
from ageneditor.mcp_server import build_server
from ageneditor.media.hashing import sha256_file
from ageneditor.media.probe import ProbeResult
from ageneditor.media.qa import run_qa
from ageneditor.media.render import render_timeline
from ageneditor.storage import ProjectStore
from ageneditor.timebase import FrameRate


def test_mcp_discovers_phase_one_tools(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with Client(build_server(tmp_path), raise_exceptions=True) as client:
            listed = await client.list_tools()
            names = {tool.name for tool in listed.tools}
            assert {
                "create_project",
                "register_asset",
                "validate_timeline",
                "commit_timeline",
                "start_render",
                "get_job_status",
                "run_qa",
                "get_qa_report",
            } <= names
            created = await client.call_tool("create_project", {"name": "MCP smoke"})
            assert not created.is_error
            assert created.structured_content["project_id"].startswith("project_")

    asyncio.run(exercise())


def test_asset_provenance_validation_and_v1_compatibility() -> None:
    from pydantic import ValidationError

    assert AssetProvenance().origin is AssetOrigin.ORIGINAL
    with pytest.raises(ValidationError):
        AssetProvenance(origin=AssetOrigin.GENERATED_EXTERNAL)
    with pytest.raises(ValidationError):
        AssetProvenance(origin=AssetOrigin.ORIGINAL, tool_name="ChatGPT")
    with pytest.raises(ValidationError):
        AssetProvenance(origin=AssetOrigin.GENERATED_EXTERNAL, tool_name="  ")
    legacy = Asset.model_validate(
        {
            "schema_version": 1,
            "id": "asset_old",
            "project_id": "project_old",
            "kind": "audio",
            "role": "music",
            "relative_path": "originals/old.m4a",
            "original_filename": "old.m4a",
            "sha256": "a" * 64,
            "size_bytes": 1,
            "audio": {
                "index": 0,
                "codec": "aac",
                "sample_rate": 48000,
                "channels": 2,
                "time_base_num": 1,
                "time_base_den": 48000,
                "duration_samples": 48000,
            },
        }
    )
    assert legacy.provenance.origin is AssetOrigin.ORIGINAL
    assert legacy.schema_version == 1


def test_generated_import_persists_and_mcp_lists_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "external.m4a"
    source.write_bytes(b"synthetic test source")
    monkeypatch.setattr(
        "ageneditor.storage.probe",
        lambda path: ProbeResult(
            path=str(path),
            container="mov,mp4,m4a",
            duration_seconds=None,
            size_bytes=source.stat().st_size,
            audio=AudioStream(
                index=0,
                codec="aac",
                sample_rate=48000,
                channels=2,
                time_base_num=1,
                time_base_den=48000,
                duration_samples=48000,
            ),
        ),
    )
    store = ProjectStore.create(tmp_path, "provenance")
    provenance = AssetProvenance(origin=AssetOrigin.GENERATED_EXTERNAL, tool_name="  External tool  ")
    asset = store.import_asset(source, AssetRole.MUSIC, provenance)
    assert asset.schema_version == 2
    assert asset.provenance.tool_name == "External tool"
    assert ProjectStore(store.root).assets()[asset.id].provenance == asset.provenance
    assert sha256_file(source) == asset.sha256
    assert sha256_file(store.safe_path(asset.relative_path)) == asset.sha256

    async def exercise() -> None:
        async with Client(build_server(tmp_path), raise_exceptions=True) as client:
            listed = await client.call_tool("list_assets", {"project_id": store.project_id})
            assert not listed.is_error
            assert listed.structured_content["result"][0]["provenance"] == {
                "origin": "generated-external",
                "tool_name": "External tool",
            }

    asyncio.run(exercise())


def test_mcp_stdio_keeps_protocol_stdout_clean(tmp_path: Path) -> None:
    async def exercise() -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "ageneditor.cli", "mcp", "--workspace", str(tmp_path)],
            env=os.environ.copy(),
        )
        async with Client(params, raise_exceptions=True) as client:
            listed = await client.list_tools()
            assert any(tool.name == "create_project" for tool in listed.tools)
            created = await client.call_tool("create_project", {"name": "stdio smoke"})
            assert not created.is_error
            assert created.structured_content["project_id"].startswith("project_")

    asyncio.run(exercise())


def test_job_cancellation_and_interrupted_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = ProjectStore.create(tmp_path, "jobs")
    profile = RenderProfile(id="job_profile", width=180, height=320, frame_rate=FrameRate(num=30))
    store.create_timeline(profile, target_duration_frames=30)
    started = threading.Event()

    def waiting_render(*_args: object, cancel: threading.Event, **_kwargs: object) -> None:
        started.set()
        if cancel.wait(5):
            raise EngineException(ErrorCode.JOB_CANCELED, "render was canceled")
        raise RuntimeError("render was not canceled")

    monkeypatch.setattr("ageneditor.jobs.render_timeline", waiting_render)
    manager = JobManager(store)
    job = manager.start_render(0)
    assert started.wait(5)
    manager.cancel(job.id)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not manager.status(job.id).is_terminal:
        time.sleep(0.05)
    assert manager.status(job.id).status is JobStatus.CANCELED
    assert ProjectStore(store.root).get_job(job.id).status is JobStatus.CANCELED

    crashed = Job(
        id="job_crashed",
        project_id=store.project_id,
        kind=JobKind.FINAL_RENDER,
        status=JobStatus.RUNNING,
        timeline_revision=0,
    )
    store.start_job(crashed)
    store.recover_interrupted_jobs()
    assert store.get_job(crashed.id).status is JobStatus.INTERRUPTED


@pytest.mark.media
def test_mcp_stdio_edit_render_and_qa(tmp_path: Path, media_dir: Path) -> None:
    async def exercise() -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "ageneditor.cli", "mcp", "--workspace", str(tmp_path)],
            env=os.environ.copy(),
        )
        async with Client(params, raise_exceptions=True) as client:
            created = await client.call_tool("create_project", {"name": "MCP reference"})
            project_id = created.structured_content["project_id"]
            imports = Path(created.structured_content["import_directory"])
            shutil.copyfile(media_dir / "talking_head_16x9_30fps_60s.mp4", imports / "source.mp4")
            registered = await client.call_tool(
                "register_asset",
                {
                    "project_id": project_id,
                    "import_path": "source.mp4",
                    "role": "primary",
                    "provenance": {"origin": "generated-external", "tool_name": "Google Flow"},
                },
            )
            assert not registered.is_error
            assert registered.structured_content["provenance"] == {
                "origin": "generated-external",
                "tool_name": "Google Flow",
            }
            listed_assets = await client.call_tool("list_assets", {"project_id": project_id})
            assert not listed_assets.is_error
            assert listed_assets.structured_content["result"][0]["provenance"]["tool_name"] == "Google Flow"
            asset_id = registered.structured_content["id"]
            profile = RenderProfile(id="mcp_test", width=180, height=320, frame_rate=FrameRate(num=30))
            seeded = await client.call_tool(
                "create_timeline",
                {
                    "project_id": project_id,
                    "render_profile": profile.model_dump(mode="json"),
                    "target_duration_frames": 30,
                },
            )
            plan = seeded.structured_content
            plan["clips"] = [
                Clip(
                    clip_id="cut",
                    asset_id=asset_id,
                    track=TrackId.V1,
                    source_in_frames=300,
                    source_out_frames=330,
                    timeline_in_frames=0,
                ).model_dump(mode="json")
            ]
            checked = await client.call_tool(
                "validate_timeline", {"project_id": project_id, "timeline": plan}
            )
            assert checked.structured_content["valid"] is True
            committed = await client.call_tool(
                "commit_timeline", {"project_id": project_id, "timeline": plan, "expected_revision": 0}
            )
            assert committed.structured_content["revision"] == 1
            conflict = await client.call_tool(
                "commit_timeline", {"project_id": project_id, "timeline": plan, "expected_revision": 0}
            )
            assert conflict.is_error
            assert any("REVISION_CONFLICT" in item.text for item in conflict.content)
            started = await client.call_tool("start_render", {"project_id": project_id, "revision": 1})
            job_id = started.structured_content["id"]
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                status = await client.call_tool(
                    "get_job_status", {"project_id": project_id, "job_id": job_id}
                )
                if status.structured_content["status"] in {"succeeded", "failed", "canceled", "interrupted"}:
                    break
                await asyncio.sleep(0.2)
            assert status.structured_content["status"] == "succeeded", status.structured_content
            checked = await client.call_tool("run_qa", {"project_id": project_id, "job_id": job_id})
            # Editorial flags (jump cuts, crop risk) may WARN; every technical check must pass.
            assert checked.structured_content["overall"] in {"pass", "warn"}, checked.structured_content
            assert all(
                c["status"] == "pass"
                for c in checked.structured_content["checks"]
                if c["kind"] == "technical"
            ), checked.structured_content
            output = imports.parent / status.structured_content["output_path"]
            assert output.is_file() and (output.parent / "timeline.json").is_file()
            assert (output.parent / "qa.json").is_file()

    asyncio.run(exercise())


@pytest.mark.media
def test_three_cuts_render_with_immutable_source_and_qa(tmp_path: Path, media_dir: Path) -> None:
    store = ProjectStore.create(tmp_path, "three cuts")
    incoming = store.safe_path("imports/source.mp4")
    shutil.copyfile(media_dir / "talking_head_16x9_30fps_60s.mp4", incoming)
    with pytest.raises(EngineException) as unsafe:
        store.register_import("../source.mp4", AssetRole.PRIMARY)
    assert unsafe.value.error.code is ErrorCode.PERMISSION_DENIED
    original_hash = sha256_file(incoming)
    asset = store.register_import("source.mp4", AssetRole.PRIMARY)
    music_incoming = store.safe_path("imports/music.m4a")
    shutil.copyfile(media_dir / "music_bed_30s.m4a", music_incoming)
    music = store.register_import(
        "music.m4a",
        AssetRole.MUSIC,
        AssetProvenance(origin=AssetOrigin.GENERATED_EXTERNAL, tool_name="External music tool"),
    )
    profile = RenderProfile(id="test_vertical", width=180, height=320, frame_rate=FrameRate(num=30))
    seed = store.create_timeline(profile, target_duration_frames=90)
    clips = [
        Clip(
            clip_id=f"cut_{index}",
            asset_id=asset.id,
            track=TrackId.V1,
            source_in_frames=start,
            source_out_frames=start + 30,
            timeline_in_frames=index * 30,
            fit_mode=FitMode.CONTAIN if index == 0 else FitMode.COVER,
            transform=Transform(scale=1.5) if index == 0 else Transform(),
        )
        for index, start in enumerate((0, 300, 600))
    ]
    clips.append(
        Clip(
            clip_id="music",
            asset_id=music.id,
            track=TrackId.A2,
            source_in_frames=0,
            source_out_frames=90,
            timeline_in_frames=0,
            gain_db=-12,
        )
    )
    candidate = seed.model_copy(update={"clips": clips, "provenance": Provenance(author=Author.AGENT)})
    committed = store.commit_timeline(candidate, expected_revision=0)
    assert committed.revision == 1
    assert store.timeline(0).clips == []
    assert store.timeline(1).clips == clips
    assert store.safe_path("timelines/r1.json").is_file()
    with pytest.raises(EngineException) as conflict:
        store.commit_timeline(candidate, expected_revision=0)
    assert conflict.value.error.code is ErrorCode.REVISION_CONFLICT

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
    assert output.is_file()
    assert output.name == "final.mp4"
    assert (output.parent / "timeline.json").is_file()
    report = run_qa(store, job.id, committed.revision, output)
    assert [(item.asset_id, item.tool_name) for item in report.generated_assets] == [
        (music.id, "External music tool")
    ]
    assert "External music tool" in (output.parent / "qa.json").read_text(encoding="utf-8")
    assert report.overall is not CheckStatus.FAIL, report.checks
    assert all(c.status is CheckStatus.PASS for c in report.checks if c.kind is CheckKind.TECHNICAL), [
        c for c in report.checks if c.status is not CheckStatus.PASS
    ]
    assert store.get_qa_report(job.id).id == report.id
    assert (output.parent / "qa.json").is_file()
    assert sha256_file(incoming) == original_hash
    assert sha256_file(store.safe_path(asset.relative_path)) == asset.sha256
    registered = store.safe_path(asset.relative_path)
    registered.chmod(0o666)
    registered.write_bytes(b"tampered project copy")
    with pytest.raises(EngineException) as changed:
        render_timeline(store, committed, store.safe_path("renders/tampered.mp4"))
    assert changed.value.error.code is ErrorCode.SOURCE_CHANGED
    assert run_qa(store, job.id, committed.revision, output).overall is CheckStatus.FAIL


@pytest.mark.media
@pytest.mark.parametrize(
    ("filename", "source_frames", "output_frames"),
    [
        ("talking_head_9x16_2997fps_20s.mp4", 600, 601),
        ("vfr_phone_10s.mp4", 270, 300),
    ],
)
def test_source_frame_ranges_map_to_output_grid(
    tmp_path: Path, media_dir: Path, filename: str, source_frames: int, output_frames: int
) -> None:
    store = ProjectStore.create(tmp_path, "frame map")
    incoming = store.safe_path(f"imports/{filename}")
    shutil.copyfile(media_dir / filename, incoming)
    asset = store.register_import(filename, AssetRole.PRIMARY)
    profile = RenderProfile(id="test_vertical", width=180, height=320, frame_rate=FrameRate(num=30))
    seed = store.create_timeline(profile, target_duration_frames=output_frames)
    clip = Clip(
        clip_id="mapped",
        asset_id=asset.id,
        track=TrackId.V1,
        source_in_frames=0,
        source_out_frames=source_frames,
        timeline_in_frames=0,
        timeline_duration_frames=output_frames,
    )
    candidate = seed.model_copy(update={"clips": [clip]})
    committed = store.commit_timeline(candidate, expected_revision=0)
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
    report = run_qa(store, job.id, committed.revision, output)
    assert report.overall is not CheckStatus.FAIL, report.checks
    assert all(c.status is CheckStatus.PASS for c in report.checks if c.kind is CheckKind.TECHNICAL), [
        c for c in report.checks if c.status is not CheckStatus.PASS
    ]
