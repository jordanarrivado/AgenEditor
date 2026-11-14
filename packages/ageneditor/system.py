"""Hardware and toolchain capability detection (Phase 5).

Everything here is *measured on this machine* (a probe encode, a library query) or reported as unknown.
Nothing estimates speed. The engine itself stays on CPU defaults (libx264, CPU int8 transcription)
unless the user opts in to an accelerated transcription device, and falls back to CPU on failure.
"""

from __future__ import annotations

import ctypes
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from ageneditor.contracts import EngineException, ErrorCode
from ageneditor.media.tools import find_tool

PROBE_TIMEOUT_S = 20
#: Hardware H.264 encoders worth probing. Detection only: renders use libx264.
HW_ENCODERS = ("h264_nvenc", "h264_qsv", "h264_amf", "h264_mf")
DEVICE_ENV = "AGENEDITOR_TRANSCRIBE_DEVICE"


@dataclass
class FfmpegInfo:
    path: str
    version: str
    gpl: bool
    version3: bool
    nonfree: bool
    libx264: bool
    libass: bool
    usable_hw_encoders: list[str] = field(default_factory=list)

    @property
    def license(self) -> str:
        if self.nonfree:
            return "nonfree (NOT redistributable)"
        if self.gpl:
            return "GPL-3.0-or-later" if self.version3 else "GPL-2.0-or-later"
        return "LGPL-3.0-or-later" if self.version3 else "LGPL-2.1-or-later"


@dataclass
class SystemReport:
    os: str
    python: str
    cpu_logical: int | None
    memory_total_bytes: int | None
    workspace_free_bytes: int | None
    ffmpeg: FfmpegInfo | None
    ffmpeg_error: str | None
    transcribe_installed: bool
    cuda_devices: int | None
    cpu_compute_types: list[str]
    transcription_plan: list[tuple[str, str]]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        if self.ffmpeg:
            data["ffmpeg"]["license"] = self.ffmpeg.license
        return data


def total_memory_bytes() -> int | None:
    if sys.platform == "win32":

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
            return int(status.ullTotalPhys)
        return None
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return None


def _run(args: list[str]) -> subprocess.CompletedProcess[bytes] | None:
    try:
        return subprocess.run(args, capture_output=True, timeout=PROBE_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def ffmpeg_info(probe_hw: bool = True) -> FfmpegInfo:
    ffmpeg = str(find_tool("ffmpeg"))
    result = _run([ffmpeg, "-hide_banner", "-version"])
    if result is None or result.returncode != 0:
        raise EngineException(ErrorCode.PROBE_FAILED, "ffmpeg -version failed")
    text = result.stdout.decode("utf-8", "replace")
    first = text.splitlines()[0] if text else ""
    match = re.search(r"ffmpeg version (\S+)", first)
    config = next((line for line in text.splitlines() if line.startswith("configuration:")), "")
    info = FfmpegInfo(
        path=ffmpeg,
        version=match.group(1) if match else "unknown",
        gpl="--enable-gpl" in config,
        version3="--enable-version3" in config,
        nonfree="--enable-nonfree" in config,
        libx264="--enable-libx264" in config,
        libass="--enable-libass" in config,
    )
    if probe_hw:
        info.usable_hw_encoders = [name for name in HW_ENCODERS if _encoder_works(ffmpeg, name)]
    return info


def _encoder_works(ffmpeg: str, encoder: str) -> bool:
    """A compiled-in encoder is only 'usable' if a tiny real encode succeeds on this machine."""
    result = _run(
        [ffmpeg, "-hide_banner", "-v", "error", "-nostdin", "-f", "lavfi", "-i",
         "color=c=black:s=256x256:r=30:d=0.2", "-c:v", encoder, "-f", "null", "-"]
    )  # fmt: skip
    return result is not None and result.returncode == 0


def transcription_plan(cuda_devices: int | None, setting: str | None = None) -> list[tuple[str, str]]:
    """Ordered (device, compute_type) attempts. CPU int8 is always the final fallback.

    ``AGENEDITOR_TRANSCRIBE_DEVICE``: ``cpu`` (default), ``auto`` (CUDA when a device is visible), or
    ``cuda`` (try CUDA first even if detection failed). The packaged Windows engine is CPU-only (it
    does not ship NVIDIA cuDNN), so it always plans CPU.
    """
    setting = (setting or os.environ.get(DEVICE_ENV, "cpu")).strip().lower()
    if setting not in {"cpu", "auto", "cuda"}:
        raise EngineException(ErrorCode.INVALID_ARGUMENT, f"{DEVICE_ENV} must be cpu, auto or cuda")
    if cpu_only_build():
        return [("cpu", "int8")]
    plan: list[tuple[str, str]] = []
    if setting == "cuda" or (setting == "auto" and cuda_devices):
        plan.append(("cuda", "float16"))
    plan.append(("cpu", "int8"))
    return plan


def cpu_only_build() -> bool:
    """True in the packaged engine, which leaves out NVIDIA cuDNN (see docs/LICENSE_REPORT.md)."""
    return bool(getattr(sys, "frozen", False))


def cuda_device_count() -> int | None:
    try:
        import ctranslate2
    except ImportError:
        return None
    try:
        return int(ctranslate2.get_cuda_device_count())
    except Exception:  # a broken CUDA install must never stop CPU work
        return 0


def system_report(workspace: Path | None = None, probe_hw: bool = True) -> SystemReport:
    notes: list[str] = []
    ffmpeg, ffmpeg_error = None, None
    try:
        ffmpeg = ffmpeg_info(probe_hw)
    except EngineException as exc:
        ffmpeg_error = exc.error.message
    free = None
    if workspace is not None:
        try:
            free = shutil.disk_usage(workspace).free
        except OSError:
            notes.append("workspace free space could not be read")
    try:
        import faster_whisper  # noqa: F401

        transcribe = True
    except ImportError:
        transcribe = False
    cuda = cuda_device_count() if transcribe else None
    cpu_types: list[str] = []
    if transcribe:
        try:
            import ctranslate2

            cpu_types = sorted(ctranslate2.get_supported_compute_types("cpu"))
        except Exception:
            notes.append("ctranslate2 CPU compute types unavailable")
    if ffmpeg and ffmpeg.nonfree:
        notes.append("this FFmpeg build is nonfree and must not be redistributed")
    if ffmpeg and not ffmpeg.libass:
        notes.append("FFmpeg lacks libass: captions cannot be burned in")
    if ffmpeg and ffmpeg.usable_hw_encoders:
        notes.append("hardware encoders detected but not used; renders use libx264 for reproducible output")
    if cpu_only_build():
        notes.append("this installed engine is CPU-only; GPU transcription is not included")
    elif cuda:
        notes.append(f"CUDA visible; transcription uses it only with {DEVICE_ENV}=auto or cuda")
    try:
        plan = transcription_plan(cuda)
    except EngineException as exc:
        plan = [("cpu", "int8")]
        notes.append(exc.error.message)
    return SystemReport(
        os=f"{platform.system()} {platform.release()} ({platform.machine()})",
        python=platform.python_version(),
        cpu_logical=os.cpu_count(),
        memory_total_bytes=total_memory_bytes(),
        workspace_free_bytes=free,
        ffmpeg=ffmpeg,
        ffmpeg_error=ffmpeg_error,
        transcribe_installed=transcribe,
        cuda_devices=cuda,
        cpu_compute_types=cpu_types,
        transcription_plan=plan,
        notes=notes,
    )


def package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "unknown"
