"""Build the Phase One 40-second reference project from the synthetic fixture."""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

from ageneditor.contracts import REEL_FINAL, AssetRole, Author, Clip, JobStatus, Provenance, TrackId
from ageneditor.jobs import JobManager
from ageneditor.media.hashing import sha256_file
from ageneditor.media.qa import run_qa
from ageneditor.storage import ProjectStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument(
        "--fixture", type=Path, default=Path("tests/fixtures/media/talking_head_16x9_30fps_60s.mp4")
    )
    args = parser.parse_args()
    args.workspace.mkdir(parents=True, exist_ok=True)
    store = ProjectStore.create(args.workspace, "phase1-reference")
    incoming = store.safe_path(f"imports/{args.fixture.name}")
    shutil.copyfile(args.fixture, incoming)
    fixture_hash = sha256_file(args.fixture)
    asset = store.register_import(args.fixture.name, AssetRole.PRIMARY)
    seed = store.create_timeline(REEL_FINAL, target_duration_frames=1200)
    cuts = ((0, 300), (450, 900), (1200, 1650))
    clips = []
    at = 0
    for index, (source_in, source_out) in enumerate(cuts):
        clips.append(
            Clip(
                clip_id=f"cut_{index + 1}",
                asset_id=asset.id,
                track=TrackId.V1,
                source_in_frames=source_in,
                source_out_frames=source_out,
                timeline_in_frames=at,
            )
        )
        at += source_out - source_in
    candidate = seed.model_copy(
        update={
            "clips": clips,
            "provenance": Provenance(
                author=Author.AGENT, tool="phase1_reference", summary="Three known source ranges"
            ),
        }
    )
    timeline = store.commit_timeline(candidate, expected_revision=0)
    manager = JobManager(store)
    job = manager.start_render(timeline.revision)
    deadline = time.monotonic() + 900
    max_running_progress = 0.0
    while time.monotonic() < deadline:
        job = manager.status(job.id)
        if job.status is JobStatus.RUNNING:
            max_running_progress = max(max_running_progress, job.progress)
        if job.is_terminal:
            break
        time.sleep(0.5)
    if job.status is not JobStatus.SUCCEEDED:
        manager.cancel(job.id)
        raise RuntimeError(f"render did not succeed: {job.model_dump_json()}")
    output = store.safe_path(job.output_path)
    report = run_qa(store, job.id, timeline.revision, output)
    # Editorial flags (e.g. same-angle jump cuts in this three-cut reference) may warn; every technical
    # check must pass.
    technical_ok = all(c.status.value == "pass" for c in report.checks if c.kind.value == "technical")
    if not technical_ok or sha256_file(args.fixture) != fixture_hash:
        raise RuntimeError(f"reference QA failed: {report.model_dump_json()}")
    print(
        json.dumps(
            {
                "project_id": store.project_id,
                "job_id": job.id,
                "revision": timeline.revision,
                "final_mp4": str(output),
                "timeline_json": str(output.parent / "timeline.json"),
                "qa_json": str(output.parent / "qa.json"),
                "qa_overall": report.overall.value,
                "max_running_progress": max_running_progress,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
