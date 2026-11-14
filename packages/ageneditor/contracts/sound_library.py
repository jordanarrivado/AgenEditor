"""Sound library: sound-effect packs the user installs from files they already have.

AgenEditor never downloads sounds. A person installs a pack from a local folder, records its
licence, and ticks it active; the editor may only use sounds from active packs.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from .common import ContractModel, Identifier, Sha256Hex, utcnow

PackName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
CreditLine = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]
SourceNote = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]


class SoundLicense(StrEnum):
    """What the person says the pack's licence is. AgenEditor does not verify it."""

    CC0 = "cc0"  # public domain, no credit
    ROYALTY_FREE = "royalty_free"  # site licence allowing use without credit (e.g. Pixabay, Mixkit)
    CC_BY = "cc_by"  # free with credit
    OTHER = "other"  # check the source's terms before publishing


class Sound(ContractModel):
    id: Identifier
    #: Readable name from the file name, e.g. ``whoosh fast 02``.
    name: str = Field(max_length=200)
    #: Sub-folder inside the installed pack, e.g. ``Whooshes``; empty at the pack root.
    category: str = Field(default="", max_length=200)
    original_filename: str = Field(max_length=255)
    #: Path relative to the pack folder.
    file: str = Field(max_length=255)
    sha256: Sha256Hex
    size_bytes: int = Field(ge=0)
    duration_ms: int = Field(ge=0)


class SoundPack(ContractModel):
    id: Identifier
    name: PackName
    license: SoundLicense
    #: Credit text to publish with the video (required for CC BY).
    credit: CreditLine = ""
    #: Where the person got the pack (URL or note). Display only; never fetched.
    source: SourceNote = ""
    active: bool = True
    installed_at: datetime = Field(default_factory=utcnow)
    sounds: list[Sound] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def _credit_when_required(self) -> SoundPack:
        if self.license is SoundLicense.CC_BY and not self.credit:
            raise ValueError("CC BY packs need the credit line to publish with the video")
        return self


class SoundLibrary(ContractModel):
    schema_version: Literal[1] = 1
    packs: list[SoundPack] = Field(default_factory=list, max_length=200)


class LibrarySource(ContractModel):
    """Where a project asset came from when it was added from the sound library."""

    pack_id: Identifier
    pack_name: PackName
    sound_id: Identifier
    license: SoundLicense
    credit: CreditLine = ""
    source: SourceNote = ""
