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
    # When true, a linked file counts as ready only after the person approved it, and a video that has a
    # "<id>_storyboard" request cannot be linked until that storyboard is approved (or skipped).
    review_required: bool = False
    approved: list[Identifier] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def _validate_links(self) -> AssetChecklist:
        ids = [request.id for request in self.requests]
        if len(ids) != len(set(ids)):
            raise ValueError("asset request IDs must be unique")
        if not self.configured and (self.requests or self.links):
            raise ValueError("an unconfigured checklist must be empty")
        if not self.links.keys() <= set(ids):
            raise ValueError("links must refer to asset requests in this checklist")
        if len(self.approved) != len(set(self.approved)):
            raise ValueError("approved requests must be unique")
        for request_id in self.approved:
            if request_id not in ids or self.links.get(request_id) in (None, "dropped"):
                raise ValueError("only a request with a linked file can be approved")
        if self.review_required:
            for request_id, link in self.links.items():
                storyboard = f"{request_id}_storyboard"
                if (
                    link != "dropped"
                    and storyboard in ids
                    and self.links.get(storyboard) != "dropped"
                    and storyboard not in self.approved
                ):
                    raise ValueError("a video needs its storyboard approved or skipped first")
        for request_id, issue in self.accepted.items():
            if self.links.get(request_id) != issue.asset_id:
                raise ValueError("an accepted issue must belong to the asset linked to its request")
        return self
