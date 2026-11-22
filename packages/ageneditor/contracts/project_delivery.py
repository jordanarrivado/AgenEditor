"""Delivery of a single project's finals (roadmap Phase 2c): client file names and a known-issues note.

The batch production flow has its own packaged delivery (``DeliveryManifest``). This one covers the desktop
workspace: the main edit and its variants, saved into one new folder with a note built from the project's
records. AgenEditor never sends anything; the folder is for the person to deliver.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from .common import ContractModel, Identifier, utcnow

#: A file stem: letters, digits, ``-`` and ``_`` only.
FileStem = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,120}$")]


class DeliveryFile(ContractModel):
    job_id: Identifier
    #: File name without extension; the video is ``<name>.mp4`` and its captions ``<name>.srt``.
    name: FileStem
    #: "Main edit" or the variant's name.
    label: str = Field(min_length=1, max_length=80)
    timeline_revision: int = Field(ge=0)
    variant_id: Identifier | None = None
    variant_revision: int | None = Field(default=None, ge=1)
    duration_frames: int = Field(ge=0)
    #: True when the render has a caption file beside it (saved as ``<name>.srt``).
    captions: bool = False


class ExcludedRender(ContractModel):
    job_id: Identifier
    reason: str = Field(min_length=1, max_length=300)


class KnownIssue(ContractModel):
    """One line of the known-issues note, with where it was recorded."""

    source: Literal["accepted", "technical_qa", "editorial_qa", "preview_check"]
    #: Names of the delivery files it concerns (empty: the whole delivery).
    files: list[FileStem] = Field(default_factory=list)
    message: str = Field(min_length=1, max_length=1200)


class GeneratedMaterial(ContractModel):
    """A file made in an outside tool that appears in delivered files; always labelled as such."""

    label: Literal["AI-generated (external)"] = "AI-generated (external)"
    asset_id: Identifier
    original_filename: str = Field(max_length=255)
    tool_name: str = Field(min_length=1, max_length=100)
    files: list[FileStem] = Field(min_length=1)


class ProjectDeliveryPlan(ContractModel):
    schema_version: Literal[1] = 1
    project_id: Identifier
    #: Name of the new folder the files are saved into.
    folder: FileStem
    files: list[DeliveryFile] = Field(default_factory=list, max_length=25)
    excluded: list[ExcludedRender] = Field(default_factory=list)
    issues: list[KnownIssue] = Field(default_factory=list)
    generated: list[GeneratedMaterial] = Field(default_factory=list)
    #: The note saved beside the files (Markdown), built from the fields above.
    note: str = Field(max_length=200_000)
    created_at: datetime = Field(default_factory=utcnow)
