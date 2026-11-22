"""Editor presets (Phase 6): reusable finishing settings shared by every project in a workspace."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from .common import ContractModel, Identifier, utcnow
from .timeline import AudioMix, CaptionPreset, ColorPreset

MAX_EDITOR_PRESETS = 50


class EditorPreset(ContractModel):
    """Caption template, colour and audio settings; never cuts, clips or media."""

    id: Identifier
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
    caption_preset: CaptionPreset = CaptionPreset.CLEAN_WHITE
    color_preset: ColorPreset = ColorPreset.NONE
    broll_color_match: bool = False
    audio: AudioMix = Field(default_factory=AudioMix)
    created_at: datetime = Field(default_factory=utcnow)


class EditorPresetLibrary(ContractModel):
    schema_version: Literal[1] = 1
    version: int = Field(default=0, ge=0)
    presets: list[EditorPreset] = Field(default_factory=list, max_length=MAX_EDITOR_PRESETS)

    @model_validator(mode="after")
    def _unique(self) -> EditorPresetLibrary:
        ids = [preset.id for preset in self.presets]
        if len(ids) != len(set(ids)):
            raise ValueError("preset IDs must be unique")
        names = [preset.name.casefold() for preset in self.presets]
        if len(names) != len(set(names)):
            raise ValueError("preset names must be unique")
        return self
