"""Delivery of a project's finals (roadmap Phase 2c): client file names and a known-issues note.

Everything in the note comes from the project's own records: the known-issues log (files the person accepted
despite a problem), the technical and editorial QA of each final render, the preview check of the matching
preview, and the AI-generated (external) labels from QA. Nothing is measured or judged here; a render that has
no QA report, or failed it, is left out with the reason. The native shell saves the files and the note into a
new folder outside the workspace; AgenEditor never sends them anywhere.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from fractions import Fraction

from ageneditor.contracts import (
    CheckKind,
    CheckStatus,
    DeliveryFile,
    EngineException,
    ErrorCode,
    ExcludedRender,
    GeneratedMaterial,
    Job,
    JobKind,
    JobStatus,
    KnownIssue,
    ProjectDeliveryPlan,
    QACheck,
    QAReport,
    Timeline,
)
from ageneditor.storage import ProjectStore

MAX_FILES = 25
_UNSAFE = re.compile(r"[^A-Za-z0-9]+")
_SOURCE_TITLES = {
    "accepted": "Accepted by the editor",
    "technical_qa": "Technical check",
    "editorial_qa": "Editorial flags",
    "preview_check": "Preview check",
}


def name_part(text: str, fallback: str, limit: int = 32) -> str:
    """Words of ``text`` joined in CamelCase-ish form: ASCII letters and digits only, never empty."""
    words = [word for word in _UNSAFE.split(text) if word]
    joined = "".join(word[:1].upper() + word[1:] for word in words)[:limit]
    return joined or fallback


def aspect_label(width: int, height: int) -> str:
    """``9x16`` for 1080x1920; the raw size when the ratio does not reduce to small numbers."""
    divisor = math.gcd(width, height)
    w, h = width // divisor, height // divisor
    return f"{w}x{h}" if max(w, h) <= 32 else f"{width}x{height}"


def seconds_label(frames: int, timeline: Timeline) -> str:
    rate = timeline.render_profile.frame_rate
    seconds = Fraction(frames * rate.den, rate.num)
    return f"{max(1, math.floor(seconds + Fraction(1, 2)))}s"  # half up: 2.5 s is "3s"


def _project_name(store: ProjectStore) -> str:
    with store._connect() as db:
        return str(db.execute("SELECT name FROM project").fetchone()[0])


def _preview_for(store: ProjectStore, job: Job) -> Job | None:
    """The newest successful preview of exactly the same edit (revision, variant, variant revision)."""
    with store._connect() as db:
        rows = db.execute("SELECT data FROM jobs ORDER BY rowid DESC").fetchall()
    for row in rows:
        other = Job.model_validate(json.loads(row[0]))
        if (
            other.kind is JobKind.PREVIEW_RENDER
            and other.status is JobStatus.SUCCEEDED
            and (other.timeline_revision, other.variant_id, other.variant_revision)
            == (job.timeline_revision, job.variant_id, job.variant_revision)
        ):
            return other
    return None


def _check_line(check: QACheck) -> str:
    detail = []
    if check.expected:
        detail.append(f"expected {check.expected}")
    if check.measured:
        detail.append(f"measured {check.measured}")
    status = "could not run" if check.status is CheckStatus.SKIPPED else check.status.value
    text = f"{check.check_id} ({status})"
    if check.message:
        text += f": {check.message}"
    if detail:
        text += f" [{'; '.join(detail)}]"
    return text[:1200]


def delivery_plan(
    store: ProjectStore, job_ids: Sequence[str], client: str | None = None
) -> ProjectDeliveryPlan:
    from ageneditor.media.preview_qa import get_preview_qa, job_timeline
    from ageneditor.variants import VariantService

    job_ids = list(dict.fromkeys(str(job_id) for job_id in job_ids))
    if not job_ids or len(job_ids) > MAX_FILES:
        raise EngineException(
            ErrorCode.INVALID_ARGUMENT, f"choose between 1 and {MAX_FILES} final renders to deliver"
        )
    if client is not None:
        client = client.strip()
        if len(client) > 60:
            raise EngineException(ErrorCode.INVALID_ARGUMENT, "client name must be at most 60 characters")
        store.set_setting("client_name", client or None)
    else:
        client = store.setting("client_name") or ""
    project = _project_name(store)
    prefix = "_".join(part for part in (name_part(client, ""), name_part(project, "Project")) if part)

    files: list[DeliveryFile] = []
    excluded: list[ExcludedRender] = []
    used_by: dict[str, set[str]] = {}  # asset ID -> delivery file names
    gathered: list[tuple[str, str, str]] = []  # (source, file name, message)
    generated: dict[str, GeneratedMaterial] = {}
    taken: set[str] = set()
    variants = VariantService(store)

    for job_id in job_ids:
        try:
            job = store.get_job(job_id)
        except EngineException:
            excluded.append(ExcludedRender(job_id=job_id, reason="this render does not exist"))
            continue
        if job.kind is not JobKind.FINAL_RENDER:
            excluded.append(ExcludedRender(job_id=job_id, reason="only final renders are delivered"))
            continue
        if job.status is not JobStatus.SUCCEEDED or not job.output_path:
            excluded.append(ExcludedRender(job_id=job_id, reason="the render did not finish"))
            continue
        try:
            report: QAReport = store.get_qa_report(job.id)
        except EngineException:
            excluded.append(ExcludedRender(job_id=job_id, reason="run the technical check first"))
            continue
        if report.overall is CheckStatus.FAIL:
            excluded.append(ExcludedRender(job_id=job_id, reason="the technical check failed"))
            continue
        try:
            timeline = job_timeline(store, job)
        except EngineException as exc:
            excluded.append(ExcludedRender(job_id=job_id, reason=exc.error.message[:300]))
            continue

        if job.variant_id is None:
            label, part, revision_tag = "Main edit", "Main", f"r{job.timeline_revision}"
        else:
            variant = variants.get(job.variant_id, job.variant_revision)
            label, part = variant.name, name_part(variant.name, "Variant")
            revision_tag = f"r{job.timeline_revision}v{job.variant_revision}"
        profile = timeline.render_profile
        stem = "_".join(
            (
                prefix,
                part,
                aspect_label(profile.width, profile.height),
                seconds_label(timeline.duration_frames, timeline),
                revision_tag,
            )
        )
        name, n = stem, 2
        while name.lower() in taken:
            name, n = f"{stem}_{n}", n + 1
        taken.add(name.lower())
        output = store.safe_path(job.output_path)
        files.append(
            DeliveryFile(
                job_id=job.id,
                name=name,
                label=label,
                timeline_revision=job.timeline_revision or 0,
                variant_id=job.variant_id,
                variant_revision=job.variant_revision,
                duration_frames=timeline.duration_frames,
                captions=output.with_name("captions.srt").is_file(),
            )
        )
        for clip in timeline.clips:
            used_by.setdefault(clip.asset_id, set()).add(name)
        for check in report.checks:
            if check.status is CheckStatus.PASS:
                continue
            source = "technical_qa" if check.kind is CheckKind.TECHNICAL else "editorial_qa"
            gathered.append((source, name, _check_line(check)))
        preview = _preview_for(store, job)
        preview_report = get_preview_qa(store, preview.id) if preview else None
        if preview_report is not None:
            for check in preview_report.checks:
                if check.status is CheckStatus.WARN:
                    gathered.append(("preview_check", name, _check_line(check)))
        for item in report.generated_assets:
            entry = generated.setdefault(
                item.asset_id,
                GeneratedMaterial(
                    asset_id=item.asset_id,
                    original_filename=item.original_filename,
                    tool_name=item.tool_name,
                    files=[name],
                ),
            )
            if name not in entry.files:
                entry.files.append(name)

    if not files:
        reasons = "; ".join(f"{item.job_id}: {item.reason}" for item in excluded)
        raise EngineException(ErrorCode.QA_FAILED, f"no final render can be delivered ({reasons})")

    assets = store.assets()
    checklist = store.asset_checklist()
    for issue in checklist.accepted.values():
        concerned = [item.name for item in files if item.name in used_by.get(issue.asset_id, ())]
        if not concerned:
            continue  # accepted for a file none of these renders use
        asset = assets.get(issue.asset_id)
        filename = asset.original_filename if asset else issue.asset_id
        for name in concerned:
            gathered.append(("accepted", name, f"{filename}: {issue.note}"[:1200]))

    merged: dict[tuple[str, str], list[str]] = {}
    order = list(_SOURCE_TITLES)
    for source, name, message in gathered:
        names = merged.setdefault((source, message), [])
        if name not in names:
            names.append(name)
    issues = [
        KnownIssue(source=source, files=names, message=message)  # type: ignore[arg-type]
        for (source, message), names in sorted(merged.items(), key=lambda item: order.index(item[0][0]))
    ]
    created = datetime.now(UTC)
    folder = f"{prefix}_delivery_{created:%Y%m%d}"
    plan = ProjectDeliveryPlan(
        project_id=store.project_id,
        folder=folder,
        files=files,
        excluded=excluded,
        issues=issues,
        generated=sorted(generated.values(), key=lambda item: item.original_filename),
        note="",
        created_at=created,
    )
    return plan.model_copy(update={"note": render_note(plan, project, client)})


def render_note(plan: ProjectDeliveryPlan, project: str, client: str) -> str:
    """The known-issues note as Markdown, readable as plain text. It is written for the client, so renders
    left out are reported to the person in the app, not here."""
    names = {item.name for item in plan.files}
    everyone = len(names)

    def concerns(files: list[str]) -> str:
        return "all files" if everyone > 1 and len(files) == everyone else ", ".join(files)

    lines = [f"# Delivery notes: {project}", ""]
    if client:
        lines += [f"For: {client}", ""]
    lines += [
        f"Prepared by AgenEditor on {plan.created_at:%Y-%m-%d %H:%M} UTC. This note lists only what was "
        "recorded in the project (accepted known issues, automatic checks and labels); it is not a full "
        "review of the content.",
        "",
        "## Files",
        "",
    ]
    for item in plan.files:
        variant = (
            f", variant revision {item.variant_revision} on main revision {item.timeline_revision}"
            if item.variant_id
            else f", revision {item.timeline_revision}"
        )
        captions = f"; captions in {item.name}.srt" if item.captions else ""
        lines.append(f"- {item.name}.mp4: {item.label}{variant}{captions}")
    lines += ["", "## Known issues", ""]
    if not plan.issues:
        lines.append("No known issues were recorded for these files.")
    for source, title in _SOURCE_TITLES.items():
        group = [issue for issue in plan.issues if issue.source == source]
        if not group:
            continue
        lines += [f"### {title}", ""]
        lines += [f"- {issue.message} ({concerns(issue.files)})" for issue in group]
        lines.append("")
    if plan.generated:
        lines += ["## AI-generated (external) material", ""]
        lines += [
            f"- {item.original_filename}: made with {item.tool_name} outside AgenEditor "
            f"({concerns(item.files)})"
            for item in plan.generated
        ]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
