"""Typed MCP stdio adapter. Business rules remain in ProjectStore and engine services."""

from __future__ import annotations

import asyncio
from pathlib import Path
from threading import RLock
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult

from ageneditor.broll import METHOD as BROLL_METHOD
from ageneditor.broll import suggest_broll as suggest_broll_service
from ageneditor.captions import to_srt
from ageneditor.contracts import (
    AssetProvenance,
    AssetRole,
    CandidateProposal,
    ClipPlanDraft,
    EditPlan,
    EngineError,
    EngineException,
    ErrorCode,
    JobKind,
    ModelPreset,
    QAFindingSubmission,
    RenderProfile,
    RevisionOp,
    RevisionPatch,
    ShotLabel,
    Timeline,
    VariantKind,
    VariantOverrides,
)
from ageneditor.jobs import JobManager
from ageneditor.media.preview_qa import run_preview_qa as run_preview_qa_service
from ageneditor.media.qa import run_qa as run_qa_service
from ageneditor.planning import candidate_hooks
from ageneditor.planning import submit_edit_plan as submit_edit_plan_service
from ageneditor.revisions import apply_patch, diff_timelines, revert_to
from ageneditor.sound_library import SoundLibraryStore
from ageneditor.storage import ProjectStore
from ageneditor.transcription import TranscriptionManager
from ageneditor.transitions import TransitionLibrary
from ageneditor.variants import PinnedTimelineStore, VariantService


def build_server(workspace: Path, shared_service: bool = False) -> MCPServer:
    workspace = workspace.expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    server = MCPServer("ageneditor")
    stores: dict[str, ProjectStore] = {}
    managers: dict[str, JobManager] = {}
    transcription_managers: dict[str, TranscriptionManager] = {}
    registry_lock = RLock()
    transitions = TransitionLibrary(workspace)

    def load_store(project_id: str) -> ProjectStore:
        if project_id not in stores:
            for database in workspace.glob("*/project.sqlite3"):
                try:
                    candidate = ProjectStore(database.parent)
                    if candidate.project_id == project_id:
                        stores[project_id] = candidate
                        managers[project_id] = JobManager(candidate)
                        transcription_managers[project_id] = TranscriptionManager(candidate)
                        break
                except EngineException:
                    continue
        if project_id not in stores:
            raise EngineException(ErrorCode.PROJECT_NOT_FOUND, "project is not in the configured workspace")
        return stores[project_id]

    def store_for(project_id: str) -> ProjectStore:
        # Concurrent HTTP clients must never create a second worker/recover a live job.
        with registry_lock:
            return load_store(project_id)

    def manager_for(project_id: str) -> JobManager:
        store = store_for(project_id)
        if project_id not in managers:
            managers[project_id] = JobManager(store)
        return managers[project_id]

    def transcription_manager_for(project_id: str) -> TranscriptionManager:
        store = store_for(project_id)
        if project_id not in transcription_managers:
            transcription_managers[project_id] = TranscriptionManager(store)
        return transcription_managers[project_id]

    @server.tool()
    def create_project(name: str) -> dict[str, Any]:
        """Create a project inside the server's configured local workspace."""
        try:
            store = ProjectStore.create(workspace, name)
            with registry_lock:
                managers[store.project_id] = JobManager(store)
                transcription_managers[store.project_id] = TranscriptionManager(store)
                stores[store.project_id] = store
            store.record_agent_event("create_project", {}, "ok")
            return {
                "project_id": store.project_id,
                "project_name": name,
                "import_directory": str(store.root / "imports"),
                "status": "created",
            }
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    @server.tool()
    def open_project(project_id: str) -> dict[str, Any]:
        """Open a project by its registered ID from the configured workspace."""
        try:
            store = store_for(project_id)
            return {
                "project_id": project_id,
                "current_revision": store.current_revision(),
                "assets": len(store.assets()),
                "import_directory": str(store.root / "imports"),
            }
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    @server.tool()
    def register_asset(
        project_id: str,
        import_path: str,
        role: AssetRole,
        provenance: AssetProvenance | None = None,
    ) -> dict[str, Any]:
        """Copy and register a file already placed under this project's imports/ folder."""
        try:
            store = store_for(project_id)
            with store.audited("register_asset", role=role.value):
                asset = store.register_import(import_path, role, provenance)
            return asset.model_dump(mode="json")
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    @server.tool()
    def list_assets(project_id: str) -> list[dict[str, Any]]:
        """List the project's assets (files the user removed are not listed and cannot be used)."""
        try:
            return [asset.model_dump(mode="json") for asset in store_for(project_id).active_assets().values()]
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    @server.tool()
    def get_asset_checklist(project_id: str) -> dict[str, Any]:
        """Read project asset requests and their ready, pending or dropped links.

        ``review_notes`` holds a person's note on an approved file (for example "use only the first
        3 seconds"); follow it when placing that file. Agents cannot write notes or approvals.
        """
        try:
            return store_for(project_id).asset_checklist().model_dump(mode="json")
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    @server.tool()
    def get_brief(project_id: str) -> dict[str, Any]:
        """Read the client brief, script, reference note and requirement checklist a person wrote.

        Read-only. The engine does not verify that a plan satisfies the brief; a person confirms each
        checklist item.
        """
        try:
            return store_for(project_id).brief().model_dump(mode="json")
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    @server.tool()
    def inspect_asset(project_id: str, asset_id: str) -> dict[str, Any]:
        """Read FFprobe metadata and verify the registered source hash."""
        try:
            store = store_for(project_id)
            asset = store.assets().get(asset_id)
            if asset is None:
                raise EngineException(ErrorCode.ASSET_NOT_FOUND, "asset does not exist", asset_id=asset_id)
            from ageneditor.media.hashing import sha256_file

            path = store.safe_path(asset.relative_path)
            if sha256_file(path) != asset.sha256:
                raise EngineException(
                    ErrorCode.SOURCE_CHANGED, "registered source hash changed", asset_id=asset_id
                )
            return asset.model_dump(mode="json")
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    @server.tool()
    def create_timeline(
        project_id: str, render_profile: RenderProfile, target_duration_frames: int
    ) -> dict[str, Any]:
        """Create revision zero using an explicit typed render profile."""
        try:
            store = store_for(project_id)
            with store.audited(
                "create_timeline", profile_id=render_profile.id, target_duration_frames=target_duration_frames
            ):
                timeline = store.create_timeline(render_profile, target_duration_frames)
            return timeline.model_dump(mode="json")
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc
        except ValueError as exc:
            raise ToolError(
                EngineError(code=ErrorCode.INVALID_ARGUMENT, message=str(exc)[:2000]).model_dump_json()
            ) from exc

    @server.tool()
    def validate_timeline(project_id: str, timeline: Timeline) -> dict[str, Any]:
        """Validate a candidate timeline against this project's registered assets."""
        try:
            store = store_for(project_id)
            issues = [*store.validation_issues(timeline), *store.removed_asset_issues(timeline)]
            return {
                "valid": not any(issue.severity == "error" for issue in issues),
                "issues": [issue.model_dump(mode="json") for issue in issues],
            }
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc
        except ValueError as exc:
            raise ToolError(
                EngineError(code=ErrorCode.INVALID_TIMELINE, message=str(exc)[:2000]).model_dump_json()
            ) from exc

    @server.tool()
    def commit_timeline(project_id: str, timeline: Timeline, expected_revision: int) -> dict[str, Any]:
        """Commit a new immutable timeline version with optimistic revision locking."""
        try:
            store = store_for(project_id)
            with store.audited("commit_timeline", expected_revision=expected_revision):
                try:
                    before = store.timeline()
                except EngineException:
                    before = None  # no timeline yet
                transitions.check_new_transitions(before, timeline)
                committed = store.commit_timeline(timeline, expected_revision)
            return committed.model_dump(mode="json")
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc
        except ValueError as exc:
            raise ToolError(
                EngineError(code=ErrorCode.INVALID_TIMELINE, message=str(exc)[:2000]).model_dump_json()
            ) from exc

    @server.tool()
    def get_timeline(project_id: str, revision: int | None = None) -> dict[str, Any]:
        """Read an immutable timeline revision."""
        try:
            return store_for(project_id).timeline(revision).model_dump(mode="json")
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    @server.tool()
    def diff_timeline(project_id: str, from_revision: int, to_revision: int) -> dict[str, Any]:
        """Summarize clip, caption, correction and settings changes between two immutable revisions."""
        try:
            store = store_for(project_id)
            before, after = store.timeline(from_revision), store.timeline(to_revision)
            return diff_timelines(before, after).model_dump(mode="json")
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    @server.tool()
    def transcribe_asset(
        project_id: str, asset_id: str, model_preset: ModelPreset = ModelPreset.BASE
    ) -> dict[str, Any]:
        """Start local word-level transcription with an already cached faster-whisper model."""
        try:
            store = store_for(project_id)
            with store.audited("transcribe_asset", asset_id=asset_id, model_preset=model_preset.value):
                job = transcription_manager_for(project_id).start(asset_id, model_preset)
            return job.model_dump(mode="json")
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    @server.tool()
    def get_transcript(
        project_id: str,
        transcript_id: str,
        start_word: int = 0,
        limit: int = 120,
        context_words: int = 8,
    ) -> dict[str, Any]:
        """Read a bounded transcript page with source word IDs, exact microseconds and surrounding context.

        Words marked ``"timing": "estimated"`` had no usable times from the recognizer; their times were
        shared out by syllables within the segment. Prefer aligned words for cut points.
        """
        try:
            if start_word < 0 or not 1 <= limit <= 300 or not 0 <= context_words <= 30:
                raise EngineException(ErrorCode.INVALID_ARGUMENT, "invalid transcript page bounds")
            transcript = store_for(project_id).transcript(transcript_id)
            start = max(0, start_word - context_words)
            end = min(len(transcript.words), start_word + limit + context_words)
            selected = transcript.words[start:end]
            selected_ids = {word.id for word in selected}
            return {
                "transcript_id": transcript.id,
                "asset_id": transcript.asset_id,
                "source_sha256": transcript.source_sha256,
                "language": transcript.language,
                "language_probability": transcript.language_probability,
                "model_preset": transcript.model_preset.value,
                "total_words": len(transcript.words),
                "page_start": start,
                "has_more": start_word + limit < len(transcript.words),
                "next_start_word": min(len(transcript.words), start_word + limit),
                "requested_start": start_word,
                "requested_limit": limit,
                "words": [word.model_dump(mode="json") for word in selected],
                "segments": [
                    segment.model_dump(mode="json")
                    for segment in transcript.segments
                    if selected_ids.intersection(segment.word_ids)
                ],
                "silence_candidates": [
                    gap.model_dump(mode="json")
                    for gap in transcript.silence_candidates
                    if selected and gap.end_us >= selected[0].start_us and gap.start_us <= selected[-1].end_us
                ],
                "hook_candidates": [hook.model_dump(mode="json") for hook in candidate_hooks(transcript)],
                "hook_scoring_method": "deterministic timing, punctuation and word confidence hints",
            }
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    @server.tool()
    def submit_edit_plan(project_id: str, plan: EditPlan, expected_revision: int) -> dict[str, Any]:
        """Validate an agent-authored, word-cited cut plan; never commit or render it automatically."""
        try:
            store = store_for(project_id)
            with store.audited("submit_edit_plan", expected_revision=expected_revision):
                result = submit_edit_plan_service(store, plan, expected_revision)
            return result.model_dump(mode="json")
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    @server.tool()
    def start_render(
        project_id: str, revision: int, final: bool = True, variant_id: str | None = None
    ) -> dict[str, Any]:
        """Start a render pinned to an immutable timeline revision.

        With ``variant_id``, renders that variant computed against main revision ``revision``; the job
        records both revisions. A variant renders only when its own asset requests are ready too.
        """
        try:
            store = store_for(project_id)
            extra = {"variant_id": variant_id} if variant_id else {}
            with store.audited("start_render", revision=revision, final=final, **extra):
                job = manager_for(project_id).start_render(revision, final, variant_id)
            return job.model_dump(mode="json")
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    @server.tool()
    def get_job_status(project_id: str, job_id: str) -> dict[str, Any]:
        """Read durable render or transcription job state and structured failure details."""
        try:
            return store_for(project_id).get_job(job_id).model_dump(mode="json")
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    @server.tool()
    def cancel_job(project_id: str, job_id: str) -> dict[str, Any]:
        """Request cancellation of a queued or running render or transcription."""
        try:
            store = store_for(project_id)
            with store.audited("cancel_job", job_id=job_id):
                current = store.get_job(job_id)
                job = (
                    transcription_manager_for(project_id).cancel(job_id)
                    if current.kind is JobKind.TRANSCRIBE
                    else manager_for(project_id).cancel(job_id)
                )
            return job.model_dump(mode="json")
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    @server.tool()
    def run_qa(project_id: str, job_id: str) -> dict[str, Any]:
        """Run decode, output format, duration and original-hash checks for a successful render."""
        try:
            store = store_for(project_id)
            with store.audited("run_qa", job_id=job_id):
                job = store.get_job(job_id)
                if job.status.value != "succeeded" or not job.output_path or job.timeline_revision is None:
                    raise EngineException(ErrorCode.QA_FAILED, "QA requires a successful render job")
                target = store
                if job.variant_id is not None:
                    view = VariantService(store).view(
                        job.variant_id, job.timeline_revision, job.variant_revision
                    )
                    if view.timeline is None:
                        raise EngineException(ErrorCode.QA_FAILED, "the variant can no longer be computed")
                    target = PinnedTimelineStore(store, view.timeline)
                report = run_qa_service(
                    target, job.id, job.timeline_revision, store.safe_path(job.output_path)
                )
            return report.model_dump(mode="json")
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    @server.tool()
    def run_preview_qa(project_id: str, job_id: str) -> dict[str, Any]:
        """Check a finished render (normally the preview) before making the final render.

        Measured on the file: format against its timeline, a frame grid of each clip's middle frame with
        flat (black or single-colour) frames flagged, black bars where a clip should fill the frame, and
        captions shown while the audio is silent (possible caption drift; music counts as sound). Warnings
        only; it never blocks a render. You cannot see the frame grid; the person can, in the app.
        """
        try:
            store = store_for(project_id)
            with store.audited("run_preview_qa", job_id=job_id):
                report = run_preview_qa_service(store, job_id)
            return report.model_dump(mode="json")
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    @server.tool()
    def get_qa_report(project_id: str, job_id: str) -> dict[str, Any]:
        """Read the latest durable technical QA report for a render job."""
        try:
            return store_for(project_id).get_qa_report(job_id).model_dump(mode="json")
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    @server.tool()
    def set_asset_labels(project_id: str, asset_id: str, labels: list[ShotLabel]) -> dict[str, Any]:
        """Replace the user-supplied shot labels on a registered asset (used for B-roll matching)."""
        try:
            store = store_for(project_id)
            with store.audited("set_asset_labels", asset_id=asset_id, label_count=len(labels)):
                asset = store.set_asset_labels(asset_id, labels)
            return asset.model_dump(mode="json")
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc
        except ValueError as exc:
            raise ToolError(
                EngineError(code=ErrorCode.INVALID_ARGUMENT, message=str(exc)[:2000]).model_dump_json()
            ) from exc

    @server.tool()
    def suggest_broll(project_id: str, revision: int | None = None, limit: int = 10) -> dict[str, Any]:
        """Propose supplied B-roll placements by matching shot labels to spoken words. Places nothing."""
        try:
            if not 1 <= limit <= 50:
                raise EngineException(ErrorCode.INVALID_ARGUMENT, "limit must be 1-50")
            store = store_for(project_id)
            timeline = store.timeline(revision)
            suggestions = suggest_broll_service(store, timeline, limit)
            return {
                "revision": timeline.revision,
                "method": BROLL_METHOD,
                "suggestions": [item.to_dict() for item in suggestions],
            }
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    @server.tool()
    def apply_revision(project_id: str, patch: RevisionPatch, expected_revision: int) -> dict[str, Any]:
        """Apply a bounded typed patch as a new immutable revision; returns the timeline and its diff."""
        try:
            store = store_for(project_id)
            with store.audited("apply_revision", expected_revision=expected_revision, ops=len(patch.ops)):
                result = apply_patch(store, patch, expected_revision)
            return result.model_dump(mode="json")
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc
        except ValueError as exc:
            raise ToolError(
                EngineError(code=ErrorCode.INVALID_TIMELINE, message=str(exc)[:2000]).model_dump_json()
            ) from exc

    @server.tool()
    def revert_revision(project_id: str, target_revision: int, expected_revision: int) -> dict[str, Any]:
        """Versioned undo: commit a new revision equal to an earlier one. History is never deleted."""
        try:
            store = store_for(project_id)
            with store.audited(
                "revert_revision", target_revision=target_revision, expected_revision=expected_revision
            ):
                result = revert_to(store, target_revision, expected_revision)
            return result.model_dump(mode="json")
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    @server.tool()
    def get_captions_srt(project_id: str, revision: int | None = None) -> dict[str, Any]:
        """Export a revision's source-linked captions as SRT text."""
        try:
            timeline = store_for(project_id).timeline(revision)
            return {
                "revision": timeline.revision,
                "cue_count": len(timeline.captions),
                "srt": to_srt(timeline),
            }
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    library = SoundLibraryStore(workspace)

    @server.tool()
    def suggest_reframe(project_id: str, revision: int | None = None) -> dict[str, Any]:
        """Suggest crop centres for primary clips whose crop cuts away part of the picture.

        Not face detection: it finds where the picture has the most detail and movement, which usually
        follows a speaker on a plain background. Apply a suggestion with apply_revision (set_transform) only
        if it fits the shot, and ask the person to check the preview. Low-confidence clips are left out.
        """
        try:
            from ageneditor.reframe import METHOD
            from ageneditor.reframe import suggest_reframe as suggest

            store = store_for(project_id)
            timeline = store.timeline(revision)
            return {"method": METHOD, "revision": timeline.revision, "suggestions": suggest(store, timeline)}
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    @server.tool()
    def export_otio(project_id: str, revision: int | None = None) -> dict[str, Any]:
        """Write a revision as an OpenTimelineIO (.otio) file inside the project's exports folder.

        For finishing in another editor. Media is referenced by its registered original's path; captions are
        not included (use get_captions_srt). Interchange with other editors is not verified.
        """
        try:
            from ageneditor.otio import export_otio as export_otio_service

            store = store_for(project_id)
            timeline = store.timeline(revision)
            text = export_otio_service(store, timeline)
            relative = f"exports/r{timeline.revision}.otio"
            store.write_artifact(relative, text)
            return {"path": relative, "revision": timeline.revision, "clips": len(timeline.clips)}
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    @server.tool()
    def list_editor_presets(project_id: str | None = None) -> dict[str, Any]:
        """List the workspace's editor presets (caption template, colour, B-roll colour match, audio mix).

        With ``project_id``, ``chosen`` is the preset the person picked for that project (or null). Apply it
        with apply_editor_preset when they did; do not apply presets they did not ask for.
        """
        try:
            from ageneditor.editor_presets import EditorPresetStore

            library = EditorPresetStore(workspace).load()
            chosen = store_for(project_id).setting("editor_preset_id") if project_id else None
            return {"presets": [p.model_dump(mode="json") for p in library.presets], "chosen": chosen}
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    @server.tool()
    def apply_editor_preset(project_id: str, preset_id: str, expected_revision: int) -> dict[str, Any]:
        """Apply an editor preset's finishing settings as a new revision (captions, colour, audio only)."""
        try:
            from ageneditor.editor_presets import EditorPresetStore, apply_preset

            store = store_for(project_id)
            preset = EditorPresetStore(workspace).get(preset_id)
            audit = {"preset_id": preset_id, "expected_revision": expected_revision}
            with store.audited("apply_editor_preset", **audit):
                result = apply_preset(store, preset, expected_revision)
            return result.model_dump(mode="json")
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    @server.tool()
    def list_variants(project_id: str) -> dict[str, Any]:
        """List variants of the main edit (alternative hooks, languages, localized shots).

        Each variant stores overrides on the shared body and is computed from the current main revision,
        so body edits reach every variant. ``status`` says whether it fits the current main edit.
        """
        try:
            store = store_for(project_id)
            service = VariantService(store)
            items = []
            for variant in service.list():
                view = service.view(variant.id)
                items.append(
                    {
                        "variant": variant.model_dump(mode="json"),
                        "main_revision": view.main_revision,
                        "renderable": view.renderable,
                        "errors": [i.message for i in view.issues if i.severity == "error"],
                        "pending_requests": view.pending_requests,
                    }
                )
            return {"variants": items}
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    @server.tool()
    def create_variant(
        project_id: str,
        name: str,
        kind: VariantKind,
        language: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Create a variant of the main edit. It starts identical to the main edit (no overrides).

        Then call update_variant to give it a different hook, asset swaps (localized B-roll, a voice line
        in another language) or captions off. Variant-only files are requested with ``variant_id`` in
        the asset_requests block; they gate only that variant.
        """
        try:
            store = store_for(project_id)
            with store.audited("create_variant", kind=kind.value):
                variant = VariantService(store).create(name, kind, language, note)
            return variant.model_dump(mode="json")
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    @server.tool()
    def update_variant(
        project_id: str,
        variant_id: str,
        overrides: VariantOverrides,
        expected_revision: int,
        summary: str = "",
    ) -> dict[str, Any]:
        """Save a variant's overrides as its next revision.

        ``hook_replaces_clips``: how many leading main V1 clips the new hook replaces. ``hook_clips``: the
        hook's V1 clips in order (positions are laid out from frame 0). Everything after the hook shifts
        by the length difference; overlays that started inside the replaced hook are dropped, except
        music on A2. ``asset_swaps``: main overlay asset ID -> replacement asset ID (V2/A2/A3/A4).
        ``hook_overlays``: extra overlays in the variant's own output time. ``captions``: "keep" (the hook
        gets captions from its transcript) or "off". Refused if the result does not validate against the
        current main edit. Returns the computed variant with its issues.
        """
        try:
            store = store_for(project_id)
            with store.audited("update_variant", variant_id=variant_id, expected_revision=expected_revision):
                view = VariantService(store).update(variant_id, overrides, expected_revision, summary)
            return view.model_dump(mode="json")
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    @server.tool()
    def get_variant(
        project_id: str,
        variant_id: str,
        main_revision: int | None = None,
        variant_revision: int | None = None,
    ) -> dict[str, Any]:
        """Read a variant computed against a main revision (default: both current): its overrides, the
        computed timeline, issues, dropped main clips and pending asset requests."""
        try:
            view = VariantService(store_for(project_id)).view(variant_id, main_revision, variant_revision)
            return view.model_dump(mode="json")
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    @server.tool()
    def list_transitions() -> dict[str, Any]:
        """Transitions you may put between V1 clips: only presets from styles the user ticked active.

        Add them to the timeline's `transitions` list as {after_clip_id, preset, duration_frames,
        rationale}. A transition sits on the cut after that V1 clip and keeps the total length.
        """
        return transitions.catalog()

    @server.tool()
    def list_sound_effects(query: str = "", limit: int = 30) -> dict[str, Any]:
        """Search sound effects in the packs the user marked active (e.g. query "whoosh").

        Only active packs are searchable. If active_packs is 0 or nothing fits, tell the user to
        tick a pack active or click "Download & install" on a free pack in the Sound effects panel.
        You cannot download or install sounds; never invent or fetch them.
        """
        try:
            return library.search(query, limit)
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    @server.tool()
    def add_sound_effect(project_id: str, sound_id: str) -> dict[str, Any]:
        """Add a sound from an active pack to the project as an sfx asset for track A3.

        Returns the registered asset (the same one if it was already added). Place it with an
        A3 clip; A3 clips may overlap. Audio-only assets use the timeline frame grid.
        """
        try:
            store = store_for(project_id)
            with store.audited("add_sound_effect", sound_id=sound_id):
                asset = library.add_to_project(store, sound_id)
            return asset.model_dump(mode="json")
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    if shared_service:
        from ageneditor.desktop_service import service_request

        async def remote_call(name: str, arguments: dict[str, Any], context: Any = None) -> CallToolResult:
            del context
            try:
                result = await asyncio.to_thread(
                    service_request, workspace, "/call", {"tool": name, "args": arguments}
                )
                return CallToolResult.model_validate(result)
            except EngineException as exc:
                raise ToolError(exc.error.model_dump_json()) from exc

        server.call_tool = remote_call
    return server


def run_stdio(workspace: Path, shared_service: bool = False) -> None:
    # No application logging is configured on stdout; MCP owns the stdio protocol stream.
    build_server(workspace, shared_service=shared_service).run(transport="stdio")


def build_scoped_server(workspace: Path, token: str) -> MCPServer:
    """MCP stdio bridge for one AgenEditor 2.0 production task.

    Exposes only the scoped production tools. Every call is forwarded to the shared engine service with
    this attempt's task token; the engine checks role, tool allowlist, lease and fencing on each call.
    """
    from ageneditor.desktop_service import service_request

    workspace = workspace.expanduser().resolve()
    server = MCPServer("ageneditor")

    def forward(tool: str, args: dict[str, Any]) -> Any:
        try:
            return service_request(workspace, "/scoped", {"tool": tool, "args": args, "task_token": token})[
                "result"
            ]
        except EngineException as exc:
            raise ToolError(exc.error.model_dump_json()) from exc

    @server.tool()
    def production_get_assignment() -> dict[str, Any]:
        """Your task, its pinned instructions and policy, the clip, its plan and in-scope sources."""
        return forward("production_get_assignment", {})

    @server.tool()
    def production_get_transcript(
        transcript_id: str, start_word: int = 0, limit: int = 200
    ) -> dict[str, Any]:
        """A page of an in-scope source transcript with word IDs and exact microsecond times."""
        return forward(
            "production_get_transcript",
            {"transcript_id": transcript_id, "start_word": start_word, "limit": limit},
        )

    @server.tool()
    def production_task_complete(summary: str, no_op: bool = False, reason: str = "") -> dict[str, Any]:
        """Say you are finished. The engine checks the clip's actual state before the task counts as done."""
        return forward("production_task_complete", {"summary": summary, "no_op": no_op, "reason": reason})

    @server.tool()
    def discovery_submit_candidates(proposals: list[CandidateProposal]) -> dict[str, Any]:
        """Propose clips by citing transcript word IDs. The engine derives times and the exact quotes."""
        return forward(
            "discovery_submit_candidates", {"proposals": [p.model_dump(mode="json") for p in proposals]}
        )

    @server.tool()
    def plan_submit(draft: ClipPlanDraft) -> dict[str, Any]:
        """Submit this clip's plan: ordered word-cited cuts, goal, hook and conditional passes."""
        return forward("plan_submit", {"draft": draft.model_dump(mode="json")})

    @server.tool()
    def clip_get_timeline(revision: int | None = None) -> dict[str, Any]:
        """Read this clip's timeline (reviewers: only the reviewed revision)."""
        return forward("clip_get_timeline", {"revision": revision})

    @server.tool()
    def clip_validate_timeline(timeline: Timeline) -> dict[str, Any]:
        """Validate a candidate timeline against registered assets without committing it."""
        return forward("clip_validate_timeline", {"timeline": timeline.model_dump(mode="json")})

    @server.tool()
    def clip_apply_revision(summary: str, ops: list[RevisionOp], expected_revision: int) -> dict[str, Any]:
        """Apply bounded typed edit operations to this clip as a new immutable revision."""
        return forward(
            "clip_apply_revision",
            {
                "summary": summary,
                "expected_revision": expected_revision,
                "ops": [op.model_dump(mode="json") for op in ops],
            },
        )

    @server.tool()
    def clip_commit_timeline(timeline: Timeline, expected_revision: int) -> dict[str, Any]:
        """Commit a full validated timeline for this clip with optimistic revision locking."""
        return forward(
            "clip_commit_timeline",
            {"timeline": timeline.model_dump(mode="json"), "expected_revision": expected_revision},
        )

    @server.tool()
    def clip_suggest_broll(limit: int = 5) -> dict[str, Any]:
        """Suggest supplied, labelled B-roll for this clip from spoken words. Places nothing."""
        return forward("clip_suggest_broll", {"limit": limit})

    @server.tool()
    def production_list_transitions() -> dict[str, Any]:
        """Transitions you may use: presets from styles the user marked active."""
        return forward("production_list_transitions", {})

    @server.tool()
    def qa_get_review_packet() -> dict[str, Any]:
        """Reviewers: the pinned, read-only evidence for the revision under review."""
        return forward("qa_get_review_packet", {})

    @server.tool()
    def qa_submit_findings(submission: QAFindingSubmission) -> dict[str, Any]:
        """Reviewers: submit your verdict and typed findings once. You cannot edit the clip."""
        return forward("qa_submit_findings", {"submission": submission.model_dump(mode="json")})

    return server
