"""Workspace editor presets (Phase 6): save a project's finishing settings and reuse them elsewhere.

A preset holds only the caption template, colour preset, B-roll colour matching and audio mix. People save
and delete presets and choose one for a project in the desktop app; the editor agent applies the chosen one
with ``apply_editor_preset``, which goes through the normal revision patch (a new immutable revision).
"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path

from ageneditor.contracts import EngineException, ErrorCode, RevisionPatch
from ageneditor.contracts.editor_preset import MAX_EDITOR_PRESETS, EditorPreset, EditorPresetLibrary
from ageneditor.contracts.revision import RevisionResult, SetAudioMix, SetCaptionPreset, SetColorPreset
from ageneditor.storage import ProjectStore

PRESETS_DIR = "_presets"
_LOCK = threading.Lock()


class EditorPresetStore:
    def __init__(self, workspace: Path):
        self.path = workspace.expanduser().resolve() / PRESETS_DIR / "editor_presets.json"

    def load(self) -> EditorPresetLibrary:
        if not self.path.is_file():
            return EditorPresetLibrary()
        return EditorPresetLibrary.model_validate_json(self.path.read_text(encoding="utf-8"))

    def _save(self, library: EditorPresetLibrary, expected_version: int) -> EditorPresetLibrary:
        current = self.load()
        if current.version != expected_version:
            raise EngineException(
                ErrorCode.REVISION_CONFLICT,
                "editor presets changed; refresh them first",
                current_version=current.version,
            )
        saved = EditorPresetLibrary.model_validate(
            {**library.model_dump(mode="json"), "version": current.version + 1}
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f".{uuid.uuid4().hex[:8]}.tmp")
        temporary.write_text(saved.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(self.path)
        return saved

    def save_from_project(self, store: ProjectStore, name: str, expected_version: int) -> EditorPresetLibrary:
        """A new preset with the finishing settings of the project's current revision."""
        timeline = store.timeline()
        with _LOCK:
            library = self.load()
            if len(library.presets) >= MAX_EDITOR_PRESETS:
                raise EngineException(
                    ErrorCode.INVALID_ARGUMENT, f"keep at most {MAX_EDITOR_PRESETS} editor presets"
                )
            if any(p.name.casefold() == name.strip().casefold() for p in library.presets):
                raise EngineException(ErrorCode.INVALID_ARGUMENT, "a preset with that name exists")
            preset = EditorPreset(
                id=f"preset_{uuid.uuid4().hex[:12]}",
                name=name,
                caption_preset=timeline.caption_preset,
                color_preset=timeline.color_preset,
                broll_color_match=timeline.broll_color_match,
                audio=timeline.audio,
            )
            return self._save(
                library.model_copy(update={"presets": [*library.presets, preset]}), expected_version
            )

    def delete(self, preset_id: str, expected_version: int) -> EditorPresetLibrary:
        with _LOCK:
            library = self.load()
            if all(p.id != preset_id for p in library.presets):
                raise EngineException(ErrorCode.INVALID_ARGUMENT, "preset does not exist")
            remaining = [p for p in library.presets if p.id != preset_id]
            return self._save(library.model_copy(update={"presets": remaining}), expected_version)

    def get(self, preset_id: str) -> EditorPreset:
        for preset in self.load().presets:
            if preset.id == preset_id:
                return preset
        raise EngineException(ErrorCode.INVALID_ARGUMENT, f"editor preset {preset_id!r} does not exist")


def apply_preset(store: ProjectStore, preset: EditorPreset, expected_revision: int) -> RevisionResult:
    """Apply a preset's finishing settings as one revision patch."""
    from ageneditor.revisions import apply_patch

    patch = RevisionPatch(
        project_id=store.project_id,
        summary=f"Apply editor preset {preset.name!r}",
        ops=[
            SetCaptionPreset(preset=preset.caption_preset),
            SetColorPreset(preset=preset.color_preset, broll_color_match=preset.broll_color_match),
            SetAudioMix(audio=preset.audio),
        ],
    )
    return apply_patch(store, patch, expected_revision)
