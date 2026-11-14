"""Workspace-wide sound library. Packs are installed from a local folder the person chose.

Nothing here downloads anything. Installed files are copied, hashed and made read-only; the
editor may only search and use sounds from packs the person ticked active.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import threading
import uuid
from pathlib import Path

from pydantic import ValidationError

from ageneditor.contracts import (
    Asset,
    AssetOrigin,
    AssetProvenance,
    AssetRole,
    EngineException,
    ErrorCode,
)
from ageneditor.contracts.sound_library import LibrarySource, Sound, SoundLibrary, SoundLicense, SoundPack
from ageneditor.media.hashing import sha256_file
from ageneditor.media.probe import probe
from ageneditor.storage import ProjectStore

#: Folder inside the workspace. It has no project.sqlite3, so project listing ignores it.
LIBRARY_DIR = "_sound_library"
AUDIO_EXTENSIONS = {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac", ".opus", ".aif", ".aiff"}
MAX_PACK_SOUNDS = 500
MAX_SOUND_BYTES = 50 * 1024 * 1024
#: Sound effects are short; long beds belong in the Assets box as music.
MAX_SOUND_MS = 60_000
MAX_SKIPPED_REPORTED = 50

_UNSAFE = re.compile(r"[^\w.\- ()\[\]]+")
_locks: dict[Path, threading.Lock] = {}
_locks_guard = threading.Lock()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def _readable(stem: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[_\-.]+", " ", stem)).strip()[:200] or "sound"


def _make_writable(function, path, _info) -> None:
    Path(path).chmod(stat.S_IWRITE)
    function(path)


class SoundLibraryStore:
    def __init__(self, workspace: Path):
        self.root = workspace.expanduser().resolve() / LIBRARY_DIR
        self.index = self.root / "library.json"
        with _locks_guard:
            self._lock = _locks.setdefault(self.root, threading.Lock())

    # ---- persistence -------------------------------------------------------------------------

    def load(self) -> SoundLibrary:
        if not self.index.is_file():
            return SoundLibrary()
        return SoundLibrary.model_validate_json(self.index.read_text(encoding="utf-8"))

    def _save(self, library: SoundLibrary) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.index.with_suffix(f".{uuid.uuid4().hex[:8]}.tmp")
        temporary.write_text(library.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(self.index)

    def _pack_dir(self, pack_id: str) -> Path:
        path = (self.root / "packs" / pack_id).resolve()
        if not path.is_relative_to(self.root) or path.is_symlink():
            raise EngineException(ErrorCode.PERMISSION_DENIED, "sound pack path escapes the library")
        return path

    def _sound_path(self, pack: SoundPack, sound: Sound) -> Path:
        base = self._pack_dir(pack.id)
        path = (base / sound.file).resolve()
        if not path.is_relative_to(base) or path.is_symlink():
            raise EngineException(ErrorCode.PERMISSION_DENIED, "sound path escapes its pack")
        return path

    # ---- person-only operations (desktop) ------------------------------------------------------

    def install_pack(
        self,
        source: Path,
        name: str,
        license: SoundLicense,
        credit: str = "",
        source_note: str = "",
    ) -> tuple[SoundPack, list[dict[str, str]]]:
        """Copy every usable audio file under ``source`` into a new pack. Symlinks are skipped."""
        source = source.expanduser()
        if not source.is_absolute() or not source.is_dir() or source.is_symlink():
            raise EngineException(ErrorCode.PERMISSION_DENIED, "install needs a folder you selected")
        # Validate the pack details before copying anything.
        try:
            draft = SoundPack(id=_id("pack"), name=name, license=license, credit=credit, source=source_note)
        except ValidationError as exc:
            message = exc.errors()[0].get("msg", "invalid pack details").removeprefix("Value error, ")
            raise EngineException(ErrorCode.INVALID_ARGUMENT, message) from exc
        candidates: list[Path] = []
        for directory, folders, files in os.walk(source, followlinks=False):
            folders[:] = sorted(f for f in folders if not f.startswith("."))
            for filename in sorted(files):
                path = Path(directory) / filename
                if path.suffix.lower() in AUDIO_EXTENSIONS and not path.is_symlink():
                    candidates.append(path)
        if not candidates:
            raise EngineException(ErrorCode.ASSET_NOT_FOUND, "no audio files were found in that folder")
        skipped: list[dict[str, str]] = []
        if len(candidates) > MAX_PACK_SOUNDS:
            skipped.append(
                {"file": f"{len(candidates) - MAX_PACK_SOUNDS} more files", "reason": "pack limit"}
            )
            candidates = candidates[:MAX_PACK_SOUNDS]

        pack_dir = self._pack_dir(draft.id)
        pack_dir.mkdir(parents=True, exist_ok=False)
        sounds: list[Sound] = []
        try:
            for path in candidates:
                reason = self._copy_sound(path, source, pack_dir, sounds)
                if reason:
                    skipped.append({"file": str(path.relative_to(source)), "reason": reason})
            if not sounds:
                raise EngineException(ErrorCode.UNSUPPORTED_CODEC, "none of the audio files could be used")
            pack = draft.model_copy(update={"sounds": sounds})
            with self._lock:
                library = self.load()
                self._save(library.model_copy(update={"packs": [*library.packs, pack]}))
        except BaseException:
            shutil.rmtree(pack_dir, onexc=_make_writable)
            raise
        return pack, skipped[:MAX_SKIPPED_REPORTED]

    def _copy_sound(self, path: Path, source: Path, pack_dir: Path, sounds: list[Sound]) -> str | None:
        try:
            size = path.stat().st_size
        except OSError:
            return "unreadable"
        if size == 0 or size > MAX_SOUND_BYTES:
            return "empty or larger than 50 MB"
        try:
            metadata = probe(path)
        except EngineException:
            return "not a readable audio file"
        if metadata.audio is None or metadata.video is not None:
            return "needs to be audio only"
        duration_ms = metadata.audio.duration_samples * 1000 // metadata.audio.sample_rate
        if duration_ms <= 0 or duration_ms > MAX_SOUND_MS:
            return "longer than 60 seconds (add long tracks as music instead)"
        before = sha256_file(path)
        sound_id = _id("sound")
        filename = _UNSAFE.sub("_", path.name)[:150].rstrip(". ") or "sound"
        destination = pack_dir / f"{sound_id}_{filename}"
        shutil.copyfile(path, destination)
        if sha256_file(destination) != before:
            destination.unlink(missing_ok=True)
            return "changed while copying"
        destination.chmod(destination.stat().st_mode & ~0o222)
        relative_parent = path.parent.relative_to(source)
        sounds.append(
            Sound(
                id=sound_id,
                name=_readable(path.stem),
                category=" / ".join(relative_parent.parts)[:200],
                original_filename=path.name[:255],
                file=destination.name,
                sha256=before,
                size_bytes=size,
                duration_ms=duration_ms,
            )
        )
        return None

    def set_active(self, pack_id: str, active: bool) -> SoundPack:
        with self._lock:
            library = self.load()
            if not any(p.id == pack_id for p in library.packs):
                raise EngineException(ErrorCode.ASSET_NOT_FOUND, "sound pack is not installed")
            packs = [p.model_copy(update={"active": active}) if p.id == pack_id else p for p in library.packs]
            self._save(library.model_copy(update={"packs": packs}))
        return next(p for p in packs if p.id == pack_id)

    def remove_pack(self, pack_id: str) -> None:
        """Uninstall a pack. Sounds already added to projects stay in those projects."""
        with self._lock:
            library = self.load()
            if not any(p.id == pack_id for p in library.packs):
                raise EngineException(ErrorCode.ASSET_NOT_FOUND, "sound pack is not installed")
            self._save(library.model_copy(update={"packs": [p for p in library.packs if p.id != pack_id]}))
            directory = self._pack_dir(pack_id)
            if directory.exists():
                shutil.rmtree(directory, onexc=_make_writable)

    # ---- editor operations (MCP) ------------------------------------------------------------

    def search(self, query: str = "", limit: int = 30) -> dict[str, object]:
        """Search active packs only. Every word in ``query`` must appear in name, folder or pack."""
        library = self.load()
        active = [p for p in library.packs if p.active]
        words = [w for w in re.split(r"\W+", query.lower()) if w]
        matches = []
        for pack in active:
            for sound in pack.sounds:
                haystack = f"{sound.name} {sound.category} {pack.name}".lower()
                if all(word in haystack for word in words):
                    matches.append(
                        {
                            "sound_id": sound.id,
                            "name": sound.name,
                            "category": sound.category,
                            "duration_ms": sound.duration_ms,
                            "pack": pack.name,
                            "license": pack.license.value,
                            "credit": pack.credit,
                        }
                    )
        limit = max(1, min(limit, 100))
        return {
            "active_packs": len(active),
            "installed_packs": len(library.packs),
            "total_matches": len(matches),
            "sounds": matches[:limit],
        }

    def add_to_project(self, store: ProjectStore, sound_id: str) -> Asset:
        """Register an active pack's sound as an sfx asset in the project (once per sound)."""
        library = self.load()
        found = next(
            ((pack, sound) for pack in library.packs for sound in pack.sounds if sound.id == sound_id), None
        )
        if found is None:
            raise EngineException(ErrorCode.ASSET_NOT_FOUND, "sound is not in the library")
        pack, sound = found
        if not pack.active:
            raise EngineException(
                ErrorCode.PERMISSION_DENIED, "that sound's pack is not active; the user must tick it first"
            )
        for asset in store.assets().values():
            if asset.role is AssetRole.SFX and asset.sha256 == sound.sha256:
                return asset
        path = self._sound_path(pack, sound)
        if not path.is_file() or sha256_file(path) != sound.sha256:
            raise EngineException(ErrorCode.SOURCE_CHANGED, "the installed sound file changed or is missing")
        provenance = AssetProvenance(
            origin=AssetOrigin.SOUND_LIBRARY,
            library=LibrarySource(
                pack_id=pack.id,
                pack_name=pack.name,
                sound_id=sound.id,
                license=pack.license,
                credit=pack.credit,
                source=pack.source,
            ),
        )
        return store.import_asset(path, AssetRole.SFX, provenance)
