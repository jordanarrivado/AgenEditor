"""AgenEditor 2.0 orchestration contracts: tasks, attempts, leases, permits, routing, cost and events.

Every state here is engine-recorded. Agent text is diagnostic only and never proves a task finished.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, model_validator

from .common import ContractModel, EngineError, Identifier, Sha256Hex, utcnow
from .production import ContextSnapshot, TaskType

ModelId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,99}$")]


class TaskStatus(StrEnum):
    CREATED = "created"
    BLOCKED = "blocked"
    READY = "ready"
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    RETRY_WAIT = "retry_wait"
    NEEDS_HUMAN = "needs_human"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_TASK_STATUSES = frozenset({TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED})
ACTIVE_TASK_STATUSES = frozenset({TaskStatus.LEASED, TaskStatus.RUNNING})


class BlockReason(StrEnum):
    DEPENDENCIES = "dependencies"
    WAITING_FOR_ASSETS = "waiting_for_assets"
    WAITING_FOR_TEAM = "waiting_for_team"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    BUDGET = "budget"
    NO_CAPABLE_WORKER = "no_capable_worker"
    PAUSED = "paused"
    POLICY = "policy"
    RATE_LIMITED = "rate_limited"


class Task(ContractModel):
    schema_version: Literal[1] = 1
    id: Identifier
    project_id: Identifier
    batch_id: Identifier
    clip_id: Identifier | None = None
    type: TaskType
    #: Revision cycle: 0 for the first pass, n for the n-th QA/human revision round.
    cycle: int = Field(default=0, ge=0, le=50)
    status: TaskStatus = TaskStatus.CREATED
    block_reason: BlockReason | None = None
    priority: int = Field(default=50, ge=0, le=100)
    writer: bool = False
    #: Inputs the result depends on (hashes of sources, context, plan, prior revision, settings).
    input_fingerprint: Sha256Hex | None = None
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1, le=10)
    not_before: datetime | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: EngineError | None = None
    checkpoint_id: Identifier | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _consistent(self) -> Task:
        if self.status is TaskStatus.SUCCEEDED and self.result is None:
            raise ValueError("a succeeded task must reference a verified result or validated no-op")
        if self.status is TaskStatus.BLOCKED and self.block_reason is None:
            raise ValueError("a blocked task must say why")
        return self


class AttemptState(StrEnum):
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    #: The lease ran out before the worker reported; a stale worker can no longer commit.
    EXPIRED = "expired"
    #: The engine restarted while the attempt was live.
    ABANDONED = "abandoned"
    CANCELLED = "cancelled"


class TaskAttempt(ContractModel):
    id: Identifier
    task_id: Identifier
    worker_id: str = Field(max_length=100)
    adapter: str = Field(max_length=40)
    model: str | None = Field(default=None, max_length=100)
    fencing_token: int = Field(ge=1)
    lease_expires_at: datetime
    state: AttemptState = AttemptState.LEASED
    started_at: datetime = Field(default_factory=utcnow)
    ended_at: datetime | None = None
    error: EngineError | None = None


class WorkerLease(ContractModel):
    attempt_id: Identifier
    task_id: Identifier
    worker_id: str = Field(max_length=100)
    fencing_token: int = Field(ge=1)
    lease_expires_at: datetime


class ScopeRole(StrEnum):
    TEAM = "team"
    QA = "qa"
    DISCOVERY = "discovery"
    PLANNER = "planner"


class ScopedTask(ContractModel):
    """What a worker receives: one task, its pinned context and the tools it may call."""

    task: Task
    attempt_id: Identifier
    fencing_token: int = Field(ge=1)
    lease_expires_at: datetime
    role: ScopeRole
    context: ContextSnapshot
    allowed_tools: list[str] = Field(default_factory=list, max_length=40)
    instructions: str = Field(max_length=40000)
    #: Secret, per-attempt capability token. Never logged, never stored in clear text by the engine.
    capability_token: str = Field(min_length=32, max_length=128, repr=False)


class AgentCapabilities(ContractModel):
    adapter_type: str = Field(max_length=40)
    task_types: list[TaskType]
    deterministic: bool
    #: Whether this adapter can call the scoped MCP editing tools (an API call is not an MCP host).
    uses_tools: bool = False
    modalities: list[Literal["text", "image"]] = Field(default_factory=lambda: ["text"])


class RunState(StrEnum):
    RUNNING = "running"
    FINISHED = "finished"
    CANCELLED = "cancelled"


class RunStatus(ContractModel):
    state: RunState
    detail: str = Field(default="", max_length=500)


class CostKind(StrEnum):
    MEASURED = "measured"
    PROVIDER_REPORTED = "provider_reported"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class ModelUsage(ContractModel):
    provider_id: Identifier
    model: str = Field(max_length=100)
    tokens_in: int | None = Field(default=None, ge=0)
    tokens_out: int | None = Field(default=None, ge=0)
    cost_micros: int | None = Field(default=None, ge=0)
    cost_kind: CostKind


class AgentResult(ContractModel):
    """An adapter's report. ``output`` is checked by the engine before any task may succeed."""

    ok: bool
    output: dict[str, Any] = Field(default_factory=dict)
    usage: ModelUsage | None = None
    error: EngineError | None = None
    #: Short diagnostic text (never treated as evidence).
    diagnostic: str = Field(default="", max_length=4000)


class PoolName(StrEnum):
    TEAM = "team"
    ANALYSIS = "analysis"
    RENDER = "render"
    QA = "qa"
    PROVIDER = "provider"


class PoolState(ContractModel):
    capacity: int = Field(ge=0)
    in_use: int = Field(ge=0)
    holders: list[str] = Field(default_factory=list)


class ResourceSnapshot(ContractModel):
    pools: dict[str, PoolState]
    cpu_count: int | None = None
    memory_total_bytes: int | None = None
    memory_available_bytes: int | None = None
    disk_free_bytes: int | None = None
    measured_at: datetime = Field(default_factory=utcnow)


class ResourceConfig(ContractModel):
    """Conservative defaults (not measured): tune with benchmarks (spec §14)."""

    team_slots: int = Field(default=2, ge=1, le=8)
    analysis_slots: int = Field(default=1, ge=1, le=4)
    render_slots: int = Field(default=1, ge=1, le=4)
    qa_slots: int = Field(default=1, ge=1, le=4)
    lease_seconds: int = Field(default=120, ge=10, le=3600)
    host_timeout_seconds: int = Field(default=1800, ge=30, le=7200)


class ProviderModel(ContractModel):
    model: ModelId
    #: Task types this model was verified for with this adapter.
    task_types: list[TaskType]
    input_micros_per_mtok: int | None = Field(default=None, ge=0)
    output_micros_per_mtok: int | None = Field(default=None, ge=0)
    #: Per-run estimate for hosts that do not report token prices (labelled "estimated").
    estimated_run_micros: int = Field(default=0, ge=0)
    #: Measured or user-set quality rank 0-100; 50 means "not benchmarked".
    quality: int = Field(default=50, ge=0, le=100)
    max_output_tokens: int = Field(default=4096, ge=256, le=64000)


class ProviderConfig(ContractModel):
    schema_version: Literal[1] = 1
    id: Identifier
    adapter_type: Literal["claude_code", "anthropic_api"]
    label: str = Field(max_length=80)
    enabled: bool = False
    models: list[ProviderModel] = Field(min_length=1, max_length=10)
    max_concurrency: int = Field(default=1, ge=1, le=8)
    requests_per_minute: int = Field(default=20, ge=1, le=1000)
    #: Environment variable holding the API key. The key itself is never stored by AgenEditor.
    api_key_env: Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")] | None = None
    #: True when the provider receives task context off this machine (all current providers do).
    sends_data_off_device: bool = True


class RouteKind(StrEnum):
    ENGINE = "engine"
    PROVIDER = "provider"
    UNAVAILABLE = "unavailable"


class ModelRoutingDecision(ContractModel):
    task_id: Identifier
    route: RouteKind
    provider_id: Identifier | None = None
    model: str | None = None
    estimated_cost_micros: int = Field(default=0, ge=0)
    block_reason: BlockReason | None = None
    reasons: list[str] = Field(default_factory=list, max_length=20)
    rejected: list[str] = Field(default_factory=list, max_length=40)


class CostReservation(ContractModel):
    id: Identifier
    attempt_id: Identifier
    batch_id: Identifier
    amount_micros: int = Field(ge=0)
    state: Literal["reserved", "reconciled", "released"] = "reserved"


class CheckpointDescriptor(ContractModel):
    id: Identifier
    task_type: TaskType
    #: What the checkpoint is for, e.g. ``clip:<id>`` or ``asset:<id>``.
    scope_key: str = Field(max_length=120)
    input_fingerprint: Sha256Hex
    task_id: Identifier
    attempt_id: Identifier | None = None
    artifact_id: Identifier | None = None
    result: dict[str, Any]
    verified_at: datetime = Field(default_factory=utcnow)
    valid: bool = True


class ArtifactKind(StrEnum):
    PREVIEW_RENDER = "preview_render"
    FINAL_RENDER = "final_render"
    CAPTIONS_SRT = "captions_srt"
    QA_REPORT = "qa_report"


class Artifact(ContractModel):
    id: Identifier
    clip_id: Identifier | None = None
    kind: ArtifactKind
    relative_path: str = Field(max_length=400)
    sha256: Sha256Hex
    size_bytes: int = Field(ge=0)
    timeline_revision: int | None = Field(default=None, ge=0)
    profile_hash: Sha256Hex | None = None
    created_by_attempt: Identifier | None = None
    verified_at: datetime = Field(default_factory=utcnow)


class ProductionEvent(ContractModel):
    seq: int = Field(ge=1)
    event_type: str = Field(max_length=60)
    aggregate_type: Literal["batch", "clip", "task", "attempt", "review", "approval", "delivery"]
    aggregate_id: Identifier
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)


class GraphNode(ContractModel):
    key: Identifier
    type: TaskType
    depends_on: list[Identifier] = Field(default_factory=list, max_length=20)
    params: dict[str, Any] = Field(default_factory=dict)


class ClipTaskGraph(ContractModel):
    clip_id: Identifier
    cycle: int = Field(ge=0)
    nodes: list[GraphNode] = Field(min_length=1, max_length=40)
