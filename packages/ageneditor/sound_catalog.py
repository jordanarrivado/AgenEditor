"""AgenEditor's fixed catalog of free (CC0) sound-effect packs.

Every entry is a pack Kenney published on OpenGameArt under CC0. The starter pack also ships inside
the installer (``resources/sounds``), so it installs with no network access. The others download only
when the person clicks "Download & install"; the editor agent cannot trigger a download.

Downloads go to a fixed HTTPS URL on an allow-listed host, are capped at the expected size and must
match the pinned SHA-256 before anything is extracted.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from ageneditor import __version__
from ageneditor.contracts import EngineException, ErrorCode
from ageneditor.contracts.sound_library import SoundCatalogEntry, SoundLicense

#: The only hosts a catalog pack may be downloaded from (redirects included).
ALLOWED_HOSTS = frozenset({"opengameart.org"})
DOWNLOAD_TIMEOUT_S = 60
_CHUNK = 256 * 1024
#: Extraction limits; real packs are far below these.
MAX_ARCHIVE_MEMBERS = 2_000
MAX_EXTRACTED_BYTES = 300 * 1024 * 1024

_OGA = "https://opengameart.org"
CATALOG: tuple[SoundCatalogEntry, ...] = (
    SoundCatalogEntry(
        id="kenney-interface-sounds",
        name="Interface Sounds",
        description="Clicks, pops, ticks, toggles, glass dings, confirmations and errors.",
        author="Kenney",
        license=SoundLicense.CC0,
        page_url=f"{_OGA}/content/interface-sounds",
        download_url=f"{_OGA}/sites/default/files/kenney_interfaceSounds.zip",
        sha256="f2193d072726d6758a5f7871b2dcc54dcce0d5c35c6f0a62f92549b327c81232",
        size_bytes=834_536,
        sound_count=100,
        bundled_file="kenney_interfaceSounds.zip",
    ),
    SoundCatalogEntry(
        id="kenney-ui-sfx",
        name="UI Sound Effects",
        description="Button clicks, switches, rollovers and mouse clicks.",
        author="Kenney",
        license=SoundLicense.CC0,
        page_url=f"{_OGA}/content/51-ui-sound-effects-buttons-switches-and-clicks",
        download_url=f"{_OGA}/sites/default/files/UI_SFX_Set.zip",
        sha256="66026b9e39859b85964dbb3ee7a2d1fc46a6a406bb3e97c8b0defee0e25fb369",
        size_bytes=1_425_902,
        sound_count=51,
    ),
    SoundCatalogEntry(
        id="kenney-digital-sfx",
        name="Digital Sound Effects",
        description="Lasers, phasers, power-ups, zaps and other synth blips.",
        author="Kenney",
        license=SoundLicense.CC0,
        page_url=f"{_OGA}/content/63-digital-sound-effects-lasers-phasers-space-etc",
        download_url=f"{_OGA}/sites/default/files/Digital_SFX_Set.zip",
        sha256="022f0f4b73da55989567fe2b373c8af89d74f5d015257e293cab497ca606d063",
        size_bytes=1_072_810,
        sound_count=62,
    ),
    SoundCatalogEntry(
        id="kenney-rpg-sfx",
        name="RPG Sound Effects",
        description="Footsteps, doors, cloth, coins, books, metal and wood handling.",
        author="Kenney",
        license=SoundLicense.CC0,
        page_url=f"{_OGA}/content/50-rpg-sound-effects",
        download_url=f"{_OGA}/sites/default/files/RPGsounds_Kenney.zip",
        sha256="3ae398ad63e293f9c450bda22d5d81c3af69c74df66fc1400f33c012c0bbc231",
        size_bytes=690_952,
        sound_count=51,
    ),
    SoundCatalogEntry(
        id="kenney-casino-sfx",
        name="Casino Sound Effects",
        description="Cards, dice, chips and shuffles.",
        author="Kenney",
        license=SoundLicense.CC0,
        page_url=f"{_OGA}/content/54-casino-sound-effects-cards-dice-chips",
        download_url=f"{_OGA}/sites/default/files/kenney_casino-audio.zip",
        sha256="f36250766ac5bc378c13708ddf12a23a8e54a3251f8d482c7536e51b5dbafa18",
        size_bytes=876_839,
        sound_count=54,
    ),
    SoundCatalogEntry(
        id="kenney-sci-fi-sounds",
        name="Sci-Fi Sounds",
        description="Engines, lasers, doors, force fields, explosions and computer noise.",
        author="Kenney",
        license=SoundLicense.CC0,
        page_url=f"{_OGA}/content/sci-fi-sounds",
        download_url=f"{_OGA}/sites/default/files/sci-fi_sounds.zip",
        sha256="119340f351a5098ad814f78719438c0da355a9ce8a4c8a3af6a8d48aa3d49e04",
        size_bytes=5_875_104,
        sound_count=73,
    ),
)


def catalog_entry(catalog_id: str) -> SoundCatalogEntry:
    for entry in CATALOG:
        if entry.id == catalog_id:
            return entry
    raise EngineException(ErrorCode.ASSET_NOT_FOUND, "that sound pack is not in AgenEditor's catalog")


def bundled_sounds_dir() -> Path:
    """Folder holding packs shipped with the app (``resources/sounds`` next to the engine)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "resources" / "sounds"
    return Path(__file__).resolve().parents[2] / "apps/desktop/src-tauri/resources/sounds"


def _allowed(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" and (parts.hostname or "") in ALLOWED_HOSTS


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _allowed(newurl):
            raise EngineException(ErrorCode.DOWNLOAD_FAILED, "the download redirected somewhere unexpected")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def verify_archive(entry: SoundCatalogEntry, path: Path) -> None:
    """Raise unless ``path`` is exactly the pinned archive."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            size += len(chunk)
            digest.update(chunk)
    if size != entry.size_bytes or digest.hexdigest() != entry.sha256:
        raise EngineException(
            ErrorCode.SOURCE_CHANGED,
            f"{entry.name} is not the expected file, so it was not installed",
        )


def download_archive(entry: SoundCatalogEntry, destination: Path) -> None:
    """Download ``entry`` to ``destination`` and verify it. Removes the partial file on failure."""
    if not _allowed(entry.download_url):
        raise EngineException(ErrorCode.PERMISSION_DENIED, "catalog download URL is not allowed")
    request = urllib.request.Request(  # noqa: S310 - scheme and host checked above
        entry.download_url, headers={"User-Agent": f"AgenEditor/{__version__} (sound pack download)"}
    )
    opener = urllib.request.build_opener(_HttpsOnlyRedirects)
    try:
        with opener.open(request, timeout=DOWNLOAD_TIMEOUT_S) as response, destination.open("xb") as out:
            if not _allowed(response.geturl()):
                raise EngineException(ErrorCode.DOWNLOAD_FAILED, "the download came from an unexpected host")
            received = 0
            while chunk := response.read(_CHUNK):
                received += len(chunk)
                if received > entry.size_bytes:
                    raise EngineException(ErrorCode.SOURCE_CHANGED, f"{entry.name} is larger than expected")
                out.write(chunk)
    except EngineException:
        destination.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        destination.unlink(missing_ok=True)
        reason = getattr(exc, "reason", None) or exc.__class__.__name__
        raise EngineException(
            ErrorCode.DOWNLOAD_FAILED, f"could not download {entry.name} ({reason}); check your connection"
        ) from exc
    try:
        verify_archive(entry, destination)
    except EngineException:
        destination.unlink(missing_ok=True)
        raise


def extract_audio(archive: Path, destination: Path, extensions: set[str]) -> int:
    """Extract only audio files from ``archive`` into ``destination``; return how many.

    Member paths with ``..``, drive letters or absolute roots are skipped. A single top-level
    folder shared by every file (e.g. ``Audio/``) is dropped so categories stay meaningful.
    """
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    try:
        with zipfile.ZipFile(archive) as bundle:
            members = []
            for info in bundle.infolist()[:MAX_ARCHIVE_MEMBERS]:
                name = info.filename.replace("\\", "/")
                parts = PurePosixPath(name).parts
                if info.is_dir() or not parts or name.startswith("/") or ":" in name:
                    continue
                if any(part in {"", ".", ".."} or part.startswith(".") for part in parts):
                    continue
                stem, suffix = PurePosixPath(parts[-1]).stem, PurePosixPath(parts[-1]).suffix.lower()
                if suffix in extensions and stem.lower() != "preview":
                    members.append((info, parts))
            if sum(info.file_size for info, _ in members) > MAX_EXTRACTED_BYTES:
                raise EngineException(ErrorCode.INVALID_ARGUMENT, "the sound pack is too large to unpack")
            folders = {parts[0] for _, parts in members if len(parts) > 1}
            strip = len(folders) == 1 and all(len(parts) > 1 for _, parts in members)
            written = 0
            for info, parts in members:
                target = root.joinpath(*(parts[1:] if strip else parts)).resolve()
                if not target.is_relative_to(root) or target.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as source, target.open("xb") as out:
                    shutil.copyfileobj(source, out, _CHUNK)
                written += 1
            return written
    except zipfile.BadZipFile as exc:
        raise EngineException(
            ErrorCode.UNSUPPORTED_CODEC, "the sound pack is not a readable zip file"
        ) from exc
