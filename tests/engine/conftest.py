"""Shared fixtures. Media fixtures are generated once (cached in tests/fixtures/media)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ageneditor.media.tools import tool_available

MEDIA_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "media"


@pytest.fixture
def private_service_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "user-data"
    home.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home))
    return home


@pytest.fixture(scope="session")
def media_dir() -> Path:
    if not (tool_available("ffmpeg") and tool_available("ffprobe")):
        pytest.skip("ffmpeg/ffprobe not available")
    from ageneditor.devtools.fixtures import generate

    generate(MEDIA_DIR)
    return MEDIA_DIR


@pytest.fixture(scope="session")
def manifest(media_dir: Path) -> dict:
    return json.loads((media_dir / "manifest.json").read_text(encoding="utf-8"))
