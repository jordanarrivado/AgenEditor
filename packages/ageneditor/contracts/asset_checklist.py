"""Project-owned requests for media generated in tools outside AgenEditor."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from .common import ContractModel, Identifier


class AssetRequest(ContractModel):
    id: Identifier
    kind: Literal["image", "video"]
    purpose: str = Field(default="", max_length=500)
    prompt: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    aspect: str = Field(default="9:16", min_length=1, max_length=32)


class AcceptedIssue(ContractModel):
    """A person's decision to use a linked file despite a known problem."""

    asset_id: Identifier
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class AssetChecklist(ContractModel):
    schema_version: Literal[1] = 1
    project_id: Identifier
    version: int = Field(default=0, ge=0)
    configured: bool = False
    requests: list[AssetRequest] = Field(default_factory=list, max_length=100)
    # An asset ID means ready; "dropped" records an explicit user decision.
    links: dict[Identifier, Identifier | Literal["dropped"]] = Field(default_factory=dict)
    # Known-issues log: a linked request the user accepted as-is, with the reason.
    accepted: dict[Identifier, AcceptedIssue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_links(self) -> AssetChecklist:
        ids = [request.id for request in self.requests]
        if len(ids) != len(set(ids)):
            raise ValueError("asset request IDs must be unique")
        if not self.configured and (self.requests or self.links):
            raise ValueError("an unconfigured checklist must be empty")
        if not self.links.keys() <= set(ids):
            raise ValueError("links must refer to asset requests in this checklist")
        for request_id, issue in self.accepted.items():
            if self.links.get(request_id) != issue.asset_id:
                raise ValueError("an accepted issue must belong to the asset linked to its request")
        return self
