"""Adaptive, cost-aware routing (spec §11).

Order: (1) deterministic engine tools when AI is unnecessary or not permitted; (2) providers filtered by
verified capability, enablement, data-egress permission and the batch allowlist; (3) user pins;
(4) rank by quality, then estimated cost; (5) reserve budget before dispatch.

Costs are estimates until a provider reports usage. A provider without token prices and without a per-run
estimate is treated as unmetered (for example a subscription host) and its cost is shown as "unknown".
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from ageneditor.contracts import (
    BatchPolicy,
    BlockReason,
    ModelRoutingDecision,
    ProviderConfig,
    ProviderModel,
    RouteKind,
    Task,
    TaskType,
)

#: Task types with a deterministic engine handler.
DETERMINISTIC_TASKS = frozenset(
    {
        TaskType.ANALYZE_SOURCE,
        TaskType.DISCOVER_CLIPS,
        TaskType.PLAN_CLIP,
        TaskType.STRUCTURAL_EDIT,
        TaskType.CAPTIONS,
        TaskType.VISUAL_POLISH,
        TaskType.AUDIO_POLISH,
        TaskType.ENHANCEMENT,
        TaskType.ASSEMBLE_DRAFT,
        TaskType.RENDER_PREVIEW,
        TaskType.TECHNICAL_QA,
        TaskType.EXPORT,
        # REVISION only when every finding carries typed ops (see ``needs_ai``).
        TaskType.REVISION,
    }
)
#: Never routed to AI: measurement, rendering and bookkeeping are engine work.
ENGINE_ONLY = frozenset(
    {
        TaskType.ANALYZE_SOURCE,
        TaskType.ASSEMBLE_DRAFT,
        TaskType.RENDER_PREVIEW,
        TaskType.TECHNICAL_QA,
        TaskType.EXPORT,
    }
)
#: Rough token budgets for estimates only (labelled "estimated" in the UI).
ESTIMATED_TOKENS: dict[TaskType, tuple[int, int]] = {
    TaskType.DISCOVER_CLIPS: (40_000, 4_000),
    TaskType.PLAN_CLIP: (20_000, 3_000),
    TaskType.EDITORIAL_QA: (25_000, 3_000),
    TaskType.REVISION: (30_000, 4_000),
}


@dataclass(frozen=True)
class SpendState:
    budget_micros: int | None
    committed_micros: int


def estimate_cost(task_type: TaskType, model: ProviderModel) -> int:
    if model.input_micros_per_mtok is None or model.output_micros_per_mtok is None:
        return model.estimated_run_micros
    tokens_in, tokens_out = ESTIMATED_TOKENS.get(task_type, (20_000, 3_000))
    return (tokens_in * model.input_micros_per_mtok + tokens_out * model.output_micros_per_mtok) // 1_000_000


def needs_ai(task: Task, policy: BatchPolicy) -> bool:
    if task.type in ENGINE_ONLY:
        return False
    if task.type is TaskType.EDITORIAL_QA:
        return True
    if task.type is TaskType.REVISION:
        # Typed fixes from a reviewer are applied by the engine; free-form fixes need an editing agent.
        return not task.params.get("deterministic_ops", False)
    return task.type in policy.ai_task_types


def route(
    task: Task,
    policy: BatchPolicy,
    providers: list[ProviderConfig],
    adapter_ready: Mapping[str, tuple[bool, str]],
    provider_ready: Mapping[str, tuple[bool, str]],
    spend: SpendState,
    now: datetime,
) -> ModelRoutingDecision:
    del now
    deterministic_ok = task.type in DETERMINISTIC_TASKS and (
        task.type is not TaskType.REVISION or bool(task.params.get("deterministic_ops"))
    )
    reasons: list[str] = []
    rejected: list[str] = []

    def engine(reason: str) -> ModelRoutingDecision:
        return ModelRoutingDecision(
            task_id=task.id, route=RouteKind.ENGINE, reasons=[*reasons, reason], rejected=rejected
        )

    def unavailable(block: BlockReason, reason: str) -> ModelRoutingDecision:
        return ModelRoutingDecision(
            task_id=task.id,
            route=RouteKind.UNAVAILABLE,
            block_reason=block,
            reasons=[*reasons, reason],
            rejected=rejected,
        )

    if not needs_ai(task, policy):
        return engine("deterministic engine tools cover this task")
    if not policy.allow_data_egress:
        if deterministic_ok:
            return engine("AI requested but data egress is not permitted for this batch; engine tools used")
        return unavailable(
            BlockReason.POLICY,
            "this task needs an AI provider, but the batch does not permit sending task context to one",
        )
    pin = policy.model_pins.get(task.type)
    options: list[tuple[int, int, str, ProviderConfig, ProviderModel]] = []
    rate_limited = budget_blocked = False
    for provider in providers:
        label = provider.id
        if not provider.enabled:
            rejected.append(f"{label}: disabled")
            continue
        if policy.allowed_providers and provider.id not in policy.allowed_providers:
            rejected.append(f"{label}: not allowed for this batch")
            continue
        ready, why = adapter_ready.get(provider.adapter_type, (False, "no adapter"))
        if not ready:
            rejected.append(f"{label}: {why}")
            continue
        ok, why = provider_ready.get(provider.id, (True, ""))
        if not ok:
            rejected.append(f"{label}: {why}")
            rate_limited = True
            continue
        for model in provider.models:
            name = f"{label}/{model.model}"
            if task.type not in model.task_types:
                rejected.append(f"{name}: not verified for {task.type.value}")
                continue
            if pin is not None and model.model != pin:
                rejected.append(f"{name}: user pinned {pin}")
                continue
            cost = estimate_cost(task.type, model)
            if (
                spend.budget_micros is not None
                and cost > 0
                and spend.committed_micros + cost > spend.budget_micros
            ):
                rejected.append(f"{name}: estimated {cost} micro-USD exceeds the remaining budget")
                budget_blocked = True
                continue
            options.append((-model.quality, cost, name, provider, model))
    if options:
        options.sort(key=lambda item: (item[0], item[1], item[2]))
        _, cost, name, provider, model = options[0]
        return ModelRoutingDecision(
            task_id=task.id,
            route=RouteKind.PROVIDER,
            provider_id=provider.id,
            model=model.model,
            estimated_cost_micros=cost,
            reasons=[*reasons, f"chose {name}: highest quality rank, then lowest estimated cost"],
            rejected=rejected,
        )
    if deterministic_ok:
        return engine("no permitted AI provider is available; engine tools used")
    if budget_blocked:
        return unavailable(BlockReason.BUDGET, "the batch budget does not cover another paid request")
    if rate_limited:
        return unavailable(BlockReason.RATE_LIMITED, "every capable provider is rate limited; retrying later")
    return unavailable(BlockReason.NO_CAPABLE_WORKER, "no enabled provider is verified for this task")
