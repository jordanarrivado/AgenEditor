"""Exercise the shipped engine and FFmpeg with no developer tools on PATH."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from http.client import HTTPConnection
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--media", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--transcribe-preset",
        choices=["tiny", "base", "small"],
        help="also transcribe with this already cached faster-whisper model (the bundle excludes PyAV)",
    )
    args = parser.parse_args()
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=False)  # never erase a previous result
    workspace = output / "workspace"
    workspace.mkdir()
    user_data = output / "user-data"
    user_data.mkdir()
    system32 = Path(os.environ["SystemRoot"]) / "System32"
    env = {**os.environ, "PATH": str(system32), "LOCALAPPDATA": str(user_data)}
    for key in ("PYTHONPATH", "AGENEDITOR_FFMPEG", "AGENEDITOR_FFPROBE"):
        env.pop(key, None)
    original = hashlib.sha256(args.media.read_bytes()).hexdigest()
    engine = args.engine.resolve()
    key = hashlib.sha256(workspace.as_posix().lower().encode()).hexdigest()
    discovery = user_data / "AgenEditor" / "services" / key / "service.json"
    started = time.monotonic()
    report = {"status": "failed", "scope": "packaged engine; isolated PATH, not a clean Windows VM"}
    with (output / "service.log").open("wb") as log:
        process = subprocess.Popen(
            [str(engine), "service", "--workspace", str(workspace)],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            while not discovery.exists():
                if process.poll() is not None or time.monotonic() - started > 90:
                    raise RuntimeError("packaged service did not start; see service.log")
                time.sleep(0.1)
            info = json.loads(discovery.read_text())

            def request(endpoint: str, payload: dict) -> dict:
                conn = HTTPConnection("127.0.0.1", info["port"], timeout=180)
                try:
                    conn.request(
                        "POST",
                        endpoint,
                        json.dumps(payload),
                        {
                            "Authorization": f"Bearer {info['token']}",
                            "Content-Type": "application/json",
                        },
                    )
                    response = conn.getresponse()
                    body = json.loads(response.read())
                    if response.status != 200 or body.get("isError"):
                        raise RuntimeError(f"engine request failed: {body}")
                    return body
                finally:
                    conn.close()

            def desktop(operation: str, **kwargs):
                return request("/desktop", {"operation": operation, "args": kwargs})["result"]

            def tool(name: str, **kwargs):
                return request("/call", {"tool": name, "args": kwargs})["structuredContent"]

            project = desktop(
                "create_project",
                name="Packaged 40-second smoke",
                target_duration_frames=1200,
                render_profile={
                    "id": "smoke",
                    "width": 180,
                    "height": 320,
                    "frame_rate": {"num": 30, "den": 1},
                },
            )["project_id"]
            asset = desktop(
                "import_file", project_id=project, source_path=str(args.media.resolve()), role="primary"
            )
            if args.transcribe_preset:
                transcription = tool(
                    "transcribe_asset",
                    project_id=project,
                    asset_id=asset["id"],
                    model_preset=args.transcribe_preset,
                )
                deadline = time.monotonic() + 600
                while transcription["status"] in {"queued", "running"} and time.monotonic() < deadline:
                    time.sleep(0.5)
                    transcription = tool("get_job_status", project_id=project, job_id=transcription["id"])
                if transcription["status"] != "succeeded":
                    raise RuntimeError(f"packaged transcription failed: {transcription}")
                report["transcription_job_id"] = transcription["id"]
            timeline = tool("get_timeline", project_id=project)
            timeline["clips"] = [
                {
                    "clip_id": "smoke_cut",
                    "asset_id": asset["id"],
                    "track": "V1",
                    "source_in_frames": 0,
                    "source_out_frames": 1200,
                    "timeline_in_frames": 0,
                }
            ]
            committed = tool("commit_timeline", project_id=project, timeline=timeline, expected_revision=0)
            job = tool("start_render", project_id=project, revision=committed["revision"], final=True)
            deadline = time.monotonic() + 180
            while job["status"] in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.2)
                job = tool("get_job_status", project_id=project, job_id=job["id"])
            if job["status"] != "succeeded":
                raise RuntimeError(f"packaged render failed: {job}")
            qa = tool("run_qa", project_id=project, job_id=job["id"])
            exported = desktop("render_path", project_id=project, job_id=job["id"], require_qa=True)
            if (
                not Path(exported["path"]).is_file()
                or hashlib.sha256(args.media.read_bytes()).hexdigest() != original
            ):
                raise RuntimeError("output missing or source modified")
            # Second revision with a burned-in caption: the render fails if the bundled caption font
            # is missing from the installed resources. The cue is a manual one with no transcript
            # words, so it would (correctly) fail caption-provenance QA; only the render is checked.
            timeline = tool("get_timeline", project_id=project)
            timeline["captions"] = [
                {
                    "cue_id": "smoke_caption",
                    "text": "Packaged caption",
                    "timeline_in_frames": 0,
                    "timeline_out_frames": 60,
                    "source_asset_id": asset["id"],
                    "corrected": True,
                }
            ]
            captioned = tool(
                "commit_timeline",
                project_id=project,
                timeline=timeline,
                expected_revision=committed["revision"],
            )
            caption_job = tool("start_render", project_id=project, revision=captioned["revision"], final=True)
            deadline = time.monotonic() + 180
            while caption_job["status"] in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.2)
                caption_job = tool("get_job_status", project_id=project, job_id=caption_job["id"])
            if caption_job["status"] != "succeeded":
                raise RuntimeError(f"packaged caption render failed: {caption_job}")
            report.update(
                status="passed",
                caption_render_job_id=caption_job["id"],
                project_id=project,
                job_id=job["id"],
                qa=qa,
                output=exported["path"],
                duration_seconds=40,
                width=180,
                height=320,
            )
        finally:
            report["elapsed_seconds"] = round(time.monotonic() - started, 3)
            (output / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            subprocess.run(
                [str(system32 / "taskkill.exe"), "/PID", str(process.pid), "/T", "/F"],
                timeout=15,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            process.wait(timeout=15)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
