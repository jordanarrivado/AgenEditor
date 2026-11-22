"""Project-owned requests for media generated in tools outside AgenEditor."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from .common import ContractModel, Identifier


class AssetRequest(ContractModel):
    id: Identifier
    #: ``audio`` is a voice line; the file is imported with the ``voice`` role and placed on A4.
    kind: Literal["image", "video", "audio"]
    purpose: str = Field(default="", max_length=500)
    #: Image/video: the generator prompt. Voice line: the voice direction (tone, pace, accent).
    prompt: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    aspect: str = Field(default="9:16", min_length=1, max_length=32)
    #: Voice lines only: the exact words to be spoken. Omitted from the wire format when unset, so
    #: image and video requests keep their existing shape.
    line: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)] | None = (
        Field(default=None, exclude_if=lambda value: value is None)
    )
    #: Set when only one variant needs the file (e.g. a localized shot); it then gates only that
    #: variant. Unset: the main edit (and so every variant) needs it.
    variant_id: Identifier | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def _line_matches_kind(self) -> AssetRequest:
        if (self.kind == "audio") != (self.line is not None):
            raise ValueError("voice-line (audio) requests, and only they, give the exact words in `line`")
        return self


class AcceptedIssue(ContractModel):
    """A person's decision to use a linked file despite a known problem."""

    asset_id: Identifier
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class ReviewNote(ContractModel):
    """A person's note on an approved file, written while checking it against the brief.

    Agents read it (for example "use only the first 3 seconds"); it belongs to the exact file approved.
    """

    asset_id: Identifier
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


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
    # Review notes on approved files; cleared when the file is reset, replaced or skipped.
    review_notes: dict[Identifier, ReviewNote] = Field(default_factory=dict)

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
        for request_id, note in self.review_notes.items():
            if request_id not in self.approved or self.links.get(request_id) != note.asset_id:
                raise ValueError("a review note must belong to the approved file linked to its request")
        for request_id, issue in self.accepted.items():
            if self.links.get(request_id) != issue.asset_id:
                raise ValueError("an accepted issue must belong to the asset linked to its request")
        return self
