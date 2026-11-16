"""Phase 5 hardening: path sandbox audit, disk preflight, crash cleanup, diagnostics, device fallback,
capability report, benchmark harness and loopback-service authentication."""

from __future__ import annotations

import json
import shutil
import sys
import threading
import time
from http.client import HTTPConnection
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter, ValidationError

from ageneditor.cli import main as cli_main
from ageneditor.contracts import (
    AssetRole,
    Clip,
    EngineException,
    ErrorCode,
    Job,
    JobKind,
    JobStatus,
    ModelPreset,
    RenderProfile,
    TrackId,
)
from ageneditor.contracts.common import ProjectRelativePath
from ageneditor.devtools.benchmark import run_benchmark, word_error_rate
from ageneditor.maintenance import (
    check_project,
    cleanup_project,
    ensure_free_space,
    estimate_render_bytes,
)
from ageneditor.media.render import render_timeline
from ageneditor.storage import ProjectStore, _safe_filename
from ageneditor.system import system_report, transcription_plan
from ageneditor.timebase import FrameRate
from ageneditor.transcription import transcribe_local

PATH = TypeAdapter(ProjectRelativePath)
PROFILE = RenderProfile(id="p5", width=180, height=320, frame_rate=FrameRate(num=30))


# ---------------------------------------------------------------- path sandbox audit


@pytest.mark.parametrize(
    "value",
    [
        "imports/CON",
        "imports/con.mp4",
        "imports/nul",
        "imports/COM1.txt",
        "imports/lpt9",
        "imports/CONIN$",
        "imports/clip.mp4:stream",
        "imports/clip.mp4::$DATA",
        "C:x",
        "imports/clip.mp4.",
        "imports/clip.mp4 ",
        "imports/a\x01b",
        "../x",
        "imports/../../x",
        "\\\\server\\share",
        "/etc/passwd",
    ],
)
def test_project_paths_reject_windows_specials_and_escapes(value: str) -> None:
    with pytest.raises(ValidationError):
        PATH.validate_python(value)


@pytest.mark.parametrize(
    "value", ["imports/my clip (1).mp4", "originals/asset_x_console.mp4", "renders/j/con1"]
)
def test_project_paths_allow_ordinary_names(value: str) -> None:
    assert PATH.validate_python(value) == value


def test_imported_file_names_are_sanitised() -> None:
    assert _safe_filename("CON") == "CON"  # prefixed with the asset id, so never a bare device name
    assert _safe_filename("a:b\\c‮gnp.mp4") == "c_gnp.mp4"
    assert _safe_filename("clip.mp4. ") == "clip.mp4"
    assert _safe_filename("...") == "media"


def test_symlinked_project_directory_is_refused(tmp_path: Path) -> None:
    store = ProjectStore.create(tmp_path, "links")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = store.root / "renders" / "escape"
    link.parent.mkdir(exist_ok=True)
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("creating symlinks needs Developer Mode or admin rights on this machine")
    with pytest.raises(EngineException) as info:
        store.safe_path("renders/escape/file.txt")
    assert info.value.error.code is ErrorCode.PERMISSION_DENIED


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS junctions are Windows-only")
def test_junction_escape_is_refused_and_cleanup_does_not_follow_it(tmp_path: Path) -> None:
    """Junctions need no admin rights, so they are the realistic escape on Windows."""
    import _winapi

    store = ProjectStore.create(tmp_path, "junction")
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / ".final.1.tmp.mp4"
    victim.write_bytes(b"not ours")
    (store.root / "renders").mkdir(exist_ok=True)
    _winapi.CreateJunction(str(outside), str(store.root / "renders" / "job_x"))
    with pytest.raises(EngineException) as info:
        store.safe_path("renders/job_x/.final.1.tmp.mp4")
    assert info.value.error.code is ErrorCode.PERMISSION_DENIED
    cleanup_project(store, apply=True)
    assert victim.read_bytes() == b"not ours"  # cleanup never deletes through a junction


# ---------------------------------------------------------------- disk preflight


def test_disk_preflight_refuses_before_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ageneditor.maintenance.free_bytes", lambda _path: 10 * 1024 * 1024)
    with pytest.raises(EngineException) as info:
        ensure_free_space(tmp_path, 1024, "a test")
    assert info.value.error.code is ErrorCode.INSUFFICIENT_DISK
    assert info.value.error.details["free_bytes"] == 10 * 1024 * 1024


@pytest.mark.media
def test_render_and_import_preflight(
    tmp_path: Path, media_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ProjectStore.create(tmp_path, "disk")
    asset = store.import_asset(media_dir / "talking_head_16x9_30fps_60s.mp4", AssetRole.PRIMARY)
    store.create_timeline(PROFILE, 30)
    clip = Clip(clip_id="c", asset_id=asset.id, track=TrackId.V1, source_in_frames=0, source_out_frames=30,
                timeline_in_frames=0)  # fmt: skip
    timeline = store.commit_timeline(store.timeline(0).model_copy(update={"clips": [clip]}), 0)
    assert 0 < estimate_render_bytes(timeline) < 10 * 1024 * 1024
    monkeypatch.setattr("ageneditor.maintenance.free_bytes", lambda _path: 1024)
    output = store.safe_path("renders/job_disk/final.mp4")
    with pytest.raises(EngineException) as info:
        render_timeline(store, timeline, output)
    assert info.value.error.code is ErrorCode.INSUFFICIENT_DISK
    assert not output.exists()
    log = (output.parent / "render.log").read_text(encoding="utf-8")
    assert "INSUFFICIENT_DISK" in log and "not started" in log
    with pytest.raises(EngineException) as imported:
        store.import_asset(media_dir / "broll_9x16_10s.mp4", AssetRole.BROLL)
    assert imported.value.error.code is ErrorCode.INSUFFICIENT_DISK
    assert len(store.assets()) == 1


# ---------------------------------------------------------------- crash cleanup + doctor


@pytest.mark.media
def test_cleanup_removes_only_engine_temporaries(tmp_path: Path, media_dir: Path) -> None:
    store = ProjectStore.create(tmp_path, "cleanup")
    store.import_asset(media_dir / "broll_9x16_10s.mp4", AssetRole.BROLL)
    store.create_timeline(PROFILE, 30)
    leftovers = {
        "renders/job_dead/.final.3.tmp.mp4": b"x" * 100,
        "renders/job_dead/.captions.r3.ass": b"ass",
        "renders/job_dead/.captionfonts.r3/Poppins-Bold.ttf": b"ttf",
        "transcripts/transcript_x.pending.json": b"{}",
        "timelines/r9.json.tmp": b"{}",
    }
    kept = {
        "renders/job_unknown/final.mp4": b"render",
        "originals/stray_copy.mp4": b"partial",
        "renders/job_live/.final.1.tmp.mp4": b"in progress",
    }
    for relative, data in {**leftovers, **kept}.items():
        path = store.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    store.start_job(Job(id="job_live", project_id=store.project_id, kind=JobKind.FINAL_RENDER,
                        timeline_revision=0, status=JobStatus.RUNNING))  # fmt: skip
    dry = cleanup_project(store)
    assert dry.dry_run and {item.path for item in dry.removed} == set(leftovers)
    assert all((store.root / relative).exists() for relative in leftovers)
    review = {item.path for item in dry.kept_for_review}
    assert {"renders/job_unknown/final.mp4", "originals/stray_copy.mp4"} <= review
    health = {check.check: check for check in check_project(store)}
    assert not health["leftovers"].ok and health["database"].ok and health["timeline_history"].ok

    applied = cleanup_project(store, apply=True)
    assert applied.reclaimed_bytes == sum(len(data) for data in leftovers.values())
    assert not any((store.root / relative).exists() for relative in leftovers)
    assert not (store.root / "renders/job_dead/.captionfonts.r3").exists()
    assert all((store.root / relative).exists() for relative in kept)  # live job + review items untouched
    assert all(check.ok for name, check in {c.check: c for c in check_project(store)}.items()
               if name.startswith("asset:"))  # fmt: skip


@pytest.mark.media
def test_doctor_detects_tampered_original(tmp_path: Path, media_dir: Path) -> None:
    store = ProjectStore.create(tmp_path, "doctor")
    asset = store.import_asset(media_dir / "broll_9x16_10s.mp4", AssetRole.BROLL)
    store.create_timeline(PROFILE, 30)
    path = store.safe_path(asset.relative_path)
    path.chmod(0o666)
    data = bytearray(path.read_bytes())
    data[-1] ^= 0xFF
    path.write_bytes(bytes(data))
    checks = {c.check: c for c in check_project(store, verify_hashes=True)}
    assert not checks[f"asset:{asset.id}"].ok
    assert (
        "writable" in checks[f"asset:{asset.id}"].detail and "SHA-256" in checks[f"asset:{asset.id}"].detail
    )
    assert cli_main(["doctor", str(store.root), "--hash"]) == 1


# ---------------------------------------------------------------- render diagnostics


@pytest.mark.media
def test_render_log_is_written_and_redacted(tmp_path: Path, media_dir: Path) -> None:
    store = ProjectStore.create(tmp_path, "logs")
    asset = store.import_asset(media_dir / "talking_head_16x9_30fps_60s.mp4", AssetRole.PRIMARY)
    store.create_timeline(PROFILE, 15)
    clip = Clip(clip_id="c", asset_id=asset.id, track=TrackId.V1, source_in_frames=0, source_out_frames=15,
                timeline_in_frames=0)  # fmt: skip
    timeline = store.commit_timeline(store.timeline(0).model_copy(update={"clips": [clip]}), 0)
    output = store.safe_path("renders/job_log/final.mp4")
    render_timeline(store, timeline, output)
    log = (output.parent / "render.log").read_text(encoding="utf-8")
    assert "outcome: succeeded" in log and "exit_code: 0" in log and "-filter_complex" in log
    assert str(store.root) not in log and "<project>/originals/" in log.replace("\\", "/")


# ---------------------------------------------------------------- hardware + device fallback


def test_transcription_plan_defaults_to_cpu_and_always_ends_on_cpu() -> None:
    assert transcription_plan(cuda_devices=2, setting="cpu") == [("cpu", "int8")]
    assert transcription_plan(cuda_devices=0, setting="auto") == [("cpu", "int8")]
    assert transcription_plan(cuda_devices=1, setting="auto") == [("cuda", "float16"), ("cpu", "int8")]
    assert transcription_plan(cuda_devices=None, setting="cuda")[-1] == ("cpu", "int8")
    with pytest.raises(EngineException):
        transcription_plan(1, setting="tpu")


@pytest.mark.media
def test_transcription_falls_back_to_cpu_when_gpu_decode_fails(
    tmp_path: Path, media_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ProjectStore.create(tmp_path, "fallback")
    asset = store.import_asset(media_dir / "talking_head_16x9_30fps_60s.mp4", AssetRole.PRIMARY)
    attempts: list[str] = []

    class FakeModel:
        def __init__(self, _name: str, device: str, compute_type: str, local_files_only: bool) -> None:
            assert local_files_only
            self.device = f"{device}/{compute_type}"
            attempts.append(self.device)

        def transcribe(self, _path: str, **_options: object):
            info = SimpleNamespace(duration=60.0, language="en", language_probability=0.9)

            def segments():
                if self.device.startswith("cuda"):
                    raise RuntimeError("Library cublas64_12.dll is not found")  # how it fails in practice
                yield SimpleNamespace(
                    words=[SimpleNamespace(word=" hi.", start=1.0, end=1.4, probability=0.9)],
                    text="hi.",
                    end=1.4,
                )

            return segments(), info

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr("ageneditor.system.cuda_device_count", lambda: 1)
    monkeypatch.setenv("AGENEDITOR_TRANSCRIBE_DEVICE", "auto")
    transcript = transcribe_local(store, asset.id, ModelPreset.BASE, "transcript_fallback")
    assert attempts == ["cuda/float16", "cpu/int8"]
    assert transcript.compute == "cpu/int8" and [w.text for w in transcript.words] == ["hi."]


@pytest.mark.media
def test_system_report_is_measured(tmp_path: Path, media_dir: Path) -> None:
    report = system_report(tmp_path, probe_hw=False).to_dict()
    assert report["ffmpeg"]["version"] and report["ffmpeg"]["license"]
    assert report["workspace_free_bytes"] > 0 and report["cpu_logical"] >= 1
    assert report["transcription_plan"][-1] == ("cpu", "int8")
    json.dumps(report)  # serialisable for the CLI


# ---------------------------------------------------------------- benchmark harness


def test_word_error_rate() -> None:
    assert word_error_rate("We brewed coffee.", "we brewed coffee") == 0
    assert word_error_rate("a b c d", "a x c") == 0.5
    assert word_error_rate("", "") == 0.0


@pytest.mark.media
def test_benchmark_runs_reference_edit_and_compares_flags(tmp_path: Path, media_dir: Path) -> None:
    media = tmp_path / "dataset" / "rec.mp4"
    media.parent.mkdir()
    shutil.copyfile(media_dir / "talking_head_16x9_30fps_60s.mp4", media)
    manifest = tmp_path / "dataset" / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "name": "synthetic smoke",
                "records": [
                    {
                        "id": "rec01",
                        "media": "rec.mp4",
                        "consent": True,
                        "accepted_ranges": [{"start_s": "1", "end_s": "3"}, {"start_s": "10", "end_s": "12"}],
                        "known_defects": [{"check_id": "jump_cuts"}, {"check_id": "hook_timing"}],
                    },
                    {
                        "id": "rec02",
                        "media": "rec.mp4",
                        "consent": False,
                        "accepted_ranges": [{"start_s": "0", "end_s": "1"}],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    report = run_benchmark(manifest, tmp_path / "out")
    first, second = report["records"]
    assert first["status"] == "ok" and second["status"] == "skipped"
    assert first["render_realtime_factor"] > 0
    assert "jump_cuts" in first["qa"]["flagged"]
    assert first["qa"]["annotated_not_flagged"] == ["hook_timing"]  # no captions → no hook check ran
    assert first["qa"]["technical_failures"] == []
    assert (tmp_path / "out" / "benchmark.md").is_file()
    assert not (tmp_path / "out" / "projects").exists()


# ---------------------------------------------------------------- loopback service auth


def test_service_auth_binding_and_malformed_requests(tmp_path: Path, private_service_home: Path) -> None:
    from ageneditor.desktop_service import DesktopService, service_address

    service = DesktopService(tmp_path)
    assert service.http.server_address[0] == "127.0.0.1"
    thread = threading.Thread(target=service.serve, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while service_address(tmp_path) is None and time.monotonic() < deadline:
            time.sleep(0.05)

        def request(method: str, path: str, headers: dict, body: bytes | None = None) -> int:
            connection = HTTPConnection("127.0.0.1", service.port, timeout=5)
            try:
                connection.request(method, path, body=body, headers=headers)
                return connection.getresponse().status
            except OSError:
                return -1
            finally:
                connection.close()

        good = {"Authorization": f"Bearer {service.token}"}
        assert request("GET", "/health", {"Authorization": "Bearer " + "0" * 64}) == 403
        assert request("GET", "/health", {"Authorization": service.token}) == 403  # scheme required
        assert request("POST", "/desktop", {**good, "Content-Length": str(10**9)}, b"{}") in {413, -1}
        assert request("POST", "/desktop", {**good, "Content-Length": "abc"}, b"{}") == 400
        assert request("GET", "/health", {**good, "Host": "attacker.example"}) == 403
        assert request("GET", "/health", {**good, "Origin": "http://attacker.example"}) == 403
        assert request("GET", "/health", good) == 200  # still serving after malformed input
    finally:
        service.http.shutdown()
        thread.join(timeout=5)


def test_caption_font_is_bundled_and_ofl_licensed():
    from ageneditor.media.tools import find_fonts_dir

    fonts = find_fonts_dir()
    assert {p.name for p in fonts.glob("*.ttf")} >= {"Poppins-Regular.ttf", "Poppins-Bold.ttf"}
    assert "SIL Open Font License" in (fonts / "OFL.txt").read_text(encoding="utf-8")


@pytest.mark.media
def test_ffmpeg_audio_decode_matches_pyav(media_dir: Path) -> None:
    """The FFmpeg decoder that replaces PyAV feeds Whisper the same samples, aligned and at the same level."""
    np = pytest.importorskip("numpy")
    pytest.importorskip("av")
    from faster_whisper.audio import decode_audio

    from ageneditor.transcription import decode_whisper_audio

    source = media_dir / "talking_head_16x9_30fps_60s.mp4"
    ours = decode_whisper_audio(source, stream_index=1, channels=2)
    reference = decode_audio(str(source))
    assert ours.dtype == np.float32 and len(ours) == 60 * 16_000  # exact duration; PyAV adds AAC tail padding
    assert 0 <= len(reference) - len(ours) < 16_000 // 10
    assert float(np.abs(ours - reference[: len(ours)]).max()) < 0.005


@pytest.mark.media
def test_transcription_runs_without_pyav(
    tmp_path: Path, media_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The packaged engine excludes PyAV (GPL x264/x265 DLLs); a real model must still transcribe."""
    pytest.importorskip("faster_whisper")
    monkeypatch.setitem(sys.modules, "av", None)  # makes `import av` raise ImportError
    for name in [m for m in sys.modules if m == "faster_whisper" or m.startswith("faster_whisper.")]:
        monkeypatch.delitem(sys.modules, name)
    store = ProjectStore.create(tmp_path, "no-pyav")
    asset = store.import_asset(media_dir / "talking_head_16x9_30fps_60s.mp4", AssetRole.PRIMARY)
    monkeypatch.setenv("AGENEDITOR_TRANSCRIBE_DEVICE", "cpu")
    try:
        transcript = transcribe_local(store, asset.id, ModelPreset.TINY, "transcript_no_pyav")
    except EngineException as exc:
        if "model is unavailable" in exc.error.message:
            pytest.skip("faster-whisper tiny model is not cached on this machine")
        raise
    assert transcript.compute == "cpu/int8" and transcript.duration_us == 60_000_000
    assert not hasattr(sys.modules["av"], "open")  # the placeholder, not PyAV, satisfied the import


def test_cache_model_and_capability_report_work_without_pyav(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The "Install speech model" button runs `cache-model` in the packaged engine, which has no PyAV."""
    pytest.importorskip("faster_whisper")
    from ageneditor.system import system_report
    from ageneditor.transcription import _import_whisper_model

    monkeypatch.setitem(sys.modules, "av", None)  # makes `import av` raise ImportError
    for name in [m for m in sys.modules if m == "faster_whisper" or m.startswith("faster_whisper.")]:
        monkeypatch.delitem(sys.modules, name)
    assert system_report(None, probe_hw=False).transcribe_installed
    loaded: list[tuple[str, bool]] = []

    class FakeModel:  # stands in for the download; the import above it is the real one
        def __init__(self, name: str, device: str, compute_type: str, local_files_only: bool) -> None:
            loaded.append((name, local_files_only))

    _import_whisper_model()
    monkeypatch.setattr(sys.modules["faster_whisper"], "WhisperModel", FakeModel)
    assert cli_main(["cache-model", "tiny"]) == 0
    assert loaded == [("tiny", False)]
    assert json.loads(capsys.readouterr().out) == {"model_preset": "tiny", "status": "cached"}


def test_packaged_engine_starts_transcription_worker_through_its_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """A frozen exe has no `python -m`; the worker must go through the hidden CLI command."""
    from ageneditor import cli, transcription

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\AgenEditor\ageneditor-engine.exe")
    assert transcription.worker_command() == [r"C:\AgenEditor\ageneditor-engine.exe", "transcribe-worker"]
    received: list[list[str]] = []
    monkeypatch.setattr(transcription, "worker_main", lambda argv: received.append(argv) or 0)
    assert cli.main(["transcribe-worker", "--project", "p"]) == 0
    assert received == [["--project", "p"]]


def test_packaged_engine_plans_cpu_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """The installer leaves out NVIDIA cuDNN, so the packaged engine never attempts CUDA."""
    from ageneditor.system import transcription_plan

    assert transcription_plan(1, "cuda")[0] == ("cuda", "float16")  # development install
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert transcription_plan(1, "cuda") == [("cpu", "int8")]
    assert transcription_plan(1, "auto") == [("cpu", "int8")]
