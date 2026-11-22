"""QAReport contract: technical checks and editorial flags for one render."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, computed_field

from .common import ContractModel, Identifier, ProjectRelativePath, utcnow


class CheckStatus(StrEnum):
    PASS = "pass"  # noqa: S105 — QA status, not a credential
    WARN = "warn"
    FAIL = "fail"
    #: Check could not run (e.g. no audio stream). Never silently counted as a pass.
    SKIPPED = "skipped"


class CheckKind(StrEnum):
    TECHNICAL = "technical"
    EDITORIAL = "editorial"


class QACheck(ContractModel):
    check_id: Identifier  # e.g. "decode", "resolution", "duration", "loudness", "source_hashes"
    kind: CheckKind = CheckKind.TECHNICAL
    status: CheckStatus
    expected: str | None = Field(default=None, max_length=200)
    measured: str | None = Field(default=None, max_length=200)
    message: str = Field(default="", max_length=2000)


class GeneratedAsset(ContractModel):
    label: Literal["AI-generated (external)"] = "AI-generated (external)"
    asset_id: Identifier
    original_filename: str = Field(max_length=255)
    tool_name: str = Field(min_length=1, max_length=100)


class QAReport(ContractModel):
    # Existing v1 reports remain readable; new reports include generated asset provenance.
    schema_version: Literal[1, 2] = 2
    id: Identifier
    job_id: Identifier
    project_id: Identifier
    timeline_revision: int = Field(ge=0)
    checks: list[QACheck]
    generated_assets: list[GeneratedAsset] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def overall(self) -> CheckStatus:
        """FAIL if any technical check failed; WARN if anything warned or was skipped; else PASS."""
        technical = [c for c in self.checks if c.kind is CheckKind.TECHNICAL]
        if not technical or any(c.status is CheckStatus.FAIL for c in technical):
            return CheckStatus.FAIL
        if any(c.status in {CheckStatus.WARN, CheckStatus.SKIPPED, CheckStatus.FAIL} for c in self.checks):
            return CheckStatus.WARN
        return CheckStatus.PASS


class PreviewQAReport(ContractModel):
    """Checks measured on a finished (usually preview) render before the final render (roadmap Phase 2c).

    Warnings only: preview QA points a person or agent at likely problems and never blocks a render. Every
    check is measured on the rendered file; ``frame_grid_path`` is a contact sheet of the sampled frames.
    """

    schema_version: Literal[1] = 1
    id: Identifier
    job_id: Identifier
    project_id: Identifier
    timeline_revision: int = Field(ge=0)
    variant_id: Identifier | None = Field(default=None, exclude_if=lambda value: value is None)
    variant_revision: int | None = Field(default=None, ge=1, exclude_if=lambda value: value is None)
    #: Output frame numbers that were sampled, in order (also the grid's reading order).
    frames: list[int] = Field(default_factory=list, max_length=12)
    frame_grid_path: ProjectRelativePath | None = None
    checks: list[QACheck]
    created_at: datetime = Field(default_factory=utcnow)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def overall(self) -> CheckStatus:
        """WARN if any check warned; otherwise PASS. Skipped checks are listed but do not warn."""
        warned = any(c.status is CheckStatus.WARN for c in self.checks)
        return CheckStatus.WARN if warned else CheckStatus.PASS
