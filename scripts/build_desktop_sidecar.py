"""Build the Windows engine sidecar and copy local FFmpeg tools into Tauri resources."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from collect_notices import collect
from license_report import EXCLUDED_BINARIES, EXCLUDED_FROM_BUNDLE

from ageneditor.media.tools import find_tool

ROOT = Path(__file__).resolve().parents[1]
BINARIES = ROOT / "apps" / "desktop" / "src-tauri" / "binaries"
WORK = ROOT / ".tools" / "pyinstaller"
TARGET = "x86_64-pc-windows-msvc"
#: Output of scripts/build_ffmpeg.py: the pinned, minimal FFmpeg the installer is meant to ship.
PINNED_FFMPEG = ROOT / ".tools" / "ffmpeg" / "bin"


def main() -> int:
    if sys.platform != "win32":
        raise SystemExit("The Phase Four alpha build currently targets Windows x64")
    pyinstaller = Path(sys.executable).with_name("pyinstaller.exe")
    if not pyinstaller.is_file():
        raise SystemExit("Install the desktop dependency group: uv sync --extra transcribe --group desktop")
    BINARIES.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    if (PINNED_FFMPEG / "ffmpeg.exe").is_file() and (PINNED_FFMPEG / "ffprobe.exe").is_file():
        os.environ["AGENEDITOR_FFMPEG"] = str(PINNED_FFMPEG / "ffmpeg.exe")
        os.environ["AGENEDITOR_FFPROBE"] = str(PINNED_FFMPEG / "ffprobe.exe")
    elif os.environ.get("AGENEDITOR_REQUIRE_PINNED_FFMPEG") == "1":
        raise SystemExit("pinned FFmpeg missing: run scripts/build_ffmpeg.py first")
    else:
        print("WARNING: bundling the FFmpeg found on PATH, not the pinned build (scripts/build_ffmpeg.py)")
    collect(ROOT / "apps/desktop/src-tauri/resources/third-party")
    for name in ("ffmpeg", "ffprobe"):
        source = find_tool(name)
        destination = BINARIES / f"{name}.exe"
        if source.resolve() != destination.resolve():
            shutil.copy2(source, destination)
    # Generate the spec, then drop excluded native libraries (PyInstaller's CLI cannot exclude a DLL).
    makespec = [
        str(pyinstaller.with_name("pyi-makespec.exe")),
        "--onefile",
        "--name",
        "ageneditor-engine",
        "--specpath",
        str(WORK),
        "--collect-all",
        "mcp_types",
        "--collect-all",
        "faster_whisper",
        "--collect-all",
        "ctranslate2",
        *[arg for module in sorted(EXCLUDED_FROM_BUNDLE) for arg in ("--exclude-module", module)],
        str(ROOT / "scripts" / "desktop_sidecar.py"),
    ]
    subprocess.run(makespec, check=True, timeout=300, cwd=ROOT)
    spec = WORK / "ageneditor-engine.spec"
    text = spec.read_text(encoding="utf-8")
    anchor = "pyz = PYZ("
    if anchor not in text:
        raise SystemExit("unexpected PyInstaller spec layout; cannot exclude binaries")
    excluded = sorted(EXCLUDED_BINARIES)
    text = text.replace(
        anchor,
        f"_excluded = {excluded!r}\n"
        "a.binaries = [b for b in a.binaries if Path(b[0]).name.lower() not in _excluded]\n"
        f"{anchor}",
        1,
    )
    spec.write_text("from pathlib import Path\n" + text, encoding="utf-8")
    build = [
        str(pyinstaller),
        "--noconfirm",
        "--clean",
        "--distpath",
        str(WORK / "dist"),
        "--workpath",
        str(WORK / "build"),
        str(spec),
    ]
    subprocess.run(build, check=True, timeout=900, cwd=ROOT)
    source = WORK / "dist" / "ageneditor-engine.exe"
    target = BINARIES / f"ageneditor-engine-{TARGET}.exe"
    shutil.copy2(source, target)
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
