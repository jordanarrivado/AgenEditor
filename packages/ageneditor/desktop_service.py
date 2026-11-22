"""Per-user loopback engine service shared by the Tauri desktop and MCP stdio bridge."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import BoundedSemaphore
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

from ageneditor.contracts import (
    REEL_FINAL,
    AssetChecklist,
    AssetProvenance,
    AssetRole,
    CheckStatus,
    EngineException,
    ErrorCode,
    JobKind,
    ProjectBrief,
    RenderProfile,
)
from ageneditor.service_credentials import discovery_path, make_private, prepare_discovery
from ageneditor.storage import ProjectStore

DISCOVERY_NAME = ".ageneditor-service.json"
MAX_REQUEST_BYTES = 4 * 1024 * 1024


def _discovery(workspace: Path) -> Path:
    return discovery_path(workspace)


class LocalHTTPServer(ThreadingHTTPServer):
    """Bound connection count and read time; stalled clients never block health/status."""

    daemon_threads = True

    def __init__(self, *args: Any):
        self.slots = BoundedSemaphore(16)
        super().__init__(*args)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        request.settimeout(10)
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def service_address(workspace: Path) -> tuple[str, str] | None:
    """Return the live loopback URL/token, or None when no service is reachable."""
    try:
        data = json.loads(_discovery(workspace).read_text(encoding="utf-8"))
        port = int(data["port"])
        token = str(data["token"])
        if not 1 <= port <= 65535 or len(token) != 64:
            return None
        connection = HTTPConnection("127.0.0.1", port, timeout=1)
        try:
            connection.request("GET", "/health", headers={"Authorization": f"Bearer {token}"})
            if connection.getresponse().status == 200:
                return f"http://127.0.0.1:{port}", token
        finally:
            connection.close()
    except (OSError, ValueError, KeyError):
        pass
    return None


def service_request(workspace: Path, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    address = service_address(workspace)
    if address is None:
        raise EngineException(ErrorCode.PROJECT_NOT_FOUND, "shared desktop engine service is not running")
    if endpoint not in {"/call", "/desktop", "/scoped"}:
        raise EngineException(ErrorCode.INVALID_ARGUMENT, "unknown service endpoint")
    url, token = address
    port = int(url.rsplit(":", 1)[1])
    connection = HTTPConnection("127.0.0.1", port, timeout=120)
    try:
        connection.request(
            "POST",
            endpoint,
            body=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        data = json.loads(response.read())
        if response.status >= 400:
            error = data.get("error", {})
            if isinstance(error, dict) and "code" in error and "message" in error:
                raise EngineException(ErrorCode(error["code"]), error["message"])
            raise EngineException(ErrorCode.PERMISSION_DENIED, "desktop service rejected request")
        return data
    except OSError as exc:
        raise EngineException(ErrorCode.PROJECT_NOT_FOUND, "desktop service became unavailable") from exc
    finally:
        connection.close()


@contextmanager
def _single_service_lock(workspace: Path) -> Iterator[None]:
    """Hold a one-byte OS file lock for the service lifetime."""
    lock_path = prepare_discovery(workspace).with_suffix(".lock")
    with lock_path.open("a+b") as handle:
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise EngineException(
                    ErrorCode.PERMISSION_DENIED, "engine service already owns workspace"
                ) from exc
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise EngineException(
                    ErrorCode.PERMISSION_DENIED, "engine service already owns workspace"
                ) from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class DesktopService:
    def __init__(self, workspace: Path):
        self.workspace = workspace.expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        from ageneditor.mcp_server import build_server

        self.mcp = build_server(self.workspace, shared_service=False)
        self.token = secrets.token_hex(32)
        self.http = LocalHTTPServer(("127.0.0.1", 0), self._handler())
        self._orchestrator: Any = None

    @property
    def orchestrator(self) -> Any:
        """AgenEditor 2.0 production orchestrator, created on first use (legacy workspaces pay nothing)."""
        if self._orchestrator is None:
            from ageneditor.orchestration.host_adapters import AnthropicMessagesAdapter, ClaudeCodeHostAdapter
            from ageneditor.orchestration.orchestrator import ProductionOrchestrator
            from ageneditor.production.workspace import WorkspaceRegistry

            registry = WorkspaceRegistry(self.workspace)
            api = next(p for p in registry.providers() if p.adapter_type == "anthropic_api")
            prices = {
                m.model: (m.input_micros_per_mtok, m.output_micros_per_mtok)
                for m in api.models
                if m.input_micros_per_mtok is not None and m.output_micros_per_mtok is not None
            }
            self._orchestrator = ProductionOrchestrator(
                self.workspace,
                registry,
                adapters=[ClaudeCodeHostAdapter(self.workspace), AnthropicMessagesAdapter(prices=prices)],
            )
        return self._orchestrator
        self.http.timeout = 0.5

    @property
    def port(self) -> int:
        return int(self.http.server_address[1])

    def _store(self, project_id: str) -> ProjectStore:
        for database in self.workspace.glob("*/project.sqlite3"):
            try:
                store = ProjectStore(database.parent)
                if store.project_id == project_id:
                    return store
            except (EngineException, sqlite3.Error):
                continue
        raise EngineException(ErrorCode.PROJECT_NOT_FOUND, "project is not in this workspace")

    def _desktop_call(self, operation: str, args: dict[str, Any]) -> Any:
        if operation == "list_projects":
            projects = []
            for database in self.workspace.glob("*/project.sqlite3"):
                try:
                    store = ProjectStore(database.parent)
                    with store._connect() as db:
                        row = db.execute(
                            "SELECT id, name, created_at, current_revision FROM project"
                        ).fetchone()
                    if row:
                        projects.append({**dict(row), "archived": store.setting("archived") == "1"})
                except (EngineException, sqlite3.Error):
                    continue
            return sorted(projects, key=lambda item: item["created_at"], reverse=True)
        if operation == "create_project":
            name = str(args["name"])
            profile = RenderProfile.model_validate(
                args.get("render_profile", REEL_FINAL.model_dump(mode="json"))
            )
            target = int(args.get("target_duration_frames", 1200))
            store = ProjectStore.create(self.workspace, name)
            store.create_timeline(profile, target)
            return {"project_id": store.project_id, "name": name, "current_revision": 0}
        if operation.startswith("production_"):
            from ageneditor.production.desktop_ops import PRODUCTION_OPERATIONS, production_call

            if operation not in PRODUCTION_OPERATIONS:
                raise EngineException(ErrorCode.INVALID_ARGUMENT, "unknown desktop operation")
            return production_call(self.orchestrator, operation, args)
        if operation.endswith("sound_pack") or operation in {"list_sound_packs", "list_sound_catalog"}:
            return self._sound_library_call(operation, args)
        if operation == "speech_model_status":
            from ageneditor.transcription import speech_models_cached

            return speech_models_cached()
        if operation in {"chat_set_key", "chat_remove_key"}:
            from ageneditor import credentials
            from ageneditor.contracts.chat import ChatProvider

            provider = ChatProvider(str(args["provider"]))
            if operation == "chat_set_key":
                credentials.set_key(provider.value, str(args["key"]))
            else:
                credentials.remove_key(provider.value)
            return {"provider": provider.value, "has_key": credentials.has_key(provider.value)}
        if operation in {"list_editor_presets", "delete_editor_preset"}:
            from ageneditor.editor_presets import EditorPresetStore

            presets = EditorPresetStore(self.workspace)
            if operation == "delete_editor_preset":
                library = presets.delete(str(args["preset_id"]), int(args["expected_version"]))
                return library.model_dump(mode="json")
            return presets.load().model_dump(mode="json")
        if operation in {"list_transition_styles", "set_active_transition_style"}:
            from ageneditor.contracts.transition import TransitionStyle
            from ageneditor.transitions import TransitionLibrary

            library = TransitionLibrary(self.workspace)
            if operation == "set_active_transition_style":
                library.set_active(TransitionStyle(str(args["style"])), bool(args["active"]))
            return library.styles()
        project_id = str(args["project_id"])
        store = self._store(project_id)
        if operation == "project_summary":
            with store._connect() as db:
                row = db.execute("SELECT id, name, created_at, current_revision FROM project").fetchone()
            assets = store.active_assets().values()
            return {**dict(row), "assets": [a.model_dump(mode="json") for a in assets]}
        if operation == "get_asset_checklist":
            return store.asset_checklist().model_dump(mode="json")
        if operation == "save_asset_checklist":
            candidate = AssetChecklist.model_validate({**args["checklist"], "project_id": project_id})
            saved = store.save_asset_checklist(candidate, int(args["expected_version"]))
            return saved.model_dump(mode="json")
        if operation == "link_asset_request":
            saved = store.link_asset_request(
                str(args["request_id"]), str(args["asset_id"]), int(args["expected_version"])
            )
            return saved.model_dump(mode="json")
        if operation in {"chat_status", "chat_history", "chat_set_consent", "chat_send", "chat_apply"}:
            return self._chat_call(store, operation, args)
        if operation == "rename_project":
            store.rename(str(args["name"]))
            return {"project_id": project_id, "name": str(args["name"]).strip()}
        if operation == "set_project_archived":
            store.set_setting("archived", "1" if args.get("archived") else None)
            return {"project_id": project_id, "archived": bool(args.get("archived"))}
        if operation == "save_editor_preset":
            from ageneditor.editor_presets import EditorPresetStore

            library = EditorPresetStore(self.workspace).save_from_project(
                store, str(args["name"]), int(args["expected_version"])
            )
            return library.model_dump(mode="json")
        if operation == "choose_editor_preset":
            from ageneditor.editor_presets import EditorPresetStore

            preset_id = args.get("preset_id")
            if preset_id is not None:
                EditorPresetStore(self.workspace).get(str(preset_id))
            store.set_setting("editor_preset_id", None if preset_id is None else str(preset_id))
            return {"project_id": project_id, "editor_preset_id": preset_id}
        if operation == "project_settings":
            return {
                "archived": store.setting("archived") == "1",
                "editor_preset_id": store.setting("editor_preset_id"),
                "client_name": store.setting("client_name"),
            }
        if operation == "export_otio":
            from ageneditor.otio import export_otio

            revision = args.get("revision")
            timeline = store.timeline(None if revision is None else int(revision))
            with store._connect() as db:
                name = str(db.execute("SELECT name FROM project").fetchone()[0])
            return {"text": export_otio(store, timeline, name), "revision": timeline.revision}
        if operation == "import_otio":
            from ageneditor.otio import import_otio

            expected = int(args["expected_revision"])
            candidate, issues = import_otio(store, str(args["text"]), store.timeline(expected))
            errors = [issue for issue in issues if issue.severity == "error"]
            committed = None
            if candidate is not None and not errors:
                committed = store.commit_timeline(candidate, expected).revision
            return {
                "committed_revision": committed,
                "clip_count": len(candidate.clips) if candidate else 0,
                "issues": [issue.model_dump(mode="json") for issue in issues],
            }
        if operation == "list_variants":
            from ageneditor.variants import VariantService

            service = VariantService(store)
            items = []
            for variant in service.list():
                view = service.view(variant.id)
                items.append(
                    {
                        "variant": variant.model_dump(mode="json"),
                        "main_revision": view.main_revision,
                        "duration_frames": view.timeline.duration_frames if view.timeline else None,
                        "renderable": view.renderable,
                        "errors": [i.message for i in view.issues if i.severity == "error"],
                        "warnings": [i.message for i in view.issues if i.severity == "warning"],
                        "pending_requests": view.pending_requests,
                    }
                )
            return items
        if operation == "get_brief":
            return store.brief().model_dump(mode="json")
        if operation == "save_brief":
            candidate = ProjectBrief.model_validate({**args["brief"], "project_id": project_id})
            return store.save_brief(candidate, int(args["expected_version"])).model_dump(mode="json")
        if operation == "replace_asset_request_link":
            saved = store.replace_asset_request_link(
                str(args["request_id"]), str(args["asset_id"]), int(args["expected_version"])
            )
            return saved.model_dump(mode="json")
        if operation == "approve_asset_request":
            note = args.get("note")
            saved = store.approve_asset_request(
                str(args["request_id"]),
                int(args["expected_version"]),
                None if note is None else str(note),
            )
            return saved.model_dump(mode="json")
        if operation == "reset_asset_request":
            saved = store.reset_asset_request(str(args["request_id"]), int(args["expected_version"]))
            return saved.model_dump(mode="json")
        if operation == "accept_asset_issue":
            note = args.get("note")
            saved = store.accept_asset_issue(
                str(args["request_id"]),
                None if note is None else str(note),
                int(args["expected_version"]),
            )
            return saved.model_dump(mode="json")
        if operation == "list_jobs":
            with store._connect() as db:
                rows = db.execute("SELECT data FROM jobs ORDER BY rowid DESC").fetchall()
            return [json.loads(row[0]) for row in rows]
        if operation == "list_revisions":
            with store._connect() as db:
                rows = db.execute(
                    "SELECT revision, created_at FROM timelines ORDER BY revision DESC"
                ).fetchall()
            return [dict(row) for row in rows]
        if operation == "list_transcripts":
            with store._connect() as db:
                rows = db.execute("SELECT data FROM transcripts ORDER BY rowid DESC").fetchall()
            return [
                {
                    "id": item["id"],
                    "asset_id": item["asset_id"],
                    "language": item["language"],
                    "word_count": len(item["words"]),
                    "created_at": item["created_at"],
                }
                for row in rows
                if (item := json.loads(row[0]))
            ]
        if operation == "import_file":
            path = Path(str(args["source_path"]))
            if not path.is_absolute():
                raise EngineException(ErrorCode.PERMISSION_DENIED, "desktop import requires a selected file")
            provenance = AssetProvenance.model_validate(args.get("provenance", {}))
            with store.audited("desktop_import", role=str(args["role"])):
                asset = store.import_asset(path, AssetRole(str(args["role"])), provenance)
            return asset.model_dump(mode="json")
        if operation == "remove_asset":
            asset_id = str(args["asset_id"])
            with store.audited("desktop_remove_asset", asset_id=asset_id):
                store.remove_asset(asset_id)
            return {"removed": asset_id}
        if operation == "asset_path":
            asset = store.assets().get(str(args["asset_id"]))
            if asset is None:
                raise EngineException(ErrorCode.ASSET_NOT_FOUND, "asset does not exist")
            return {"path": str(store.safe_path(asset.relative_path))}
        if operation == "source_range":
            from ageneditor.contracts import Clip, TrackId
            from ageneditor.media.ranges import source_interval
            from ageneditor.timebase import seconds_to_frames_round

            asset = store.assets().get(str(args["asset_id"]))
            if asset is None or asset.video is None:
                raise EngineException(ErrorCode.ASSET_NOT_FOUND, "video asset does not exist")
            timeline = store.timeline(int(args.get("revision", store.current_revision())))
            clip = Clip(
                clip_id="range_preview",
                asset_id=asset.id,
                track=TrackId.V1,
                source_in_frames=int(args["source_in_frames"]),
                source_out_frames=int(args["source_out_frames"]),
                timeline_in_frames=0,
            )
            if clip.source_out_frames > asset.video.duration_frames:
                raise EngineException(ErrorCode.RANGE_OUT_OF_BOUNDS, "source range exceeds video")
            start, end = source_interval(
                asset, clip, store.safe_path(asset.relative_path), timeline.render_profile.frame_rate.fraction
            )
            return {
                "start_us": round(start * 1_000_000),
                "end_us": round(end * 1_000_000),
                "output_frames": seconds_to_frames_round(end - start, timeline.render_profile.frame_rate),
            }
        if operation == "preview_qa":
            from ageneditor.media.preview_qa import get_preview_qa, run_preview_qa

            job_id = str(args["job_id"])
            report = run_preview_qa(store, job_id) if args.get("run") else get_preview_qa(store, job_id)
            if report is None:
                return {"report": None, "grid_path": None}
            grid = str(store.safe_path(report.frame_grid_path)) if report.frame_grid_path else None
            return {"report": report.model_dump(mode="json"), "grid_path": grid}
        if operation == "delivery_plan":
            from ageneditor.delivery import delivery_plan

            client = args.get("client")
            plan = delivery_plan(store, list(args["job_ids"]), None if client is None else str(client))
            return plan.model_dump(mode="json")
        if operation == "render_path":
            job = store.get_job(str(args["job_id"]))
            if job.status.value != "succeeded" or not job.output_path:
                raise EngineException(ErrorCode.RENDER_FAILED, "render is not complete")
            if args.get("require_qa"):
                if job.kind is not JobKind.FINAL_RENDER:
                    raise EngineException(ErrorCode.QA_FAILED, "only final renders may be exported")
                report = store.get_qa_report(job.id)
                if report.overall is CheckStatus.FAIL:
                    raise EngineException(ErrorCode.QA_FAILED, "final render did not pass technical QA")
            return {"path": str(store.safe_path(job.output_path))}
        raise EngineException(ErrorCode.INVALID_ARGUMENT, "unknown desktop operation")

    def _chat_call(self, store: ProjectStore, operation: str, args: dict[str, Any]) -> Any:
        """Built-in chat (Phase 6). Keys stay in the credential store; the page never receives one."""
        from ageneditor import chat, credentials
        from ageneditor.chat_providers import DEFAULT_MODELS, complete
        from ageneditor.contracts.chat import ChatProvider

        if operation == "chat_status":
            keys = {}
            for provider in ChatProvider:
                try:
                    keys[provider.value] = credentials.has_key(provider.value)
                except EngineException:
                    keys[provider.value] = False
            return {
                "consent": store.setting("chat_consent") == "1",
                "keys": keys,
                "default_models": {p.value: m for p, m in DEFAULT_MODELS.items()},
                "data_sent": chat.DATA_SENT,
            }
        if operation == "chat_set_consent":
            store.set_setting("chat_consent", "1" if args.get("consent") else None)
            return {"consent": bool(args.get("consent"))}
        if operation == "chat_history":
            return {
                "messages": [m.model_dump(mode="json") for m in chat.history(store)],
                "proposals": [
                    p.model_dump(mode="json", exclude={"result": {"candidate_timeline"}})
                    for p in chat.proposals(store)
                ],
            }
        if operation == "chat_apply":
            applied = chat.apply_proposal(store, str(args["proposal_id"]), int(args["expected_revision"]))
            return applied.model_dump(mode="json", exclude={"result": {"candidate_timeline"}})
        provider = ChatProvider(str(args["provider"]))
        model = str(args.get("model") or DEFAULT_MODELS[provider])
        key = credentials.get_key(provider.value)
        if not key:
            raise EngineException(ErrorCode.PERMISSION_DENIED, f"add a {provider.value} API key first")

        def ask(system: str, messages: list[tuple[str, str]]) -> str:
            return complete(provider, model, key, system, messages)

        turn = chat.send(store, provider, model, str(args["text"]), ask)
        return turn.model_dump(mode="json", exclude={"proposal": {"result": {"candidate_timeline"}}})

    def _sound_library_call(self, operation: str, args: dict[str, Any]) -> Any:
        from ageneditor.contracts.sound_library import SoundLicense
        from ageneditor.sound_catalog import bundled_sounds_dir
        from ageneditor.sound_library import SoundLibraryStore

        def summary(pack: Any) -> dict[str, Any]:
            return {**pack.model_dump(mode="json", exclude={"sounds"}), "sound_count": len(pack.sounds)}

        library = SoundLibraryStore(self.workspace)
        if operation == "list_sound_packs":
            library.install_starter(bundled_sounds_dir())
            return [summary(pack) for pack in library.load().packs]
        if operation == "list_sound_catalog":
            return library.catalog()
        if operation == "download_sound_pack":
            pack, skipped = library.install_catalog_pack(str(args["catalog_id"]), bundled_sounds_dir())
            return {"pack": summary(pack), "skipped": skipped}
        if operation == "install_sound_pack":
            path = Path(str(args["source_path"]))
            if not path.is_absolute():
                raise EngineException(ErrorCode.PERMISSION_DENIED, "install requires a selected folder")
            pack, skipped = library.install_pack(
                path,
                str(args["name"]),
                SoundLicense(str(args["license"])),
                str(args.get("credit") or ""),
                str(args.get("source") or ""),
            )
            return {"pack": summary(pack), "skipped": skipped}
        if operation == "set_active_sound_pack":
            return summary(library.set_active(str(args["pack_id"]), bool(args["active"])))
        if operation == "remove_sound_pack":
            library.remove_pack(str(args["pack_id"]))
            return {"removed": str(args["pack_id"])}
        raise EngineException(ErrorCode.INVALID_ARGUMENT, "unknown desktop operation")

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                return  # Never leak local paths or tokens to stdout.

            def _write(self, status: int, value: Any) -> None:
                body = json.dumps(value).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _authorized(self) -> bool:
                return (
                    self.headers.get("Host") == f"127.0.0.1:{owner.port}"
                    and self.headers.get("Origin") is None
                    and secrets.compare_digest(self.headers.get("Authorization", ""), f"Bearer {owner.token}")
                )

            def do_GET(self) -> None:
                if not self._authorized():
                    self._write(403, {"error": "forbidden"})
                elif self.path == "/health":
                    self._write(200, {"status": "ready"})
                else:
                    self._write(404, {"error": "not found"})

            def do_POST(self) -> None:
                if not self._authorized():
                    self._write(403, {"error": "forbidden"})
                    return
                try:
                    if self.headers.get("Transfer-Encoding"):
                        raise ValueError("transfer encoding unsupported")
                    if len(self.headers.get_all("Content-Length", [])) != 1:
                        raise ValueError("exactly one content length required")
                    size = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._write(400, {"error": "invalid content length"})
                    return
                if size <= 0 or size > MAX_REQUEST_BYTES:
                    self._write(413, {"error": "invalid body size"})
                    return
                try:
                    payload = json.loads(self.rfile.read(size))
                    if not isinstance(payload, dict):
                        raise ValueError("object body required")
                    if self.path == "/call":
                        result = asyncio.run(
                            owner.mcp.call_tool(str(payload["tool"]), payload.get("args", {}))
                        )
                        self._write(200, result.model_dump(mode="json", by_alias=True))
                    elif self.path == "/scoped":
                        # Production agents: every call is checked against its per-attempt task token.
                        result = owner.orchestrator.tools.call(
                            str(payload["tool"]), dict(payload.get("args", {})), str(payload["task_token"])
                        )
                        self._write(200, {"result": result})
                    elif self.path == "/desktop":
                        self._write(
                            200,
                            {
                                "result": owner._desktop_call(
                                    str(payload["operation"]), payload.get("args", {})
                                )
                            },
                        )
                    else:
                        self._write(404, {"error": "not found"})
                except ToolError as exc:
                    result = CallToolResult(content=[TextContent(type="text", text=str(exc))], is_error=True)
                    self._write(200, result.model_dump(mode="json", by_alias=True))
                except (EngineException, ValueError, KeyError, TypeError) as exc:
                    error = (
                        exc.error.model_dump(mode="json")
                        if isinstance(exc, EngineException)
                        else {
                            "code": ErrorCode.INVALID_ARGUMENT.value,
                            "message": "invalid desktop request",
                            "details": {},
                        }
                    )
                    self._write(400, {"error": error})

        return Handler

    def serve(self) -> None:
        with _single_service_lock(self.workspace):
            path = prepare_discovery(self.workspace)
            temporary = path.with_suffix(f".{secrets.token_hex(8)}.tmp")
            temporary.write_text(json.dumps({"port": self.port, "token": self.token}), encoding="utf-8")
            make_private(temporary)
            temporary.replace(path)
            self.orchestrator.start()
            try:
                self.http.serve_forever(poll_interval=0.2)
            finally:
                self.orchestrator.stop()
                self.http.server_close()
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if data.get("token") == self.token:
                        path.unlink(missing_ok=True)
                except (OSError, ValueError):
                    pass
