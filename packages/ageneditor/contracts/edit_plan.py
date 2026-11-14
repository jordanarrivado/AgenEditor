"""An external agent's proposed cuts, retained as a proposal until explicitly committed."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .common import SCHEMA_VERSION, ContractModel, Identifier
from .timeline import Timeline, ValidationIssue


class PlanSegment(ContractModel):
    asset_id: Identifier
    transcript_id: Identifier
    first_word_id: Identifier
    last_word_id: Identifier
    source_in_frames: int = Field(ge=0)
    source_out_frames: int = Field(gt=0)
    rationale: str = Field(min_length=10, max_length=1000)
    score: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _range(self) -> PlanSegment:
        if self.source_out_frames <= self.source_in_frames:
            raise ValueError("source out must follow source in")
        return self


class EditPlan(ContractModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    project_id: Identifier
    target_duration_frames: int = Field(gt=0)
    brief: str = Field(min_length=10, max_length=2000)
    segments: list[PlanSegment] = Field(min_length=2, max_length=5)


class PlanDisposition(StrEnum):
    VALID = "valid"
    RETRY = "retry"
    MANUAL_REVIEW = "manual_review"
    SOURCE_INSUFFICIENT = "source_insufficient"


class PlanResult(ContractModel):
    valid: bool
    disposition: PlanDisposition
    attempt: int = Field(ge=1)
    issues: list[ValidationIssue] = Field(default_factory=list)
    candidate_timeline: Timeline | None = None
