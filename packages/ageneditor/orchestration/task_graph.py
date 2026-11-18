"""Compose and validate per-clip task DAGs (spec §7).

Graphs are composed from each clip's plan: an optional enhancement pass only appears when the plan asks
for one, so no dependency ever dangles. Revision rounds append a new acyclic sub-graph (a higher
``cycle``) instead of looping back, which keeps the stored graph a DAG.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from ageneditor.contracts import (
    WRITER_TASKS,
    Batch,
    ClipPlan,
    ClipTaskGraph,
    EngineException,
    ErrorCode,
    GraphNode,
    Task,
    TaskStatus,
    TaskType,
)
from ageneditor.production.common import new_id
from ageneditor.production.store import ProductionStore

#: How many times a task may be attempted before it fails (transient errors only are retried).
MAX_ATTEMPTS: dict[TaskType, int] = {
    TaskType.ANALYZE_SOURCE: 2,
    TaskType.RENDER_PREVIEW: 2,
    TaskType.EXPORT: 2,
    TaskType.TECHNICAL_QA: 2,
}


def validate_graph(graph: ClipTaskGraph, external: Sequence[str] = ()) -> list[str]:
    """Return the nodes in a dependency-respecting order; raise on duplicates, dangling edges or cycles."""
    keys = [node.key for node in graph.nodes]
    if len(keys) != len(set(keys)):
        raise EngineException(ErrorCode.INVALID_ARGUMENT, "task graph has duplicate node keys")
    known = set(keys) | set(external)
    for node in graph.nodes:
        missing = [dep for dep in node.depends_on if dep not in known]
        if missing:
            raise EngineException(
                ErrorCode.INVALID_ARGUMENT, f"task {node.key} depends on unknown {missing[0]}"
            )
        if node.key in node.depends_on:
            raise EngineException(ErrorCode.INVALID_ARGUMENT, f"task {node.key} depends on itself")
    indegree = {node.key: sum(dep in keys for dep in node.depends_on) for node in graph.nodes}
    children: dict[str, list[str]] = {key: [] for key in keys}
    for node in graph.nodes:
        for dep in node.depends_on:
            if dep in children:
                children[dep].append(node.key)
    order: list[str] = []
    ready = sorted(key for key, degree in indegree.items() if degree == 0)
    while ready:
        key = ready.pop(0)
        order.append(key)
        for child in children[key]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
        ready.sort()
    if len(order) != len(keys):
        raise EngineException(ErrorCode.INVALID_ARGUMENT, "task graph contains a cycle")
    return order


def _tail(nodes: list[GraphNode], after: str, editorial_qa: bool) -> None:
    nodes.append(GraphNode(key="assemble", type=TaskType.ASSEMBLE_DRAFT, depends_on=[after]))
    nodes.append(GraphNode(key="render", type=TaskType.RENDER_PREVIEW, depends_on=["assemble"]))
    nodes.append(GraphNode(key="technical_qa", type=TaskType.TECHNICAL_QA, depends_on=["render"]))
    if editorial_qa:
        nodes.append(GraphNode(key="editorial_qa", type=TaskType.EDITORIAL_QA, depends_on=["technical_qa"]))


def production_graph(clip_id: str, plan: ClipPlan, editorial_qa: bool) -> ClipTaskGraph:
    """First editing round for a clip with a validated plan."""
    nodes = [
        GraphNode(key="structural", type=TaskType.STRUCTURAL_EDIT, params={"plan_id": plan.id}),
        GraphNode(key="captions", type=TaskType.CAPTIONS, depends_on=["structural"]),
        GraphNode(key="visual", type=TaskType.VISUAL_POLISH, depends_on=["structural"]),
        GraphNode(key="audio", type=TaskType.AUDIO_POLISH, depends_on=["structural"]),
    ]
    merge = ["captions", "visual", "audio"]
    if plan.conditional_passes:
        nodes.append(
            GraphNode(
                key="enhancement",
                type=TaskType.ENHANCEMENT,
                depends_on=["visual"],
                params={"passes": [item.value for item in plan.conditional_passes]},
            )
        )
        merge = ["captions", "audio", "enhancement"]
    nodes.append(
        GraphNode(key="merge", type=TaskType.ASSEMBLE_DRAFT, depends_on=merge, params={"merge": True})
    )
    nodes.append(GraphNode(key="render", type=TaskType.RENDER_PREVIEW, depends_on=["merge"]))
    nodes.append(GraphNode(key="technical_qa", type=TaskType.TECHNICAL_QA, depends_on=["render"]))
    if editorial_qa:
        nodes.append(GraphNode(key="editorial_qa", type=TaskType.EDITORIAL_QA, depends_on=["technical_qa"]))
    graph = ClipTaskGraph(clip_id=clip_id, cycle=0, nodes=nodes)
    validate_graph(graph)
    return graph


def revision_graph(clip_id: str, cycle: int, finding_ids: Sequence[str], editorial_qa: bool) -> ClipTaskGraph:
    """A targeted revision round: fix the listed findings, then re-check everything downstream."""
    nodes = [GraphNode(key="revision", type=TaskType.REVISION, params={"finding_ids": list(finding_ids)})]
    _tail(nodes, "revision", editorial_qa)
    graph = ClipTaskGraph(clip_id=clip_id, cycle=cycle, nodes=nodes)
    validate_graph(graph)
    return graph


def materialize(
    production: ProductionStore,
    db: sqlite3.Connection,
    batch: Batch,
    scope_id: str,
    clip_id: str | None,
    graph: ClipTaskGraph,
    external: Mapping[str, Sequence[str]] | None = None,
    initial_status: TaskStatus = TaskStatus.CREATED,
) -> dict[str, str]:
    """Insert a validated graph. ``external`` maps node keys to already-stored task IDs they wait for.

    Idempotent: re-materializing the same graph returns the existing task IDs.
    """
    external = external or {}
    order = validate_graph(graph)
    by_key = {node.key: node for node in graph.nodes}
    ids: dict[str, str] = {}
    for key in order:
        node = by_key[key]
        params: dict[str, Any] = dict(node.params)
        task = Task(
            id=new_id("task"),
            project_id=production.project_id,
            batch_id=batch.id,
            clip_id=clip_id,
            type=node.type,
            cycle=graph.cycle,
            status=initial_status,
            priority=batch.policy.priority,
            writer=node.type in WRITER_TASKS,
            max_attempts=MAX_ATTEMPTS.get(node.type, 3),
            params=params,
            created_at=production.now(),
            updated_at=production.now(),
        )
        depends = [ids[dep] for dep in node.depends_on if dep in ids] + list(external.get(key, ()))
        ids[key] = production.insert_task(db, task, f"{scope_id}:{graph.cycle}:{key}", depends)
    return ids
