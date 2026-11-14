"""Client brief, script and requirement checklist, written by a person and read by agents."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from .common import ContractModel, Identifier

#: ``open`` has not been confirmed. ``met`` and ``waived`` are decisions a person made.
BriefItemStatus = Literal["open", "met", "waived"]


class BriefItem(ContractModel):
    id: Identifier
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    status: BriefItemStatus = "open"
    note: str = Field(default="", max_length=300)


class ProjectBrief(ContractModel):
    schema_version: Literal[1] = 1
    project_id: Identifier
    #: Optimistic-lock counter. 0 means nothing has been saved yet.
    version: int = Field(default=0, ge=0)
    brief_text: str = Field(default="", max_length=20000)
    script_text: str = Field(default="", max_length=20000)
    #: A description or link of the reference video. AgenEditor does not fetch or import it.
    reference_note: str = Field(default="", max_length=1000)
    checklist: list[BriefItem] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _unique_items(self) -> ProjectBrief:
        ids = [item.id for item in self.checklist]
        if len(ids) != len(set(ids)):
            raise ValueError("brief checklist item IDs must be unique")
        return self
