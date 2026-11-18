"""AgenEditor 2.0 production domain: Client → Project → Batch → Clip, profiles, discovery and plans.

These contracts describe *editing* work only. Nothing here generates media; plan asset requests point at
the project's existing external-generation checklist (spec §3.1).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from .common import ContractModel, Identifier, Sha256Hex, utcnow
from .render_profile import REEL_FINAL, RenderProfile
from .timeline import CaptionPreset, ColorPreset

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
#: A file-name stem used for delivery files: letters, digits, ``_`` and ``-`` only.
SafeStem = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,59}$")]


# ---------------------------------------------------------------- clients, profiles, context


class ClientStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class Client(ContractModel):
    schema_version: Literal[1] = 1
    id: Identifier
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    status: ClientStatus = ClientStatus.ACTIVE
    created_at: datetime = Field(default_factory=utcnow)


class ProfileScope(StrEnum):
    GLOBAL = "global"
    CLIENT = "client"


class Pacing(StrEnum):
    CALM = "calm"
    BALANCED = "balanced"
    FAST = "fast"


class CreativeDefaults(ContractModel):
    """Overridable creative defaults. ``None`` means "inherit from the layer above"."""

    caption_preset: CaptionPreset | None = None
    color_preset: ColorPreset | None = None
    captions: bool | None = None
    normalize_audio: bool | None = None
    pacing: Pacing | None = None
    tone: str | None = Field(default=None, max_length=500)
    platform: str | None = Field(default=None, max_length=60)


CreativeField = Literal[
    "caption_preset", "color_preset", "captions", "normalize_audio", "pacing", "tone", "platform"
]


class ResolvedCreative(ContractModel):
    """Creative settings after every layer was applied; nothing is left to inherit."""

    caption_preset: CaptionPreset = CaptionPreset.CLEAN_WHITE
    color_preset: ColorPreset = ColorPreset.NONE
    captions: bool = True
    normalize_audio: bool = True
    pacing: Pacing = Pacing.BALANCED
    tone: str = Field(default="", max_length=500)
    platform: str = Field(default="", max_length=60)


class StyleObservation(ContractModel):
    """Reference intelligence. ``measured`` values came from FFprobe/FFmpeg; ``estimate`` did not."""

    id: Identifier
    label: ShortText
    value: str = Field(max_length=300)
    basis: Literal["measured", "estimate", "person"]
    source_asset_id: Identifier | None = None


class DeliveryRules(ContractModel):
    allow_partial_delivery: bool = True
    include_captions: bool = True
    include_project_snapshot: bool = True
    naming_prefix: SafeStem | None = None


class InstructionProfile(ContractModel):
    """A versioned instruction layer (global defaults or one client's profile)."""

    schema_version: Literal[1] = 1
    scope: ProfileScope
    client_id: Identifier | None = None
    #: Optimistic-lock counter. 0 means nothing has been saved yet.
    revision: int = Field(default=0, ge=0)
    creative: CreativeDefaults = Field(default_factory=CreativeDefaults)
    instructions: str = Field(default="", max_length=20000)
    #: Client constraints that lower layers may never override (they are escalated instead).
    required_constraints: list[ShortText] = Field(default_factory=list, max_length=50)
    #: Creative fields lower layers may not change; a different lower value is escalated as a conflict.
    locked_creative: list[CreativeField] = Field(default_factory=list)
    style_observations: list[StyleObservation] = Field(default_factory=list, max_length=50)
    delivery: DeliveryRules = Field(default_factory=DeliveryRules)

    @model_validator(mode="after")
    def _scope(self) -> InstructionProfile:
        if (self.scope is ProfileScope.CLIENT) != (self.client_id is not None):
            raise ValueError("client profiles, and only they, name a client_id")
        return self


class ContextLayer(ContractModel):
    scope: Literal["engine", "global", "client", "project", "batch", "clip"]
    source_id: str = Field(max_length=100)
    revision: int = Field(ge=0)
    content_hash: Sha256Hex


class ContextConflict(ContractModel):
    """Two layers disagree on something a lower layer may not decide; escalated, never auto-resolved."""

    field: str = Field(max_length=100)
    message: str = Field(max_length=500)
    layers: list[str] = Field(default_factory=list, max_length=6)


class ContextSnapshot(ContractModel):
    """Immutable, content-addressed instruction/policy context a task ran with (spec §10)."""

    schema_version: Literal[1] = 1
    hash: Sha256Hex
    client_id: Identifier | None = None
    project_id: Identifier
    layers: list[ContextLayer]
    creative: ResolvedCreative
    instructions: list[tuple[str, str]] = Field(default_factory=list, max_length=6)
    required_constraints: list[str] = Field(default_factory=list, max_length=60)
    engine_invariants: list[str]
    style_observations: list[StyleObservation] = Field(default_factory=list, max_length=50)
    delivery: DeliveryRules
    conflicts: list[ContextConflict] = Field(default_factory=list)
    source_asset_hashes: dict[Identifier, Sha256Hex] = Field(default_factory=dict)
    policy_hash: Sha256Hex
    created_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------- batches


class TaskType(StrEnum):
    """Smallest schedulable unit types (spec §7). Batch-level tasks have no clip."""

    ANALYZE_SOURCE = "analyze_source"
    DISCOVER_CLIPS = "discover_clips"
    PLAN_CLIP = "plan_clip"
    STRUCTURAL_EDIT = "structural_edit"
    CAPTIONS = "captions"
    VISUAL_POLISH = "visual_polish"
    AUDIO_POLISH = "audio_polish"
    ENHANCEMENT = "enhancement"
    ASSEMBLE_DRAFT = "assemble_draft"
    RENDER_PREVIEW = "render_preview"
    TECHNICAL_QA = "technical_qa"
    EDITORIAL_QA = "editorial_qa"
    REVISION = "revision"
    EXPORT = "export"


#: Task types that commit to a clip timeline. At most one runs per clip at a time (fenced writer).
WRITER_TASKS = frozenset(
    {
        TaskType.STRUCTURAL_EDIT,
        TaskType.CAPTIONS,
        TaskType.VISUAL_POLISH,
        TaskType.AUDIO_POLISH,
        TaskType.ENHANCEMENT,
        TaskType.REVISION,
    }
)


class BatchTargetMode(StrEnum):
    AUTO = "auto"
    COUNT = "count"


class SelectionMode(StrEnum):
    #: A person confirms which candidates become clips (default).
    HUMAN = "human"
    #: Policy-based autonomy: the top viable candidates are confirmed automatically, never more than
    #: are viable.
    AUTO_TOP = "auto_top"


class BatchPolicy(ContractModel):
    target_mode: BatchTargetMode = BatchTargetMode.AUTO
    target_count: int | None = Field(default=None, ge=1, le=50)
    #: Clip duration range in microseconds (integers, never float seconds).
    min_duration_us: int = Field(default=20_000_000, ge=1_000_000, le=600_000_000)
    max_duration_us: int = Field(default=60_000_000, ge=1_000_000, le=600_000_000)
    platform: str = Field(default="", max_length=60)
    audience: str = Field(default="", max_length=300)
    #: Words or phrases a clip must not contain (case-insensitive substring match on cited speech).
    exclusions: list[ShortText] = Field(default_factory=list, max_length=50)
    #: Primary assets discovery may cite; empty means every active primary asset.
    source_asset_ids: list[Identifier] = Field(default_factory=list, max_length=50)
    output_profile: RenderProfile = REEL_FINAL
    creative: CreativeDefaults = Field(default_factory=CreativeDefaults)
    priority: int = Field(default=50, ge=0, le=100)
    deadline: datetime | None = None
    selection: SelectionMode = SelectionMode.HUMAN
    #: Centralized AI editorial QA. ``skip`` is an explicit, recorded user choice.
    editorial_qa: Literal["required", "skip"] = "required"
    max_revision_attempts_per_finding: int = Field(default=2, ge=1, le=5)
    #: Task types that should run on an AI provider when one is permitted; everything else uses
    #: deterministic engine tools first (spec §11 routing order 1).
    ai_task_types: list[TaskType] = Field(
        default_factory=lambda: [TaskType.EDITORIAL_QA, TaskType.REVISION], max_length=20
    )
    allowed_providers: list[Identifier] = Field(default_factory=list, max_length=10)
    #: Optional user pins: task type → model id.
    model_pins: dict[TaskType, Annotated[str, StringConstraints(min_length=1, max_length=100)]] = Field(
        default_factory=dict
    )
    #: Paid-model budget for the batch in micro-USD. ``None`` means no cap; 0 blocks paid requests.
    budget_micros: int | None = Field(default=None, ge=0)
    #: Explicit permission to send minimum task context to approved external AI providers.
    allow_data_egress: bool = False
    #: Overrides the client's delivery rule when set.
    allow_partial_delivery: bool | None = None
    max_parallel_teams: int | None = Field(default=None, ge=1, le=8)

    @model_validator(mode="after")
    def _consistent(self) -> BatchPolicy:
        if self.max_duration_us < self.min_duration_us:
            raise ValueError("max_duration_us must be at least min_duration_us")
        if (self.target_mode is BatchTargetMode.COUNT) != (self.target_count is not None):
            raise ValueError("target_count is required for count mode and only allowed there")
        return self


class BatchStatus(StrEnum):
    CREATED = "created"
    DISCOVERING = "discovering"
    AWAITING_SELECTION = "awaiting_selection"
    PRODUCING = "producing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class BatchCreateRequest(ContractModel):
    project_id: Identifier
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    policy: BatchPolicy = Field(default_factory=BatchPolicy)
    instructions: str = Field(default="", max_length=10000)


class Batch(ContractModel):
    schema_version: Literal[1] = 1
    id: Identifier
    project_id: Identifier
    client_id: Identifier | None = None
    name: str = Field(max_length=80)
    sequence: int = Field(ge=1)
    status: BatchStatus = BatchStatus.CREATED
    paused: bool = False
    policy: BatchPolicy
    instructions: str = Field(default="", max_length=10000)
    context_hash: Sha256Hex
    created_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------- discovery


class EvidenceRange(ContractModel):
    """A contiguous run of cited transcript words in one registered source."""

    asset_id: Identifier
    transcript_id: Identifier
    first_word_id: Identifier
    last_word_id: Identifier
    start_us: int = Field(ge=0)
    end_us: int = Field(gt=0)

    @model_validator(mode="after")
    def _range(self) -> EvidenceRange:
        if self.end_us <= self.start_us:
            raise ValueError("evidence end must follow start")
        return self


class QualityScores(ContractModel):
    """Advisory 0-1 scores. Weighting is unvalidated until compared with human-reviewed samples."""

    hook: float = Field(ge=0, le=1)
    coherence: float = Field(ge=0, le=1)
    relevance: float | None = Field(default=None, ge=0, le=1)
    clarity: float = Field(ge=0, le=1)
    visual_potential: float | None = Field(default=None, ge=0, le=1)
    source_condition: float = Field(ge=0, le=1)


class CandidateOverlap(ContractModel):
    candidate_id: Identifier
    overlap_us: int = Field(gt=0)


class CandidateStatus(StrEnum):
    PROPOSED = "proposed"
    SELECTED = "selected"
    REJECTED = "rejected"


class ClipCandidate(ContractModel):
    schema_version: Literal[1] = 1
    id: Identifier
    batch_id: Identifier
    rank: int = Field(ge=1)
    topic: str = Field(min_length=1, max_length=200)
    #: Exact transcript text of the first cited sentence; never paraphrased.
    hook_text: str = Field(max_length=500)
    #: Exact cited transcript text (truncated for display, never rewritten).
    quote: str = Field(max_length=4000)
    evidence: list[EvidenceRange] = Field(min_length=1, max_length=5)
    duration_us: int = Field(gt=0)
    scores: QualityScores
    overall_score: float = Field(ge=0, le=1)
    narrative_complete: bool
    method: Literal["deterministic", "agent"]
    rationale: str = Field(default="", max_length=1000)
    caveats: list[str] = Field(default_factory=list, max_length=10)
    overlaps: list[CandidateOverlap] = Field(default_factory=list)
    status: CandidateStatus = CandidateStatus.PROPOSED


class CandidateProposal(ContractModel):
    """What a discovery agent may submit: word citations only. The engine derives times and text."""

    topic: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=10, max_length=1000)
    evidence: list[EvidenceCitation] = Field(min_length=1, max_length=5)
    hook: float | None = Field(default=None, ge=0, le=1)
    relevance: float | None = Field(default=None, ge=0, le=1)
    visual_potential: float | None = Field(default=None, ge=0, le=1)


class EvidenceCitation(ContractModel):
    transcript_id: Identifier
    first_word_id: Identifier
    last_word_id: Identifier
    rationale: str = Field(default="", max_length=1000)


class DiscoveryReport(ContractModel):
    batch_id: Identifier
    method: Literal["deterministic", "agent"]
    requested: int | None = None
    viable_count: int = Field(ge=0)
    recommended_count: int = Field(ge=0)
    #: How many requested clips the source cannot support. Never filled with invented material.
    shortfall: int = Field(ge=0)
    candidates: list[ClipCandidate] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list, max_length=20)


# ---------------------------------------------------------------- clips and plans


class PassKind(StrEnum):
    STRUCTURAL = "structural"
    CAPTIONS = "captions"
    VISUAL = "visual"
    AUDIO = "audio"
    BROLL = "broll"
    MUSIC = "music"
    SFX = "sfx"
    TRANSITIONS = "transitions"


REQUIRED_PASSES = (PassKind.STRUCTURAL, PassKind.CAPTIONS, PassKind.VISUAL, PassKind.AUDIO)


class PlannedCut(ContractModel):
    asset_id: Identifier
    transcript_id: Identifier
    first_word_id: Identifier
    last_word_id: Identifier
    source_in_frames: int = Field(ge=0)
    source_out_frames: int = Field(gt=0)
    rationale: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def _range(self) -> PlannedCut:
        if self.source_out_frames <= self.source_in_frames:
            raise ValueError("source out must follow source in")
        return self


class QACriterion(ContractModel):
    id: Identifier
    text: ShortText


class ClipPlan(ContractModel):
    """Immutable, source-linked production plan for one clip (spec §6)."""

    schema_version: Literal[1] = 1
    id: Identifier
    clip_id: Identifier
    version: int = Field(ge=1)
    candidate_id: Identifier
    narrative_goal: str = Field(min_length=1, max_length=1000)
    hook: str = Field(max_length=500)
    cuts: list[PlannedCut] = Field(min_length=1, max_length=12)
    expected_duration_frames: int = Field(gt=0)
    output_profile: RenderProfile
    required_passes: list[PassKind]
    conditional_passes: list[PassKind] = Field(default_factory=list)
    #: IDs of requests in the project's external-generation asset checklist this clip waits for.
    asset_request_ids: list[Identifier] = Field(default_factory=list, max_length=20)
    allowed_exceptions: list[ShortText] = Field(default_factory=list, max_length=20)
    reference_ids: list[Identifier] = Field(default_factory=list, max_length=20)
    qa_criteria: list[QACriterion] = Field(default_factory=list, max_length=20)
    delivery_name: SafeStem
    creative: ResolvedCreative
    context_hash: Sha256Hex
    author: Literal["engine", "agent"] = "engine"
    created_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _passes(self) -> ClipPlan:
        if not set(REQUIRED_PASSES) <= set(self.required_passes):
            raise ValueError("structural, captions, visual and audio passes are always required")
        if set(self.required_passes) & set(self.conditional_passes):
            raise ValueError("a pass is either required or conditional")
        return self


class ClipPlanDraft(ContractModel):
    """What a planning agent may submit. The engine resolves citations to frames and validates it."""

    narrative_goal: str = Field(min_length=1, max_length=1000)
    hook: str = Field(default="", max_length=500)
    cuts: list[EvidenceCitation] = Field(min_length=1, max_length=12)
    conditional_passes: list[PassKind] = Field(default_factory=list)
    asset_request_ids: list[Identifier] = Field(default_factory=list, max_length=20)
    allowed_exceptions: list[ShortText] = Field(default_factory=list, max_length=20)
    qa_criteria: list[ShortText] = Field(default_factory=list, max_length=20)


class ClipStatus(StrEnum):
    DISCOVERED = "discovered"
    PLANNED = "planned"
    WAITING_FOR_ASSETS = "waiting_for_assets"
    READY = "ready"
    EDITING = "editing"
    PREVIEW_RENDERING = "preview_rendering"
    TECHNICAL_QA = "technical_qa"
    EDITORIAL_QA = "editorial_qa"
    NEEDS_REVISION = "needs_revision"
    REVIEW_READY = "review_ready"
    HUMAN_APPROVED = "human_approved"
    EXPORTED = "exported"
    DELIVERED = "delivered"
    BLOCKED = "blocked"
    RETRY_WAIT = "retry_wait"
    NEEDS_HUMAN = "needs_human"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProductionClip(ContractModel):
    schema_version: Literal[1] = 1
    id: Identifier
    batch_id: Identifier
    candidate_id: Identifier
    title: str = Field(max_length=200)
    ordinal: int = Field(ge=1)
    status: ClipStatus = ClipStatus.DISCOVERED
    status_reason: str = Field(default="", max_length=500)
    plan_id: Identifier | None = None
    #: Newest committed revision of this clip's own timeline history (None before the first edit).
    current_revision: int | None = Field(default=None, ge=0)
    #: Revision the team submitted for render/QA. Reviews and approvals pin to it.
    draft_revision: int | None = Field(default=None, ge=0)
    #: Clip-specific creative instructions from a person (lowest instruction layer).
    instructions: str = Field(default="", max_length=4000)
    creative: CreativeDefaults = Field(default_factory=CreativeDefaults)
    team_id: str | None = Field(default=None, max_length=64)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


CandidateProposal.model_rebuild()
