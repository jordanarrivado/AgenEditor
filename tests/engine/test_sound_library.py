"""Sound library: install packs from a local folder, gate on active, place overlapping A3 effects."""

from __future__ import annotations

import re
import shutil
import subprocess
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from ageneditor import sound_catalog, sound_library
from ageneditor.contracts import (
    AssetOrigin,
    AssetRole,
    AudioMix,
    Clip,
    EngineException,
    ErrorCode,
    RenderProfile,
    SoundCatalogEntry,
    SoundLicense,
    TrackId,
)
from ageneditor.media.hashing import sha256_file
from ageneditor.media.render import render_timeline
from ageneditor.media.tools import find_tool
from ageneditor.sound_library import LIBRARY_DIR, SoundLibraryStore
from ageneditor.storage import ProjectStore
from ageneditor.timebase import FrameRate


def _tone(path: Path, seconds: float, frequency: int = 1000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(find_tool("ffmpeg")), "-v", "error", "-y", "-f", "lavfi",
            "-i", f"sine=frequency={frequency}:sample_rate=48000:duration={seconds}",
            "-ac", "2", str(path),
        ],
        check=True,
        timeout=60,
    )  # fmt: skip
    return path


@pytest.fixture
def pack_folder(tmp_path: Path, media_dir: Path) -> Path:
    folder = tmp_path / "downloaded pack"
    _tone(folder / "Whooshes" / "whoosh_fast-01.wav", 0.5, 800)
    _tone(folder / "pop soft.wav", 0.3, 1500)
    shutil.copyfile(media_dir / "broll_9x16_10s.mp4", folder / "not audio.mp4")
    (folder / "readme.txt").write_text("CC0 pack", encoding="utf-8")
    return folder


def test_install_copies_audio_only_and_reports_skips(tmp_path: Path, pack_folder: Path) -> None:
    library = SoundLibraryStore(tmp_path / "workspace")
    pack, skipped = library.install_pack(pack_folder, "Kenney UI", SoundLicense.CC0, source_note="kenney.nl")
    assert sorted((s.name, s.category) for s in pack.sounds) == [
        ("pop soft", ""),
        ("whoosh fast 01", "Whooshes"),
    ]
    assert all(0 < s.duration_ms <= 600 for s in pack.sounds)
    # .mp4 is not an audio extension, and readme.txt is ignored: nothing reported for them.
    assert skipped == []
    for sound in pack.sounds:
        copied = library.root / "packs" / pack.id / sound.file
        assert sha256_file(copied) == sound.sha256
    # The originals the person picked are untouched and still there.
    assert (pack_folder / "pop soft.wav").is_file()
    # The library folder is not a project.
    assert not list((tmp_path / "workspace").glob("*/project.sqlite3"))
    assert (tmp_path / "workspace" / LIBRARY_DIR / "library.json").is_file()


def test_long_or_video_files_are_skipped_with_a_reason(tmp_path: Path, media_dir: Path) -> None:
    folder = tmp_path / "mixed"
    _tone(folder / "hit.wav", 0.2)
    shutil.copyfile(media_dir / "music_bed_30s.m4a", folder / "bed.m4a")  # 30 s is fine
    _tone(folder / "drone.wav", 61)
    shutil.copyfile(media_dir / "broll_9x16_10s.mp4", folder / "clip.m4a")  # video hiding as .m4a
    pack, skipped = SoundLibraryStore(tmp_path / "ws").install_pack(folder, "Mixed", SoundLicense.OTHER)
    assert sorted(s.original_filename for s in pack.sounds) == ["bed.m4a", "hit.wav"]
    reasons = {item["file"]: item["reason"] for item in skipped}
    assert "longer than 60 seconds" in reasons["drone.wav"]
    assert reasons["clip.m4a"] == "needs to be audio only"


def test_install_rejects_bad_details_and_empty_folders(tmp_path: Path, pack_folder: Path) -> None:
    library = SoundLibraryStore(tmp_path / "ws")
    with pytest.raises(EngineException) as missing_credit:
        library.install_pack(pack_folder, "Freesound picks", SoundLicense.CC_BY)
    assert missing_credit.value.error.code is ErrorCode.INVALID_ARGUMENT
    assert "credit" in missing_credit.value.error.message
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(EngineException) as nothing:
        library.install_pack(empty, "Empty", SoundLicense.CC0)
    assert nothing.value.error.code is ErrorCode.ASSET_NOT_FOUND
    with pytest.raises(EngineException):
        library.install_pack(Path("relative/folder"), "Rel", SoundLicense.CC0)
    assert library.load().packs == []
    assert not list(library.root.glob("packs/*"))  # nothing left half-installed


def test_only_active_packs_are_searchable_or_usable(tmp_path: Path, pack_folder: Path) -> None:
    workspace = tmp_path / "ws"
    library = SoundLibraryStore(workspace)
    pack, _ = library.install_pack(
        pack_folder, "Freesound picks", SoundLicense.CC_BY, credit="Sounds by alice (CC BY 4.0)"
    )
    found = library.search("whoosh")
    assert found["active_packs"] == 1
    assert [s["name"] for s in found["sounds"]] == ["whoosh fast 01"]
    assert found["sounds"][0]["credit"] == "Sounds by alice (CC BY 4.0)"
    assert library.search("whooshes fast")["total_matches"] == 1  # folder names count
    assert library.search("explosion")["sounds"] == []

    store = ProjectStore.create(workspace, "sfx project")
    whoosh_id = found["sounds"][0]["sound_id"]
    library.set_active(pack.id, False)
    assert library.search("whoosh") == {
        "active_packs": 0,
        "installed_packs": 1,
        "total_matches": 0,
        "sounds": [],
    }
    with pytest.raises(EngineException) as inactive:
        library.add_to_project(store, whoosh_id)
    assert inactive.value.error.code is ErrorCode.PERMISSION_DENIED
    assert store.assets() == {}

    library.set_active(pack.id, True)
    asset = library.add_to_project(store, whoosh_id)
    assert asset.role is AssetRole.SFX
    assert asset.provenance.origin is AssetOrigin.SOUND_LIBRARY
    assert asset.provenance.library and asset.provenance.library.credit == "Sounds by alice (CC BY 4.0)"
    assert library.add_to_project(store, whoosh_id).id == asset.id  # added once
    with pytest.raises(EngineException):
        library.add_to_project(store, "sound_missing")

    # Uninstalling keeps the copy the project already registered.
    library.remove_pack(pack.id)
    assert library.load().packs == []
    assert not (library.root / "packs" / pack.id).exists()
    assert sha256_file(store.safe_path(asset.relative_path)) == asset.sha256
    with pytest.raises(EngineException):
        library.set_active(pack.id, True)


def test_tampered_library_file_is_refused(tmp_path: Path, pack_folder: Path) -> None:
    workspace = tmp_path / "ws"
    library = SoundLibraryStore(workspace)
    pack, _ = library.install_pack(pack_folder, "Pack", SoundLicense.CC0)
    sound = pack.sounds[0]
    copied = library.root / "packs" / pack.id / sound.file
    copied.chmod(0o666)
    _tone(copied, 0.4, 300)
    with pytest.raises(EngineException) as changed:
        library.add_to_project(ProjectStore.create(workspace, "p"), sound.id)
    assert changed.value.error.code is ErrorCode.SOURCE_CHANGED


def _mean_volume(path: Path, start: float, duration: float) -> float:
    result = subprocess.run(
        [
            str(find_tool("ffmpeg")), "-v", "info", "-ss", str(start), "-t", str(duration), "-i", str(path),
            "-af", "volumedetect", "-vn", "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )  # fmt: skip
    return float(re.search(r"mean_volume: (-?[\d.]+) dB", result.stderr).group(1))


def test_overlapping_sound_effects_render_into_the_mix(tmp_path: Path, media_dir: Path) -> None:
    workspace = tmp_path / "ws"
    folder = tmp_path / "pack"
    _tone(folder / "beep.wav", 0.5, 1000)
    library = SoundLibraryStore(workspace)
    pack, _ = library.install_pack(folder, "Beeps", SoundLicense.CC0)
    store = ProjectStore.create(workspace, "sfx render")
    primary = store.import_asset(media_dir / "talking_head_16x9_30fps_60s.mp4", AssetRole.PRIMARY)
    beep = library.add_to_project(store, pack.sounds[0].id)
    seed = store.create_timeline(
        RenderProfile(id="sfx", width=180, height=320, frame_rate=FrameRate(num=30)), 90
    )
    base = Clip(
        clip_id="primary", asset_id=primary.id, track=TrackId.V1,
        source_in_frames=0, source_out_frames=90, timeline_in_frames=0,
    )  # fmt: skip
    effects = [
        Clip(
            clip_id=f"beep{n}", asset_id=beep.id, track=TrackId.A3, source_in_frames=0,
            source_out_frames=15, timeline_in_frames=at, gain_db=0, rationale="test hit",
        )
        for n, at in enumerate((30, 36))  # overlapping on purpose
    ]  # fmt: skip
    plain = store.commit_timeline(
        seed.model_copy(update={"clips": [base], "audio": AudioMix(normalize=False)}), 0
    )
    assert store.validation_issues(plain) == []
    with_sfx = store.commit_timeline(plain.model_copy(update={"clips": [base, *effects]}), plain.revision)
    assert [i for i in store.validation_issues(with_sfx) if i.severity == "error"] == []

    plain_out = store.safe_path("renders/plain.mp4")
    sfx_out = store.safe_path("renders/sfx.mp4")
    render_timeline(store, plain, plain_out)
    render_timeline(store, with_sfx, sfx_out)
    # Louder where the effects play, unchanged before them.
    assert _mean_volume(sfx_out, 1.05, 0.5) > _mean_volume(plain_out, 1.05, 0.5) + 3
    assert abs(_mean_volume(sfx_out, 0.1, 0.6) - _mean_volume(plain_out, 0.1, 0.6)) < 0.5


def test_sfx_role_rules(tmp_path: Path, media_dir: Path) -> None:
    store = ProjectStore.create(tmp_path, "roles")
    with pytest.raises(EngineException):
        store.import_asset(media_dir / "broll_9x16_10s.mp4", AssetRole.SFX)
    music = store.import_asset(media_dir / "music_bed_30s.m4a", AssetRole.MUSIC)
    primary = store.import_asset(media_dir / "talking_head_16x9_30fps_60s.mp4", AssetRole.PRIMARY)
    seed = store.create_timeline(
        RenderProfile(id="r", width=180, height=320, frame_rate=FrameRate(num=30)), 60
    )
    clips = [
        Clip(clip_id="v", asset_id=primary.id, track=TrackId.V1, source_in_frames=0, source_out_frames=60,
             timeline_in_frames=0),
        Clip(clip_id="m", asset_id=music.id, track=TrackId.A3, source_in_frames=0, source_out_frames=30,
             timeline_in_frames=0),
    ]  # fmt: skip
    issues = store.validation_issues(seed.model_copy(update={"clips": clips}))
    assert any("music asset cannot be placed on A3" in issue.message for issue in issues)


# ---- catalog packs: bundled starter and one-click downloads ------------------------------------


def _zip(path: Path, members: dict[str, Path | bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as bundle:
        for name, content in members.items():
            if isinstance(content, Path):
                bundle.write(content, name)
            else:
                bundle.writestr(name, content)
    return path


def _entry(archive: Path, catalog_id: str = "test-pack", bundled: bool = False) -> SoundCatalogEntry:
    return SoundCatalogEntry(
        id=catalog_id,
        name="Test Pack",
        description="Beeps.",
        author="Tester",
        license=SoundLicense.CC0,
        page_url="https://opengameart.org/content/test-pack",
        download_url=f"https://opengameart.org/sites/default/files/{archive.name}",
        sha256=sha256_file(archive),
        size_bytes=archive.stat().st_size,
        sound_count=2,
        bundled_file=archive.name if bundled else None,
    )


@pytest.fixture
def pack_zip(tmp_path: Path) -> Path:
    tones = tmp_path / "tones"
    (tmp_path / "bundled").mkdir()
    return _zip(
        tmp_path / "bundled" / "test_pack.zip",
        {
            "Audio/clickSoft1.wav": _tone(tones / "a.wav", 0.2, 900),
            "Audio/Whooshes/whoosh_002.wav": _tone(tones / "b.wav", 0.4, 600),
            "Audio/Preview.wav": _tone(tones / "c.wav", 0.3, 300),
            "Audio/License.txt": b"CC0",
            "../escape.wav": _tone(tones / "d.wav", 0.2, 400),
        },
    )


@pytest.fixture
def catalog(monkeypatch: pytest.MonkeyPatch, pack_zip: Path) -> tuple[SoundCatalogEntry, ...]:
    entries = (_entry(pack_zip, bundled=True),)
    monkeypatch.setattr(sound_catalog, "CATALOG", entries)
    monkeypatch.setattr(sound_library, "CATALOG", entries)
    return entries


def test_readable_names_split_camel_case() -> None:
    assert sound_library._readable("phaserUp1") == "phaser Up 1"
    assert sound_library._readable("whoosh_fast-01") == "whoosh fast 01"


def test_extract_audio_skips_unsafe_and_non_audio_members(tmp_path: Path, pack_zip: Path) -> None:
    out = tmp_path / "out"
    assert sound_catalog.extract_audio(pack_zip, out, sound_library.AUDIO_EXTENSIONS) == 2
    files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    # The shared "Audio/" folder is dropped; preview, licence and the ../ entry are skipped.
    assert files == ["Whooshes/whoosh_002.wav", "clickSoft1.wav"]
    assert not (tmp_path / "escape.wav").exists()


@pytest.mark.usefixtures("catalog")
def test_bundled_catalog_pack_installs_with_provenance(tmp_path: Path, pack_zip: Path) -> None:
    library = SoundLibraryStore(tmp_path / "ws")
    pack, skipped = library.install_catalog_pack("test-pack", pack_zip.parent)
    assert skipped == []
    assert (pack.catalog_id, pack.license, pack.source, pack.active) == (
        "test-pack",
        SoundLicense.CC0,
        "https://opengameart.org/content/test-pack",
        True,
    )
    assert sorted((s.name, s.category) for s in pack.sounds) == [
        ("click Soft 1", ""),
        ("whoosh 002", "Whooshes"),
    ]
    assert library.catalog()[0]["installed_pack_id"] == pack.id
    with pytest.raises(EngineException) as again:
        library.install_catalog_pack("test-pack", pack_zip.parent)
    assert "already installed" in again.value.error.message
    assert not list(library.root.glob(".staging-*"))


@pytest.mark.usefixtures("catalog")
def test_tampered_bundled_pack_is_rejected(tmp_path: Path, pack_zip: Path) -> None:
    with pack_zip.open("ab") as handle:
        handle.write(b"x")
    library = SoundLibraryStore(tmp_path / "ws")
    with pytest.raises(EngineException) as info:
        library.install_catalog_pack("test-pack", pack_zip.parent)
    assert info.value.error.code is ErrorCode.SOURCE_CHANGED
    assert library.load().packs == []
    assert not list(library.root.glob(".staging-*"))


@pytest.mark.usefixtures("catalog")
def test_starter_installs_once_and_stays_removed(tmp_path: Path, pack_zip: Path) -> None:
    library = SoundLibraryStore(tmp_path / "ws")
    assert library.install_starter(tmp_path / "missing") is None
    assert library.load().starter_installed is False  # retried once the bundled file exists
    starter = library.install_starter(pack_zip.parent)
    assert starter is not None and starter.active
    library.remove_pack(starter.id)
    assert library.install_starter(pack_zip.parent) is None
    assert library.load().packs == []


class _Response:
    def __init__(self, body: bytes, url: str):
        self._body, self._url = body, url

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def geturl(self) -> str:
        return self._url

    def read(self, size: int = -1) -> bytes:
        chunk, self._body = self._body[:size], self._body[size:]
        return chunk


def _fake_network(monkeypatch: pytest.MonkeyPatch, respond: Callable[[str], _Response]) -> list[str]:
    requested: list[str] = []

    class Opener:
        def open(self, request: urllib.request.Request, timeout: int) -> _Response:
            assert timeout == sound_catalog.DOWNLOAD_TIMEOUT_S
            assert str(request.get_header("User-agent")).startswith("AgenEditor/")
            requested.append(request.full_url)
            return respond(request.full_url)

    monkeypatch.setattr(sound_catalog.urllib.request, "build_opener", lambda *_: Opener())
    return requested


@pytest.mark.usefixtures("catalog")
def test_download_installs_verified_pack(
    tmp_path: Path, pack_zip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = pack_zip.read_bytes()
    requested = _fake_network(monkeypatch, lambda url: _Response(body, url))
    library = SoundLibraryStore(tmp_path / "ws")
    pack, _ = library.install_catalog_pack("test-pack", bundled_dir=None)
    assert requested == ["https://opengameart.org/sites/default/files/test_pack.zip"]
    assert len(pack.sounds) == 2


@pytest.mark.usefixtures("catalog")
@pytest.mark.parametrize(
    ("respond", "code"),
    [
        (lambda url: _Response(b"not the pack", url), ErrorCode.SOURCE_CHANGED),
        (lambda url: _Response(b"x" * 10_000_000, url), ErrorCode.SOURCE_CHANGED),
        (lambda url: _Response(b"", "https://evil.example/pack.zip"), ErrorCode.DOWNLOAD_FAILED),
    ],
)
def test_bad_downloads_install_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    respond: Callable[[str], _Response],
    code: ErrorCode,
) -> None:
    _fake_network(monkeypatch, respond)
    library = SoundLibraryStore(tmp_path / "ws")
    with pytest.raises(EngineException) as info:
        library.install_catalog_pack("test-pack")
    assert info.value.error.code is code
    assert library.load().packs == []
    assert not list(library.root.glob(".staging-*"))


@pytest.mark.usefixtures("catalog")
def test_offline_download_reports_connection_problem(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def offline(url: str) -> _Response:
        raise urllib.error.URLError("getaddrinfo failed")

    _fake_network(monkeypatch, offline)
    with pytest.raises(EngineException) as info:
        SoundLibraryStore(tmp_path / "ws").install_catalog_pack("test-pack")
    assert info.value.error.code is ErrorCode.DOWNLOAD_FAILED
    assert "check your connection" in info.value.error.message


def test_real_catalog_is_pinned_and_starter_ships() -> None:
    entries = sound_catalog.CATALOG
    assert len({e.id for e in entries}) == len(entries)
    assert all(e.license is SoundLicense.CC0 for e in entries)
    assert all(urlsplit(e.download_url).hostname in sound_catalog.ALLOWED_HOSTS for e in entries)
    bundled = [e for e in entries if e.bundled_file]
    assert [e.id for e in bundled] == ["kenney-interface-sounds"]
    shipped = sound_catalog.bundled_sounds_dir() / str(bundled[0].bundled_file)
    sound_catalog.verify_archive(bundled[0], shipped)
    with pytest.raises(EngineException):
        sound_catalog.catalog_entry("not-a-pack")


def test_real_starter_pack_installs_all_sounds(tmp_path: Path) -> None:
    library = SoundLibraryStore(tmp_path / "ws")
    starter = library.install_starter(sound_catalog.bundled_sounds_dir())
    assert starter is not None and starter.catalog_id == "kenney-interface-sounds"
    assert len(starter.sounds) == 100
    assert library.search("click")["total_matches"] > 0
