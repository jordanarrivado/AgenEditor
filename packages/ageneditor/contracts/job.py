"""Job contract: durable background work (probe, transcribe, render, QA)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .common import SCHEMA_VERSION, ContractModel, EngineError, Identifier, ProjectRelativePath, utcnow


class JobKind(StrEnum):
    PROBE = "probe"
    TRANSCRIBE = "transcribe"
    PREVIEW_RENDER = "preview_render"
    FINAL_RENDER = "final_render"
    QA = "qa"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    #: The engine stopped (crash/shutdown) while the job ran; recoverable, never "succeeded".
    INTERRUPTED = "interrupted"


TERMINAL = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELED, JobStatus.INTERRUPTED}


class Job(ContractModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    id: Identifier
    project_id: Identifier
    kind: JobKind
    status: JobStatus = JobStatus.QUEUED
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    #: Timeline revision this job is pinned to (renders/QA). Rendering never reads "latest".
    timeline_revision: int | None = Field(default=None, ge=0)
    output_path: ProjectRelativePath | None = None
    error: EngineError | None = None
    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    ended_at: datetime | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Job:
        if self.status is JobStatus.SUCCEEDED and self.progress < 1.0:
            raise ValueError("succeeded jobs must report progress 1.0")
        if self.status in {JobStatus.FAILED, JobStatus.INTERRUPTED} and self.error is None:
            raise ValueError("failed or interrupted jobs must carry an error")
        if self.status is JobStatus.SUCCEEDED and self.error is not None:
            raise ValueError("succeeded jobs cannot carry an error")
        if self.kind in {JobKind.PREVIEW_RENDER, JobKind.FINAL_RENDER} and self.timeline_revision is None:
            raise ValueError("render jobs must pin a timeline_revision")
        return self

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL
