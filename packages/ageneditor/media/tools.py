"""Locate FFmpeg/FFprobe. Override with AGENEDITOR_FFMPEG / AGENEDITOR_FFPROBE."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from ageneditor.contracts import EngineException, ErrorCode


def find_tool(name: str) -> Path:
    override = os.environ.get(f"AGENEDITOR_{name.upper()}")
    if override:
        p = Path(override)
        if p.is_file():
            return p
        raise EngineException(ErrorCode.PROBE_FAILED, f"AGENEDITOR_{name.upper()} points to a missing file")
    if getattr(sys, "frozen", False):
        bundled = Path(sys.executable).parent / "binaries" / f"{name}.exe"
        if bundled.is_file():
            return bundled
        raise EngineException(ErrorCode.PROBE_FAILED, f"bundled {name} is missing")
    found = shutil.which(name)
    if not found:
        raise EngineException(ErrorCode.PROBE_FAILED, f"{name} not found on PATH")
    return Path(found)


def find_fonts_dir() -> Path:
    """Directory holding the bundled caption fonts (Poppins, SIL OFL 1.1)."""
    if getattr(sys, "frozen", False):
        bundled = Path(sys.executable).parent / "resources" / "fonts"
    else:
        bundled = Path(__file__).resolve().parents[3] / "apps/desktop/src-tauri/resources/fonts"
    if not any(bundled.glob("*.ttf")):
        raise EngineException(ErrorCode.PROBE_FAILED, "bundled caption fonts are missing")
    return bundled


def tool_available(name: str) -> bool:
    try:
        find_tool(name)
    except EngineException:
        return False
    return True
