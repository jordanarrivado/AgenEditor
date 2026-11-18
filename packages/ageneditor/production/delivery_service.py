"""Hybrid clip approval and batch delivery packaging (spec §15).

Only enumerated clips with an active approval *and* a verified export of exactly that approval are
packaged. Files are copied into a staging folder, re-hashed against the recorded export hashes, described
in a manifest, and the folder is then renamed into place in one step. AgenEditor never uploads, sends or
notifies anyone; the package is a local folder the person delivers themselves.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Sequence
from pathlib import Path

from ageneditor.contracts import (
    BatchStatus,
    ClipStatus,
    DeliveryManifest,
    EngineException,
    ErrorCode,
    FindingState,
)
from ageneditor.contracts.review import DeliveredClip, ManifestFile
from ageneditor.media.hashing import sha256_file
from ageneditor.production.batch_service import check_approval
from ageneditor.production.common import new_id, ts
from ageneditor.production.store import ProductionStore

_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")


def _stem(text: str, fallback: str) -> str:
    return _UNSAFE.sub("_", text).strip("_")[:40] or fallback


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def package_delivery(
    production: ProductionStore,
    batch_id: str,
    clip_ids: Sequence[str],
    authorized_by: str,
    client_name: str | None = None,
) -> DeliveryManifest:
    if not authorized_by.strip():
        raise EngineException(ErrorCode.INVALID_ARGUMENT, "say who authorizes this delivery")
    clip_ids = list(dict.fromkeys(clip_ids))
    if not clip_ids:
        raise EngineException(ErrorCode.INVALID_ARGUMENT, "choose at least one approved clip")
    batch = production.batch(batch_id)
    snapshot = production.snapshot(batch.context_hash)
    clips = {clip.id: clip for clip in production.clips(batch_id)}
    if any(clip_id not in clips for clip_id in clip_ids):
        raise EngineException(ErrorCode.INVALID_ARGUMENT, "a chosen clip is not in this batch")
    deliverable = [clip for clip in clips.values() if clip.status is not ClipStatus.CANCELLED]
    partial = len(clip_ids) < len(deliverable)
    allow_partial = (
        batch.policy.allow_partial_delivery
        if batch.policy.allow_partial_delivery is not None
        else snapshot.delivery.allow_partial_delivery
    )
    if partial and not allow_partial:
        raise EngineException(
            ErrorCode.PERMISSION_DENIED, "this client's delivery rules do not allow partial batches"
        )

    entries = []
    for clip_id in clip_ids:
        clip = clips[clip_id]
        approval = production.active_approval(clip_id)
        if approval is None:
            raise EngineException(ErrorCode.APPROVAL_REQUIRED, f"clip {clip.title!r} is not approved")
        check_approval(production, clip_id, approval.id)
        export = production.export_for(clip_id, approval.id)
        if export is None or clip.status not in {ClipStatus.EXPORTED, ClipStatus.DELIVERED}:
            raise EngineException(
                ErrorCode.APPROVAL_REQUIRED, f"clip {clip.title!r} has no verified export yet"
            )
        plan = production.plan(str(clip.plan_id))
        entries.append((clip, approval, export, plan))

    prefix = snapshot.delivery.naming_prefix or _stem(client_name or "", "Client")
    project_name = _stem(production.project.root.name.rsplit("-", 1)[0], "Project")
    package_name = f"{prefix}_{project_name}_Batch_{batch.sequence:03d}"
    deliveries = production.project.safe_path("deliveries")
    deliveries.mkdir(parents=True, exist_ok=True)
    number = 1 + sum(1 for path in deliveries.iterdir() if path.name.startswith(package_name))
    package_name = f"{package_name}_{number:02d}"
    manifest_id = new_id("delivery")
    staging = production.project.safe_path(f"deliveries/.staging-{manifest_id}")
    final = production.project.safe_path(f"deliveries/{package_name}")
    files: list[ManifestFile] = []
    delivered: list[DeliveredClip] = []
    try:
        for folder in ("videos", "captions", "reports", "project"):
            (staging / package_name / folder).mkdir(parents=True)
        root = staging / package_name

        def add(source: Path, relative: str, expected: str | None) -> None:
            target = root / relative
            shutil.copyfile(source, target)
            digest = sha256_file(target)
            if expected is not None and digest != expected:
                raise EngineException(
                    ErrorCode.DELIVERY_FAILED, f"{relative} does not match its verified export"
                )
            files.append(ManifestFile(path=relative, sha256=digest, size_bytes=target.stat().st_size))

        known_issues = []
        qa_reports = {}
        revisions = {}
        for clip, approval, export, plan in entries:
            stem = plan.delivery_name
            video = f"videos/{stem}.mp4"
            add(production.project.safe_path(export.video_path), video, export.video_sha256)
            captions = None
            if export.captions_path and snapshot.delivery.include_captions:
                captions = f"captions/{stem}.srt"
                add(production.project.safe_path(export.captions_path), captions, export.captions_sha256)
            report = production.project.get_qa_report(export.video_artifact_id)
            qa_reports[stem] = report.model_dump(mode="json")
            known_issues.extend(
                {
                    "clip": stem,
                    "severity": f.severity.value,
                    "category": f.category.value,
                    "evidence": f.evidence,
                    "state": f.state.value,
                }
                for f in production.findings(clip.id, [FindingState.ACCEPTED, FindingState.OPEN])
            )
            revisions[stem] = {
                "clip_id": clip.id,
                "timeline_revision": approval.timeline_revision,
                "approval_id": approval.id,
                "approved_by": approval.approved_by,
                "approved_at": ts(approval.approved_at),
                "plan_id": plan.id,
                "sources": sorted({cut.asset_id for cut in plan.cuts}),
            }
            delivered.append(
                DeliveredClip(
                    clip_id=clip.id,
                    title=clip.title,
                    timeline_revision=approval.timeline_revision,
                    approval_id=approval.id,
                    export_id=export.id,
                    video=video,
                    captions=captions,
                )
            )
        for name, body in (
            ("reports/qa_report.json", qa_reports),
            ("reports/known_issues.json", known_issues),
            ("project/revision_manifest.json", revisions),
        ):
            _write_json(root / name, body)
            files.append(
                ManifestFile(
                    path=name, sha256=sha256_file(root / name), size_bytes=(root / name).stat().st_size
                )
            )
        if snapshot.delivery.include_project_snapshot:
            name = "project/project_snapshot.json"
            _write_json(
                root / name,
                {
                    "note": "References only; media is not included.",
                    "project_id": production.project_id,
                    "batch": batch.model_dump(mode="json"),
                    "clips": {
                        stem: production.clip_timeline(info["clip_id"], info["timeline_revision"]).model_dump(
                            mode="json"
                        )
                        for stem, info in revisions.items()
                    },
                },
            )
            files.append(
                ManifestFile(
                    path=name, sha256=sha256_file(root / name), size_bytes=(root / name).stat().st_size
                )
            )
        manifest = DeliveryManifest(
            id=manifest_id,
            batch_id=batch.id,
            project_id=production.project_id,
            client_id=batch.client_id,
            package_name=package_name,
            partial=partial,
            clips=delivered,
            files=files,
            authorized_by=authorized_by.strip()[:80],
            created_at=production.now(),
        )
        manifest_path = root / "reports" / "delivery_manifest.json"
        manifest_path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
        manifest_sha = sha256_file(manifest_path)
        (root).replace(final)  # one rename publishes the verified package
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    with production.transaction() as db:
        production.insert_manifest(db, manifest, manifest_sha)
        for clip, *_ in entries:
            production.transition_clip(db, clip.id, ClipStatus.DELIVERED, f"packaged in {package_name}")
        remaining = [
            clip
            for clip in production.clips(batch_id, db)
            if clip.status not in {ClipStatus.DELIVERED, ClipStatus.CANCELLED}
        ]
        if not remaining:
            production.set_batch_status(db, production.batch(batch_id, db), BatchStatus.COMPLETED)
    return manifest
