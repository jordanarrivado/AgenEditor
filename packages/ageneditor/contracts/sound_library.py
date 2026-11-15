"""Sound library: sound-effect packs the user installs.

A pack comes from a local folder the person picked, or from AgenEditor's fixed catalog of CC0 packs:
one starter pack ships inside the installer, and the person can download the others with one click.
Catalog downloads use fixed HTTPS URLs and are checked against a pinned SHA-256. The editor agent
never downloads anything; it may only use sounds from packs the person ticked active.
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
CatalogId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")]


class SoundLicense(StrEnum):
    """The pack's licence. For folder installs it is what the person says; AgenEditor does not verify it."""

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
    #: Where the pack came from (URL or note). Display only; never fetched.
    source: SourceNote = ""
    #: Set when the pack was installed from AgenEditor's catalog.
    catalog_id: CatalogId | None = None
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
    #: True once the bundled starter pack was installed, so removing it does not bring it back.
    starter_installed: bool = False
    packs: list[SoundPack] = Field(default_factory=list, max_length=200)


class SoundCatalogEntry(ContractModel):
    """A CC0 pack AgenEditor can install for the person: bundled with the app or downloaded on request."""

    id: CatalogId
    name: PackName
    description: str = Field(max_length=300)
    author: str = Field(max_length=80)
    license: SoundLicense
    #: The page that states the licence (shown to the person).
    page_url: str = Field(max_length=300, pattern=r"^https://")
    #: Fixed HTTPS download; the bytes must match ``sha256`` and ``size_bytes``.
    download_url: str = Field(max_length=300, pattern=r"^https://")
    sha256: Sha256Hex
    size_bytes: int = Field(gt=0, le=100 * 1024 * 1024)
    sound_count: int = Field(gt=0, le=500)
    #: File name of the copy shipped in the installer's ``resources/sounds`` (starter pack), if any.
    bundled_file: str | None = Field(default=None, max_length=120, pattern=r"^[\w.\-]+\.zip$")


class LibrarySource(ContractModel):
    """Where a project asset came from when it was added from the sound library."""

    pack_id: Identifier
    pack_name: PackName
    sound_id: Identifier
    license: SoundLicense
    credit: CreditLine = ""
    source: SourceNote = ""
