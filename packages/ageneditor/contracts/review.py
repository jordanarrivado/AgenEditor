"""AgenEditor 2.0 centralized QA, human approval, export and delivery contracts (spec §8, §12, §15).

AI review never equals approval: approvals are recorded only from a person, bound to an exact revision,
render hash, export profile and policy snapshot.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .common import ContractModel, Identifier, Sha256Hex, utcnow
from .revision import RevisionOp


class FindingSeverity(StrEnum):
    BLOCKER = "blocker"
    MAJOR = "major"
    MINOR = "minor"
    INFO = "info"


#: Findings at these severities send the clip back for a targeted revision.
REVISION_SEVERITIES = frozenset({FindingSeverity.BLOCKER, FindingSeverity.MAJOR})


class FindingCategory(StrEnum):
    HOOK = "hook"
    NARRATIVE = "narrative"
    FRAMING = "framing"
    PACING = "pacing"
    CAPTIONS = "captions"
    AUDIO = "audio"
    BROLL = "broll"
    REFERENCE_ALIGNMENT = "reference_alignment"
    BRIEF = "brief"
    TECHNICAL = "technical"
    OTHER = "other"


class FindingReference(ContractModel):
    """Where in the reviewed revision the finding applies. The engine resolves every reference."""

    kind: Literal["timeline", "clip", "caption", "word", "time_range"]
    id: Identifier | None = None
    transcript_id: Identifier | None = None
    start_frame: int | None = Field(default=None, ge=0)
    end_frame: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _shape(self) -> FindingReference:
        if self.kind in {"clip", "caption", "word"} and self.id is None:
            raise ValueError(f"a {self.kind} reference needs an id")
        if self.kind == "word" and self.transcript_id is None:
            raise ValueError("a word reference needs its transcript_id")
        if self.kind == "time_range":
            if self.start_frame is None or self.end_frame is None or self.end_frame <= self.start_frame:
                raise ValueError("a time_range reference needs start_frame < end_frame")
        return self


class FindingInput(ContractModel):
    """One finding as a reviewer submits it."""

    severity: FindingSeverity
    category: FindingCategory
    reference: FindingReference
    evidence: str = Field(min_length=10, max_length=1000)
    suggested_correction: str = Field(default="", max_length=1000)
    #: Optional typed fix the engine can apply deterministically (bounded revision ops only).
    suggested_ops: list[RevisionOp] = Field(default_factory=list, max_length=10)
    confidence: float = Field(ge=0, le=1)


class FindingState(StrEnum):
    OPEN = "open"
    REVISION_QUEUED = "revision_queued"
    RESOLVED = "resolved"
    NEEDS_HUMAN = "needs_human"
    ACCEPTED = "accepted"


class QAFinding(FindingInput):
    id: Identifier
    review_id: Identifier
    clip_id: Identifier
    review_revision: int = Field(ge=0)
    source: Literal["ai", "human", "technical"]
    state: FindingState = FindingState.OPEN
    revision_attempts: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utcnow)


class QAVerdict(StrEnum):
    PASS = "pass"  # noqa: S105 — QA verdict, not a credential
    NEEDS_REVISION = "needs_revision"


class QAReviewStatus(StrEnum):
    QUEUED = "queued"
    IN_REVIEW = "in_review"
    COMPLETED = "completed"
    SUPERSEDED = "superseded"


class QAReview(ContractModel):
    schema_version: Literal[1] = 1
    id: Identifier
    clip_id: Identifier
    task_id: Identifier | None = None
    timeline_revision: int = Field(ge=0)
    render_artifact_id: Identifier
    render_sha256: Sha256Hex
    technical_report_id: Identifier | None = None
    reviewer: str = Field(max_length=100)
    status: QAReviewStatus = QAReviewStatus.QUEUED
    verdict: QAVerdict | None = None
    summary: str = Field(default="", max_length=2000)
    #: What the reviewer actually examined, e.g. ["timeline", "transcript", "technical_qa"].
    evidence_basis: list[Literal["timeline", "transcript", "technical_qa", "plan", "frames", "brief"]] = (
        Field(default_factory=list)
    )
    created_at: datetime = Field(default_factory=utcnow)
    completed_at: datetime | None = None


class QAFindingSubmission(ContractModel):
    verdict: QAVerdict
    summary: str = Field(min_length=1, max_length=2000)
    evidence_basis: list[Literal["timeline", "transcript", "technical_qa", "plan", "frames", "brief"]] = (
        Field(min_length=1)
    )
    findings: list[FindingInput] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def _verdict(self) -> QAFindingSubmission:
        if self.verdict is QAVerdict.NEEDS_REVISION and not any(
            f.severity in REVISION_SEVERITIES for f in self.findings
        ):
            raise ValueError("needs_revision requires at least one blocker or major finding")
        return self


class ClipApproval(ContractModel):
    """A person's approval of exactly one reviewed revision + artifact (spec §8 invariant)."""

    schema_version: Literal[1] = 1
    id: Identifier
    clip_id: Identifier
    timeline_revision: int = Field(ge=0)
    render_artifact_id: Identifier
    render_sha256: Sha256Hex
    export_profile_hash: Sha256Hex
    policy_snapshot_hash: Sha256Hex
    approved_by: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=500)
    approved_at: datetime = Field(default_factory=utcnow)
    revoked_at: datetime | None = None
    revoked_reason: str = Field(default="", max_length=300)


class ExportArtifact(ContractModel):
    id: Identifier
    clip_id: Identifier
    approval_id: Identifier
    timeline_revision: int = Field(ge=0)
    video_artifact_id: Identifier
    video_path: str = Field(max_length=400)
    video_sha256: Sha256Hex
    captions_path: str | None = Field(default=None, max_length=400)
    captions_sha256: Sha256Hex | None = None
    qa_report_id: Identifier
    verified_at: datetime = Field(default_factory=utcnow)


class ManifestFile(ContractModel):
    path: str = Field(max_length=400)
    sha256: Sha256Hex
    size_bytes: int = Field(ge=0)


class DeliveredClip(ContractModel):
    clip_id: Identifier
    title: str = Field(max_length=200)
    timeline_revision: int = Field(ge=0)
    approval_id: Identifier
    export_id: Identifier
    video: str = Field(max_length=400)
    captions: str | None = Field(default=None, max_length=400)


class DeliveryManifest(ContractModel):
    schema_version: Literal[1] = 1
    id: Identifier
    batch_id: Identifier
    project_id: Identifier
    client_id: Identifier | None = None
    package_name: str = Field(max_length=120)
    partial: bool
    clips: list[DeliveredClip] = Field(min_length=1)
    files: list[ManifestFile] = Field(default_factory=list)
    authorized_by: str = Field(min_length=1, max_length=80)
    created_at: datetime = Field(default_factory=utcnow)
    notice: Literal["Packaged locally. AgenEditor did not upload, send or notify anyone."] = (
        "Packaged locally. AgenEditor did not upload, send or notify anyone."
    )
