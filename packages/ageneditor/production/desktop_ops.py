"""Typed desktop IPC operations for the Production Command Center (spec §16, §17).

These are human-authorized operations (selection, approval, delivery, pause/cancel, provider and budget
settings). They are reachable only through the authenticated desktop endpoint, never as agent tools.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ageneditor.contracts import (
    BatchCreateRequest,
    ClipStatus,
    CreativeDefaults,
    EngineException,
    ErrorCode,
    InstructionProfile,
    ProfileScope,
    ResourceConfig,
    TaskStatus,
)
from ageneditor.orchestration.orchestrator import ProductionOrchestrator
from ageneditor.production.batch_service import ProductionService

PRODUCTION_OPERATIONS = frozenset(
    {
        "production_overview",
        "production_list_clients",
        "production_create_client",
        "production_bind_project",
        "production_get_profile",
        "production_save_profile",
        "production_list_providers",
        "production_set_provider_enabled",
        "production_get_resources",
        "production_save_resources",
        "production_create_batch",
        "production_start_discovery",
        "production_confirm_selection",
        "production_set_paused",
        "production_cancel_batch",
        "production_status",
        "production_events",
        "production_clip_detail",
        "production_set_clip_instructions",
        "production_approve",
        "production_request_revision",
        "production_accept_findings",
        "production_retry_task",
        "production_package_delivery",
        "production_costs",
    }
)


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise EngineException(ErrorCode.INVALID_ARGUMENT, "expected a list of IDs")
    return value


def production_call(orchestrator: ProductionOrchestrator, operation: str, args: dict[str, Any]) -> Any:
    service = ProductionService(orchestrator)
    registry = orchestrator.registry
    if operation == "production_overview":
        batches = []
        for production in orchestrator.all_productions():
            for batch in production.batches():
                clips = production.clips(batch.id)
                counts: dict[str, int] = {}
                for clip in clips:
                    counts[clip.status.value] = counts.get(clip.status.value, 0) + 1
                tasks = production.tasks(batch_id=batch.id)
                batches.append(
                    {
                        "batch": batch.model_dump(mode="json"),
                        "project_name": production.project.root.name,
                        "clip_counts": counts,
                        "clips_total": len(clips),
                        "attention": sum(
                            c.status
                            in {
                                ClipStatus.NEEDS_HUMAN,
                                ClipStatus.FAILED,
                                ClipStatus.BLOCKED,
                                ClipStatus.WAITING_FOR_ASSETS,
                            }
                            for c in clips
                        ),
                        "review_ready": counts.get(ClipStatus.REVIEW_READY.value, 0),
                        "tasks_active": sum(
                            t.status in {TaskStatus.LEASED, TaskStatus.RUNNING} for t in tasks
                        ),
                        "tasks_waiting": sum(
                            t.status in {TaskStatus.READY, TaskStatus.BLOCKED, TaskStatus.RETRY_WAIT}
                            for t in tasks
                        ),
                    }
                )
        return {
            "batches": sorted(batches, key=lambda item: item["batch"]["created_at"], reverse=True),
            "running": orchestrator.running(),
            "teams": orchestrator.teams.active(),
            "resources": orchestrator.resources.snapshot().model_dump(mode="json"),
        }
    if operation == "production_list_clients":
        return [client.model_dump(mode="json") for client in registry.clients()]
    if operation == "production_create_client":
        return registry.create_client(str(args["name"])).model_dump(mode="json")
    if operation == "production_bind_project":
        production = orchestrator.production(str(args["project_id"]))
        registry.bind_project(production.project_id, str(args["client_id"]), production.has_batches())
        return {"project_id": production.project_id, "client_id": str(args["client_id"])}
    if operation == "production_get_profile":
        scope = ProfileScope(str(args.get("scope", "global")))
        return registry.profile(scope, args.get("client_id")).model_dump(mode="json")
    if operation == "production_save_profile":
        profile = InstructionProfile.model_validate(args["profile"])
        return registry.save_profile(profile, int(args["expected_revision"])).model_dump(mode="json")
    if operation == "production_list_providers":
        ready = orchestrator._adapter_ready()
        return [
            {
                **provider.model_dump(mode="json"),
                "adapter_ready": ready.get(provider.adapter_type, (False, "no adapter"))[0],
                "adapter_reason": ready.get(provider.adapter_type, (False, "no adapter"))[1],
            }
            for provider in registry.providers()
        ]
    if operation == "production_set_provider_enabled":
        provider = registry.set_provider_enabled(str(args["provider_id"]), bool(args["enabled"]))
        orchestrator.resources.configure(orchestrator.config, registry.providers())
        return provider.model_dump(mode="json")
    if operation == "production_get_resources":
        return {
            "config": registry.resource_config().model_dump(mode="json"),
            "snapshot": orchestrator.resources.snapshot().model_dump(mode="json"),
        }
    if operation == "production_save_resources":
        config = registry.save_resource_config(ResourceConfig.model_validate(args["config"]))
        orchestrator.config = config
        orchestrator.resources.configure(config, registry.providers())
        return config.model_dump(mode="json")
    if operation == "production_create_batch":
        return service.create_batch(BatchCreateRequest.model_validate(args["request"]))
    if operation == "production_start_discovery":
        return service.start_discovery(str(args["batch_id"])).model_dump(mode="json")
    if operation == "production_confirm_selection":
        clips = service.confirm_selection(
            str(args["batch_id"]), _str_list(args["candidate_ids"]), str(args.get("confirmed_by") or "person")
        )
        return [clip.model_dump(mode="json") for clip in clips]
    if operation == "production_set_paused":
        return service.set_paused(str(args["batch_id"]), bool(args["paused"])).model_dump(mode="json")
    if operation == "production_cancel_batch":
        return service.cancel(str(args["batch_id"])).model_dump(mode="json")
    if operation == "production_status":
        return service.status(str(args["batch_id"]))
    if operation == "production_events":
        production = orchestrator.production(str(args["project_id"]))
        return [
            event.model_dump(mode="json")
            for event in production.events(
                int(args.get("after_seq", 0)), int(args.get("limit", 200)), args.get("aggregate_id")
            )
        ]
    if operation == "production_clip_detail":
        clip_id = str(args["clip_id"])
        production = orchestrator.production_for_clip(clip_id)
        clip = production.clip(clip_id)
        artifacts = []
        with production.connect() as db:
            rows = db.execute(
                "SELECT data FROM artifacts WHERE clip_id=? ORDER BY rowid DESC", (clip_id,)
            ).fetchall()
        from ageneditor.contracts import Artifact

        for row in rows:
            artifact = Artifact.model_validate_json(row[0])
            artifacts.append(
                {
                    **artifact.model_dump(mode="json"),
                    "path": str(production.project.safe_path(artifact.relative_path)),
                }
            )
        return {
            "clip": clip.model_dump(mode="json"),
            "plan": production.plan(clip.plan_id).model_dump(mode="json") if clip.plan_id else None,
            "revisions": production.clip_revisions(clip_id),
            "timeline": production.clip_timeline(clip_id).model_dump(mode="json")
            if clip.current_revision is not None
            else None,
            "reviews": [review.model_dump(mode="json") for review in production.reviews(clip_id)],
            "findings": [finding.model_dump(mode="json") for finding in production.findings(clip_id)],
            "approvals": [approval.model_dump(mode="json") for approval in production.approvals(clip_id)],
            "artifacts": artifacts,
            "tasks": [task.model_dump(mode="json") for task in production.tasks(clip_id=clip_id)],
        }
    if operation == "production_set_clip_instructions":
        return service.set_clip_instructions(
            str(args["clip_id"]),
            str(args.get("instructions", "")),
            CreativeDefaults.model_validate(args.get("creative", {})),
        ).model_dump(mode="json")
    if operation == "production_approve":
        approvals = service.approve(
            _str_list(args["clip_ids"]), str(args.get("approved_by", "")), str(args.get("note", ""))
        )
        return [approval.model_dump(mode="json") for approval in approvals]
    if operation == "production_request_revision":
        return {
            "task_ids": service.request_revision(
                str(args["clip_id"]), str(args["note"]), str(args.get("requested_by") or "person")
            )
        }
    if operation == "production_accept_findings":
        return service.accept_needs_human(
            str(args["clip_id"]), str(args.get("decided_by") or "person"), str(args.get("note", ""))
        ).model_dump(mode="json")
    if operation == "production_retry_task":
        return service.retry_task(str(args["task_id"]))
    if operation == "production_package_delivery":
        from ageneditor.production.delivery_service import package_delivery

        batch_id = str(args["batch_id"])
        production = orchestrator.production_for_batch(batch_id)
        batch = production.batch(batch_id)
        client_name = registry.client(batch.client_id).name if batch.client_id else None
        manifest = package_delivery(
            production, batch_id, _str_list(args["clip_ids"]), str(args.get("authorized_by", "")), client_name
        )
        folder = production.project.safe_path(f"deliveries/{manifest.package_name}")
        return {**manifest.model_dump(mode="json"), "folder": str(Path(folder))}
    if operation == "production_costs":
        rows = []
        for production in orchestrator.all_productions():
            for item in production.usage(args.get("batch_id")):
                rows.append({**item, "project_id": production.project_id})
        return rows
    raise EngineException(ErrorCode.INVALID_ARGUMENT, "unknown production operation")
