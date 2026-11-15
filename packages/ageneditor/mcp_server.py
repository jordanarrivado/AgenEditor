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
    EditPlan,
    EngineError,
    EngineException,
    ErrorCode,
    JobKind,
    ModelPreset,
    RenderProfile,
    RevisionPatch,
    ShotLabel,
    Timeline,
)
from ageneditor.jobs import JobManager
from ageneditor.media.qa import run_qa as run_qa_service
from ageneditor.planning import candidate_hooks
from ageneditor.planning import submit_edit_plan as submit_edit_plan_service
from ageneditor.revisions import apply_patch, diff_timelines, revert_to
from ageneditor.sound_library import SoundLibraryStore
from ageneditor.storage import ProjectStore
from ageneditor.transcription import TranscriptionManager
from ageneditor.transitions import TransitionLibrary


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
        """Read project asset requests and their ready, pending or dropped links."""
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
        """Read a bounded transcript page with source word IDs, exact microseconds and surrounding context."""
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
    def start_render(project_id: str, revision: int, final: bool = True) -> dict[str, Any]:
        """Start a render pinned to an immutable timeline revision."""
        try:
            store = store_for(project_id)
            with store.audited("start_render", revision=revision, final=final):
                job = manager_for(project_id).start_render(revision, final)
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
                report = run_qa_service(
                    store, job.id, job.timeline_revision, store.safe_path(job.output_path)
                )
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
