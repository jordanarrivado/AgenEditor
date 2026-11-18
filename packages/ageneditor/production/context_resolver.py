"""Resolve hierarchical instructions into an immutable, content-addressed context snapshot (spec §10).

Precedence: engine invariants (never overridable) → global defaults → client profile → project brief →
batch → clip. Lower layers may change creative defaults unless the client locked a field; a lower layer
that disagrees with a locked field becomes a ``ContextConflict`` that a person resolves.
"""

from __future__ import annotations

from ageneditor.contracts import (
    AssetRole,
    Batch,
    BatchPolicy,
    ContextSnapshot,
    CreativeDefaults,
    EngineException,
    ErrorCode,
    InstructionProfile,
    ProductionClip,
    ProfileScope,
    ResolvedCreative,
)
from ageneditor.contracts.production import ContextConflict, ContextLayer
from ageneditor.production.common import content_hash
from ageneditor.production.workspace import WorkspaceRegistry
from ageneditor.storage import ProjectStore

ENGINE_INVARIANTS = [
    "AgenEditor edits only registered, approved source assets; it never generates footage, images, "
    "voices or music, and never fetches stock media.",
    "Originals are immutable. Every change is a new validated timeline revision.",
    "Every cut, caption word and overlay cites a registered asset ID and source range.",
    "Report only renders, measurements and QA results that actually happened.",
    "Stay inside this task's scope: one project and, where given, one clip. No other client's context.",
]

_CREATIVE_FIELDS = tuple(CreativeDefaults.model_fields)


def _apply(
    resolved: dict[str, object],
    owner: dict[str, str],
    layer_name: str,
    creative: CreativeDefaults,
    locked: dict[str, str],
    conflicts: list[ContextConflict],
) -> None:
    for field in _CREATIVE_FIELDS:
        value = getattr(creative, field)
        if value is None:
            continue
        value = value.value if hasattr(value, "value") else value
        if field in locked and resolved.get(field) != value:
            conflicts.append(
                ContextConflict(
                    field=field,
                    message=f"{layer_name} sets {field} to {value!r}, but the client locked it to "
                    f"{resolved.get(field)!r}",
                    layers=[locked[field], layer_name],
                )
            )
            continue
        resolved[field] = value
        owner[field] = layer_name


def scoped_source_hashes(store: ProjectStore, policy: BatchPolicy) -> dict[str, str]:
    active = store.active_assets()
    if policy.source_asset_ids:
        missing = [asset_id for asset_id in policy.source_asset_ids if asset_id not in active]
        if missing:
            raise EngineException(
                ErrorCode.ASSET_NOT_FOUND, "batch cites a source that is not an active project asset"
            )
        chosen = [active[asset_id] for asset_id in policy.source_asset_ids]
    else:
        chosen = [asset for asset in active.values() if asset.role is AssetRole.PRIMARY]
    if any(asset.role is not AssetRole.PRIMARY or asset.video is None for asset in chosen):
        raise EngineException(ErrorCode.INVALID_ARGUMENT, "batch sources must be primary video assets")
    return {asset.id: asset.sha256 for asset in sorted(chosen, key=lambda item: item.id)}


def resolve_context(
    registry: WorkspaceRegistry,
    store: ProjectStore,
    client_id: str | None,
    policy: BatchPolicy,
    batch_instructions: str,
    batch_id: str = "new",
    clip: ProductionClip | None = None,
    profile_revisions: dict[str, int] | None = None,
) -> ContextSnapshot:
    """Build the snapshot. ``profile_revisions`` pins the profile revisions an earlier snapshot used."""
    pins = profile_revisions or {}
    global_profile = (
        registry.profile_revision(ProfileScope.GLOBAL, None, pins["global"])
        if pins.get("global")
        else registry.profile(ProfileScope.GLOBAL)
    )
    client_profile: InstructionProfile | None = None
    if client_id is not None:
        client_profile = (
            registry.profile_revision(ProfileScope.CLIENT, client_id, pins["client"])
            if pins.get("client")
            else registry.profile(ProfileScope.CLIENT, client_id)
        )
    brief = store.brief()

    resolved: dict[str, object] = ResolvedCreative().model_dump(mode="json")
    owner: dict[str, str] = {}
    locked: dict[str, str] = {}
    conflicts: list[ContextConflict] = []
    _apply(resolved, owner, "global", global_profile.creative, locked, conflicts)
    if client_profile is not None:
        _apply(resolved, owner, "client", client_profile.creative, locked, conflicts)
        locked = {field: "client" for field in client_profile.locked_creative}
    batch_creative = policy.creative
    if policy.platform and batch_creative.platform is None:
        batch_creative = batch_creative.model_copy(update={"platform": policy.platform})
    _apply(resolved, owner, "batch", batch_creative, locked, conflicts)
    if clip is not None:
        _apply(resolved, owner, "clip", clip.creative, locked, conflicts)

    layers = [
        ContextLayer(
            scope="engine", source_id="invariants", revision=1, content_hash=content_hash(ENGINE_INVARIANTS)
        ),
        ContextLayer(
            scope="global",
            source_id="global",
            revision=global_profile.revision,
            content_hash=content_hash(global_profile),
        ),
    ]
    instructions: list[tuple[str, str]] = []
    if global_profile.instructions:
        instructions.append(("global", global_profile.instructions))
    if client_profile is not None:
        layers.append(
            ContextLayer(
                scope="client",
                source_id=str(client_id),
                revision=client_profile.revision,
                content_hash=content_hash(client_profile),
            )
        )
        if client_profile.instructions:
            instructions.append(("client", client_profile.instructions))
    layers.append(
        ContextLayer(
            scope="project",
            source_id=store.project_id,
            revision=brief.version,
            content_hash=content_hash(brief),
        )
    )
    project_text = "\n\n".join(
        part
        for part in (
            brief.brief_text and f"Brief:\n{brief.brief_text}",
            brief.script_text and f"Script:\n{brief.script_text}",
            brief.reference_note and f"Reference note (not fetched): {brief.reference_note}",
        )
        if part
    )
    if project_text:
        instructions.append(("project", project_text[:20000]))
    layers.append(
        ContextLayer(
            scope="batch",
            source_id=batch_id,
            revision=1,
            content_hash=content_hash({"policy": policy, "instructions": batch_instructions}),
        )
    )
    if batch_instructions:
        instructions.append(("batch", batch_instructions))
    if clip is not None:
        layers.append(
            ContextLayer(
                scope="clip",
                source_id=clip.id,
                revision=1,
                content_hash=content_hash({"instructions": clip.instructions, "creative": clip.creative}),
            )
        )
        if clip.instructions:
            instructions.append(("clip", clip.instructions))

    required = list(client_profile.required_constraints) if client_profile else []
    required += [f"Brief requirement: {item.text}" for item in brief.checklist if item.status != "waived"]
    observations = list(global_profile.style_observations)
    if client_profile is not None:
        observations += client_profile.style_observations
    delivery = (client_profile or global_profile).delivery
    body = {
        "client_id": client_id,
        "project_id": store.project_id,
        "layers": [layer.model_dump(mode="json") for layer in layers],
        "creative": ResolvedCreative.model_validate(resolved).model_dump(mode="json"),
        "instructions": instructions,
        "required_constraints": required[:60],
        "engine_invariants": ENGINE_INVARIANTS,
        "style_observations": [item.model_dump(mode="json") for item in observations[:50]],
        "delivery": delivery.model_dump(mode="json"),
        "conflicts": [item.model_dump(mode="json") for item in conflicts],
        "source_asset_hashes": scoped_source_hashes(store, policy),
        "policy_hash": content_hash(policy),
    }
    return ContextSnapshot.model_validate({**body, "hash": content_hash(body)})


def batch_context(
    registry: WorkspaceRegistry,
    store: ProjectStore,
    batch: Batch,
    snapshot: ContextSnapshot,
    clip: ProductionClip | None = None,
) -> ContextSnapshot:
    """Re-resolve a batch (optionally clip) context pinned to the profile revisions of ``snapshot``."""
    pins = {layer.scope: layer.revision for layer in snapshot.layers if layer.scope in {"global", "client"}}
    return resolve_context(
        registry, store, batch.client_id, batch.policy, batch.instructions, batch.id, clip, pins
    )
