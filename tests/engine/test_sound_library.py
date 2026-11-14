"""Sound library: install packs from a local folder, gate on active, place overlapping A3 effects."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ageneditor.contracts import (
    AssetOrigin,
    AssetRole,
    AudioMix,
    Clip,
    EngineException,
    ErrorCode,
    RenderProfile,
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
