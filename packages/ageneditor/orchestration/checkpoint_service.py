"""Checkpoint fingerprints and reuse (spec §13).

A checkpoint binds a verified result to the hashes of everything it depends on. It is reused only when
the fingerprint matches exactly *and* its artifact still exists with the recorded SHA-256; otherwise it is
invalidated and the task runs again. Edit passes are not reused from checkpoints (their durable success
already survives restarts); analysis, renders, technical QA and exports are.
"""

from __future__ import annotations

from typing import Any

from ageneditor.contracts import CheckpointDescriptor, EngineException, Task, TaskType, Timeline
from ageneditor.media.hashing import sha256_file
from ageneditor.production.common import content_hash, new_id
from ageneditor.production.store import ProductionStore

#: Bump when a handler's output for the same inputs would change.
HANDLER_VERSION = "2.0.0"
REUSABLE = frozenset(
    {TaskType.ANALYZE_SOURCE, TaskType.RENDER_PREVIEW, TaskType.TECHNICAL_QA, TaskType.EXPORT}
)


def timeline_content_hash(timeline: Timeline) -> str:
    """Hash of what renders: excludes revision numbers and provenance timestamps."""
    data = timeline.model_dump(mode="json", exclude={"revision", "parent_revision", "provenance"})
    return content_hash(data)


def fingerprint(
    production: ProductionStore, task: Task, extra: dict[str, Any] | None = None
) -> tuple[str, str]:
    """Return ``(scope_key, input_fingerprint)`` for a task about to run."""
    body: dict[str, Any] = {"type": task.type.value, "version": HANDLER_VERSION, "params": task.params}
    scope = f"batch:{task.batch_id}"
    if task.type is TaskType.ANALYZE_SOURCE:
        asset = production.project.assets()[task.params["asset_id"]]
        scope = f"asset:{asset.id}"
        body["source_sha256"] = asset.sha256
    elif task.clip_id is not None:
        scope = f"clip:{task.clip_id}"
        clip = production.clip(task.clip_id)
        if clip.plan_id is not None:
            body["plan_hash"] = production.plan_hash(clip.plan_id)
        revision = (
            clip.draft_revision
            if task.type in {TaskType.RENDER_PREVIEW, TaskType.TECHNICAL_QA, TaskType.EXPORT}
            else clip.current_revision
        )
        if revision is not None:
            body["timeline"] = timeline_content_hash(production.clip_timeline(task.clip_id, revision))
    if extra:
        body.update(extra)
    return scope, content_hash(body)


def reusable_checkpoint(
    production: ProductionStore, task: Task, scope: str, fp: str
) -> CheckpointDescriptor | None:
    if task.type not in REUSABLE:
        return None
    checkpoint = production.find_checkpoint(task.type.value, scope, fp)
    if checkpoint is None:
        return None
    if checkpoint.artifact_id is not None:
        try:
            artifact = production.artifact(checkpoint.artifact_id)
            path = production.project.safe_path(artifact.relative_path)
            intact = path.is_file() and sha256_file(path) == artifact.sha256
        except (EngineException, OSError):
            intact = False
        if not intact:  # missing, unreadable or changed: never reuse
            production.invalidate_checkpoint(checkpoint.id, "artifact missing or changed")
            return None
    return checkpoint


def make_checkpoint(
    task: Task, attempt_id: str | None, scope: str, fp: str, result: dict[str, Any], artifact_id: str | None
) -> CheckpointDescriptor:
    return CheckpointDescriptor(
        id=new_id("ckpt"),
        task_type=task.type,
        scope_key=scope,
        input_fingerprint=fp,
        task_id=task.id,
        attempt_id=attempt_id,
        artifact_id=artifact_id,
        result=result,
    )
