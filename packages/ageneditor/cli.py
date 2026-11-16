"""``ageneditor`` CLI: probe media, schemas, fixtures, MCP/service, diagnostics, cleanup and benchmarks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ageneditor.contracts import EngineException, ErrorCode, ModelPreset, export_json_schemas


def _probe(args: argparse.Namespace) -> int:
    from ageneditor.media.hashing import sha256_file
    from ageneditor.media.probe import probe

    for raw in args.files:
        path = Path(raw)
        result = probe(path).to_dict()
        if args.hash:
            result["sha256"] = sha256_file(path)
        print(json.dumps(result, indent=2))
    return 0


def _schema(args: argparse.Namespace) -> int:
    for p in export_json_schemas(Path(args.out)):
        print(p)
    return 0


def _fixtures(args: argparse.Namespace) -> int:
    from ageneditor.devtools.fixtures import generate

    print(generate(Path(args.out), force=args.force))
    return 0


def _mcp(args: argparse.Namespace) -> int:
    from ageneditor.mcp_server import run_stdio

    run_stdio(Path(args.workspace), shared_service=args.shared_service)
    return 0


def _service(args: argparse.Namespace) -> int:
    from ageneditor.desktop_service import DesktopService

    DesktopService(Path(args.workspace)).serve()
    return 0


def _cache_model(args: argparse.Namespace) -> int:
    """Explicitly download a speech model; MCP transcription never initiates a download."""
    from ageneditor.transcription import _import_whisper_model

    try:
        WhisperModel = _import_whisper_model()  # the packaged engine has no PyAV
    except ImportError as exc:
        raise EngineException(
            ErrorCode.TRANSCRIPTION_FAILED, "install with uv sync --extra transcribe"
        ) from exc
    try:
        WhisperModel(args.preset, device="cpu", compute_type="int8", local_files_only=False)
    except Exception as exc:
        raise EngineException(ErrorCode.TRANSCRIPTION_FAILED, "speech model download failed") from exc
    print(json.dumps({"model_preset": args.preset, "status": "cached"}))
    return 0


def _system(args: argparse.Namespace) -> int:
    from ageneditor.system import system_report

    workspace = Path(args.workspace) if args.workspace else None
    print(json.dumps(system_report(workspace, probe_hw=not args.no_hw_probe).to_dict(), indent=2))
    return 0


def _doctor(args: argparse.Namespace) -> int:
    from dataclasses import asdict

    from ageneditor.maintenance import check_project
    from ageneditor.storage import ProjectStore

    checks = check_project(ProjectStore(Path(args.project)), verify_hashes=args.hash)
    print(json.dumps([asdict(check) for check in checks], indent=2))
    return 0 if all(check.ok for check in checks) else 1


def _cleanup(args: argparse.Namespace) -> int:
    from ageneditor.maintenance import cleanup_project
    from ageneditor.storage import ProjectStore

    report = cleanup_project(ProjectStore(Path(args.project)), apply=args.apply)
    print(json.dumps(report.to_dict(), indent=2))
    return 0


def _benchmark(args: argparse.Namespace) -> int:
    from ageneditor.devtools.benchmark import run_benchmark

    preset = ModelPreset(args.transcribe) if args.transcribe else None
    report = run_benchmark(Path(args.manifest), Path(args.out), preset, keep_projects=args.keep_projects)
    print(json.dumps(report["summary"], indent=2))
    return 0 if report["summary"]["completed"] == report["summary"]["records"] else 1


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    if raw[:1] == ["transcribe-worker"]:  # internal: started by TranscriptionManager in the packaged exe
        from ageneditor.transcription import worker_main

        return worker_main(raw[1:])
    parser = argparse.ArgumentParser(prog="ageneditor", description="AgenEditor engine developer tools")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("probe", help="inspect media files with ffprobe")
    p.add_argument("files", nargs="+")
    p.add_argument("--hash", action="store_true", help="also compute SHA-256")
    p.set_defaults(func=_probe)

    s = sub.add_parser("schema", help="export JSON Schemas for contracts v1")
    s.add_argument("--out", default="schemas")
    s.set_defaults(func=_schema)

    f = sub.add_parser("fixtures", help="generate synthetic reference media")
    f.add_argument("--out", default="tests/fixtures/media")
    f.add_argument("--force", action="store_true")
    f.set_defaults(func=_fixtures)

    m = sub.add_parser("mcp", help="start the typed MCP stdio server")
    m.add_argument(
        "--workspace", required=True, help="user-selected directory that contains AgenEditor projects"
    )
    m.add_argument(
        "--shared-service", action="store_true", help="forward tools to the desktop engine service"
    )
    m.set_defaults(func=_mcp)

    service = sub.add_parser("service", help="run the authenticated per-user desktop engine service")
    service.add_argument("--workspace", required=True)
    service.set_defaults(func=_service)

    model = sub.add_parser("cache-model", help="explicitly download a local faster-whisper model")
    model.add_argument("preset", choices=[preset.value for preset in ModelPreset])
    model.set_defaults(func=_cache_model)

    system = sub.add_parser("system", help="report hardware, FFmpeg build/licence and transcription devices")
    system.add_argument("--workspace", help="directory whose free disk space to report")
    system.add_argument("--no-hw-probe", action="store_true", help="skip test encodes on hardware encoders")
    system.set_defaults(func=_system)

    doctor = sub.add_parser("doctor", help="read-only health check of one project directory")
    doctor.add_argument("project")
    doctor.add_argument("--hash", action="store_true", help="re-hash every original (slow on long media)")
    doctor.set_defaults(func=_doctor)

    cleanup = sub.add_parser("cleanup", help="list (or with --apply delete) temporary files left by crashes")
    cleanup.add_argument("project")
    cleanup.add_argument("--apply", action="store_true")
    cleanup.set_defaults(func=_cleanup)

    bench = sub.add_parser("benchmark", help="run the consented evaluation manifest (docs/BENCHMARK.md)")
    bench.add_argument("manifest")
    bench.add_argument("--out", default="benchmark-results")
    bench.add_argument("--transcribe", choices=[preset.value for preset in ModelPreset])
    bench.add_argument("--keep-projects", action="store_true")
    bench.set_defaults(func=_benchmark)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except EngineException as e:
        print(json.dumps({"error": e.error.model_dump(mode="json")}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
