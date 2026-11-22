"""Built-in chat (Phase 6, docs/BUILT_IN_CHAT_PLAN.md): messages and proposals stored per project.

A provider model the person chooses (with their own API key) reads bounded text evidence and may propose
an ``EditPlan``. The engine validates every proposal with the normal ``submit_edit_plan`` checks; nothing
changes the timeline until a person applies a valid proposal, which then becomes one new immutable
revision.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from .common import ContractModel, Identifier, utcnow
from .edit_plan import EditPlan, PlanResult


class ChatProvider(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GEMINI = "gemini"


ModelName = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9._:\-/]{1,100}$")]


class ChatMessage(ContractModel):
    id: Identifier
    project_id: Identifier
    role: Literal["user", "assistant", "system"]
    text: str = Field(max_length=20_000)
    provider: ChatProvider | None = None
    model: ModelName | None = None
    proposal_id: Identifier | None = None
    created_at: datetime = Field(default_factory=utcnow)


class ChatProposal(ContractModel):
    """A validated (or refused) plan from the chat, pinned to the revision it was made against."""

    id: Identifier
    project_id: Identifier
    base_revision: int = Field(ge=0)
    plan: EditPlan
    result: PlanResult
    status: Literal["proposed", "applied", "stale", "rejected"] = "proposed"
    applied_revision: int | None = Field(default=None, ge=1)
    created_at: datetime = Field(default_factory=utcnow)


class ChatTurn(ContractModel):
    messages: list[ChatMessage]
    proposal: ChatProposal | None = None
