"""External AI adapters (spec §9, D02, D06).

* ``ClaudeCodeHostAdapter`` runs Claude Code headless with no built-in tools and only the scoped
  AgenEditor MCP server, whose every call carries this attempt's capability token. It can do any task
  whose tools exist (discovery, planning, editing passes, revisions, editorial QA).
* ``AnthropicMessagesAdapter`` calls the Anthropic Messages API (official ``anthropic`` SDK, optional
  ``ai`` extra) for the two text-only tasks it is verified for: clip discovery and editorial QA. It is not
  an MCP host: it builds the read-only packet in-process and submits through the same scoped tool
  service, so the engine validates its output exactly like any other worker's.

Both adapters only run when a person enabled the provider and the batch permits data egress.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ageneditor.contracts import (
    AgentCapabilities,
    AgentResult,
    CostKind,
    EngineError,
    EngineException,
    ErrorCode,
    ModelUsage,
    RunState,
    RunStatus,
    ScopedTask,
    TaskType,
)
from ageneditor.orchestration.agent_runtime import RunHandle
from ageneditor.production.common import new_id

if TYPE_CHECKING:
    from ageneditor.production.agent_tools import ScopedToolService

TOKEN_ENV = "AGENEDITOR_TASK_TOKEN"  # noqa: S105 — name of the variable, not a secret


def engine_command() -> list[str]:
    """How to start this engine's CLI (the packaged sidecar is itself the CLI)."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "ageneditor.cli"]


def _private_dir() -> Path:
    from ageneditor.service_credentials import make_private

    path = Path(tempfile.mkdtemp(prefix="ageneditor-task-"))
    make_private(path)
    return path


class ClaudeCodeHostAdapter:
    name = "claude_code"

    def __init__(
        self, workspace: Path, executable: list[str] | None = None, provider_id: str = "claude_code"
    ):
        self.workspace = workspace
        self._executable = executable
        self.provider_id = provider_id

    def _claude(self) -> list[str] | None:
        if self._executable is not None:
            return self._executable
        found = shutil.which("claude")
        return [found] if found else None

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            adapter_type=self.name,
            task_types=[
                TaskType.DISCOVER_CLIPS,
                TaskType.PLAN_CLIP,
                TaskType.STRUCTURAL_EDIT,
                TaskType.CAPTIONS,
                TaskType.VISUAL_POLISH,
                TaskType.AUDIO_POLISH,
                TaskType.ENHANCEMENT,
                TaskType.REVISION,
                TaskType.EDITORIAL_QA,
            ],
            deterministic=False,
            uses_tools=True,
        )

    def available(self) -> tuple[bool, str]:
        if self._claude() is None:
            return False, "Claude Code is not installed"
        from ageneditor.desktop_service import service_address

        if service_address(self.workspace) is None:
            return False, "the shared engine service is not running"
        return True, ""

    def start_task(self, assignment: ScopedTask, model: str | None = None) -> RunHandle:
        claude = self._claude()
        if claude is None:
            raise EngineException(ErrorCode.NO_CAPABLE_WORKER, "Claude Code is not installed")
        folder = _private_dir()
        config = folder / "mcp.json"
        config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "ageneditor": {
                            "command": engine_command()[0],
                            "args": [
                                *engine_command()[1:],
                                "mcp",
                                "--workspace",
                                str(self.workspace),
                                "--shared-service",
                                "--scoped",
                            ],
                            "env": {TOKEN_ENV: assignment.capability_token},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        instructions = folder / "instructions.txt"
        instructions.write_text(assignment.instructions, encoding="utf-8")
        command = [
            *claude,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--tools",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            str(config),
            "--allowedTools",
            "mcp__ageneditor",
            "--append-system-prompt-file",
            str(instructions),
        ]
        if model:
            command += ["--model", model]
        flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        try:
            process = subprocess.Popen(
                command,
                cwd=folder,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=flags,
            )
        except OSError as exc:
            shutil.rmtree(folder, ignore_errors=True)
            raise EngineException(ErrorCode.ADAPTER_FAILED, "could not start Claude Code") from exc
        handle = RunHandle(
            id=new_id("run"),
            adapter=self.name,
            attempt_id=assignment.attempt_id,
            data={"process": process, "folder": folder, "lines": [], "result": None, "model": model},
        )

        def read() -> None:
            for raw in process.stdout or ():
                try:
                    line = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(line, dict) and line.get("type") == "result":
                    handle.data["result"] = line
                elif isinstance(line, dict) and line.get("type") == "assistant":
                    handle.data["lines"].append("assistant turn")

        def drain_errors() -> None:
            data = process.stderr.read() if process.stderr else b""
            handle.data["stderr"] = data[-4000:].decode("utf-8", "replace")

        handle.data["reader"] = threading.Thread(target=read, daemon=True)
        handle.data["reader"].start()
        handle.data["stderr_reader"] = threading.Thread(target=drain_errors, daemon=True)
        handle.data["stderr_reader"].start()
        try:
            if process.stdin is None:
                raise OSError("no stdin")
            process.stdin.write(b"Carry out your scoped AgenEditor task now.")
            process.stdin.close()
        except OSError:
            pass
        return handle

    def poll(self, handle: RunHandle) -> RunStatus:
        process: subprocess.Popen[bytes] = handle.data["process"]
        if process.poll() is None:
            return RunStatus(state=RunState.RUNNING)
        return RunStatus(state=RunState.CANCELLED if handle.cancel.is_set() else RunState.FINISHED)

    def cancel(self, handle: RunHandle) -> None:
        handle.cancel.set()
        process: subprocess.Popen[bytes] = handle.data["process"]
        if process.poll() is None:
            process.kill()

    def collect_result(self, handle: RunHandle) -> AgentResult:
        process: subprocess.Popen[bytes] = handle.data["process"]
        process.wait(timeout=30)
        handle.data["reader"].join(timeout=5)
        handle.data["stderr_reader"].join(timeout=5)
        shutil.rmtree(handle.data["folder"], ignore_errors=True)
        result = handle.data.get("result") or {}
        usage = None
        cost = result.get("total_cost_usd")
        tokens = result.get("usage") or {}
        if handle.data.get("model"):
            usage = ModelUsage(
                provider_id=self.provider_id,
                model=str(handle.data["model"]),
                tokens_in=tokens.get("input_tokens"),
                tokens_out=tokens.get("output_tokens"),
                cost_micros=round(float(cost) * 1_000_000) if isinstance(cost, int | float) else None,
                cost_kind=CostKind.PROVIDER_REPORTED if isinstance(cost, int | float) else CostKind.UNKNOWN,
            )
        if handle.cancel.is_set():
            return AgentResult(
                ok=False,
                usage=usage,
                error=EngineError(code=ErrorCode.JOB_CANCELED, message="agent run was stopped"),
            )
        if process.returncode != 0 or result.get("is_error"):
            return AgentResult(
                ok=False,
                usage=usage,
                diagnostic=str(handle.data.get("stderr", ""))[-4000:],
                error=EngineError(code=ErrorCode.ADAPTER_FAILED, message="Claude Code ended with an error"),
            )
        return AgentResult(ok=True, usage=usage, diagnostic=str(result.get("result", ""))[:4000])


# ---------------------------------------------------------------- Anthropic Messages API

_FINDINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "summary", "evidence_basis", "findings"],
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "needs_revision"]},
        "summary": {"type": "string"},
        "evidence_basis": {
            "type": "array",
            "items": {"type": "string", "enum": ["timeline", "transcript", "technical_qa", "plan", "brief"]},
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "severity",
                    "category",
                    "reference",
                    "evidence",
                    "suggested_correction",
                    "confidence",
                ],
                "properties": {
                    "severity": {"type": "string", "enum": ["blocker", "major", "minor", "info"]},
                    "category": {
                        "type": "string",
                        "enum": [
                            "hook",
                            "narrative",
                            "framing",
                            "pacing",
                            "captions",
                            "audio",
                            "broll",
                            "reference_alignment",
                            "brief",
                            "technical",
                            "other",
                        ],
                    },
                    "reference": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["kind"],
                        "properties": {
                            "kind": {
                                "type": "string",
                                "enum": ["timeline", "clip", "caption", "word", "time_range"],
                            },
                            "id": {"type": "string"},
                            "transcript_id": {"type": "string"},
                            "start_frame": {"type": "integer"},
                            "end_frame": {"type": "integer"},
                        },
                    },
                    "evidence": {"type": "string"},
                    "suggested_correction": {"type": "string"},
                    "confidence": {"type": "number"},
                },
            },
        },
    },
}
_PROPOSALS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["proposals"],
    "properties": {
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["topic", "rationale", "evidence"],
                "properties": {
                    "topic": {"type": "string"},
                    "rationale": {"type": "string"},
                    "hook": {"type": "number"},
                    "relevance": {"type": "number"},
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["transcript_id", "first_word_id", "last_word_id"],
                            "properties": {
                                "transcript_id": {"type": "string"},
                                "first_word_id": {"type": "string"},
                                "last_word_id": {"type": "string"},
                            },
                        },
                    },
                },
            },
        }
    },
}
#: Words sent per source for discovery (keeps requests bounded; the rest is never silently implied).
MAX_DISCOVERY_WORDS = 20_000


class _NotRaised(Exception):
    """Placeholder for SDK error classes when only an injected client is in use."""


def _sdk_errors() -> tuple[type[Exception], type[Exception], type[Exception]]:
    try:
        import anthropic
    except ImportError:
        return _NotRaised, _NotRaised, _NotRaised
    return anthropic.RateLimitError, anthropic.APIStatusError, anthropic.APIConnectionError


class AnthropicMessagesAdapter:
    name = "anthropic_api"

    def __init__(
        self,
        provider_id: str = "anthropic_api",
        api_key_env: str = "ANTHROPIC_API_KEY",
        client_factory: Callable[[], Any] | None = None,
        prices: dict[str, tuple[int, int]] | None = None,
    ):
        self.provider_id = provider_id
        self.api_key_env = api_key_env
        self._client_factory = client_factory
        self.prices = prices or {}
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ageneditor-api")
        self.tools: ScopedToolService | None = None

    def bind(self, tools: ScopedToolService) -> None:
        self.tools = tools

    def _client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        import anthropic

        return anthropic.Anthropic(api_key=os.environ[self.api_key_env], max_retries=0)

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            adapter_type=self.name,
            task_types=[TaskType.DISCOVER_CLIPS, TaskType.EDITORIAL_QA],
            deterministic=False,
        )

    def available(self) -> tuple[bool, str]:
        if self._client_factory is not None:
            return True, ""
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False, "the anthropic SDK is not installed (uv sync --extra ai)"
        if not os.environ.get(self.api_key_env):
            return False, f"{self.api_key_env} is not set"
        return True, ""

    def start_task(self, assignment: ScopedTask, model: str | None = None) -> RunHandle:
        if self.tools is None:
            raise EngineException(ErrorCode.ADAPTER_FAILED, "adapter is not bound to the tool service")
        if assignment.task.type not in {TaskType.DISCOVER_CLIPS, TaskType.EDITORIAL_QA}:
            raise EngineException(ErrorCode.NO_CAPABLE_WORKER, "the API adapter cannot run this task")
        handle = RunHandle(id=new_id("run"), adapter=self.name, attempt_id=assignment.attempt_id)
        handle.future = self._pool.submit(self._run, assignment, model or "claude-opus-5-5", handle)
        return handle

    def _ask(
        self, model: str, system: str, user: str, schema: dict[str, Any]
    ) -> tuple[dict[str, Any], ModelUsage]:
        rate_limit, status_error, connection_error = _sdk_errors()
        client = self._client()
        try:
            response = client.beta.messages.create(
                model=model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": "high", "format": {"type": "json_schema", "schema": schema}},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except rate_limit as exc:
            retry = exc.response.headers.get("retry-after", "60")
            raise EngineException(
                ErrorCode.RATE_LIMITED, "provider rate limit", retry_after_s=float(retry)
            ) from exc
        except status_error as exc:
            code = ErrorCode.PROVIDER_UNAVAILABLE if exc.status_code >= 500 else ErrorCode.INVALID_ARGUMENT
            raise EngineException(code, f"provider returned HTTP {exc.status_code}") from exc
        except connection_error as exc:
            raise EngineException(ErrorCode.PROVIDER_UNAVAILABLE, "could not reach the provider") from exc
        tokens_in, tokens_out = response.usage.input_tokens, response.usage.output_tokens
        price = self.prices.get(model)
        usage = ModelUsage(
            provider_id=self.provider_id,
            model=str(getattr(response, "model", model)),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_micros=(tokens_in * price[0] + tokens_out * price[1]) // 1_000_000 if price else None,
            cost_kind=CostKind.ESTIMATED if price else CostKind.UNKNOWN,
        )
        if response.stop_reason == "refusal":
            raise EngineException(ErrorCode.HUMAN_DECISION_REQUIRED, "the provider declined this task")
        text = "".join(block.text for block in response.content if block.type == "text")
        try:
            return json.loads(text), usage
        except ValueError as exc:
            raise EngineException(ErrorCode.ADAPTER_FAILED, "provider returned malformed JSON") from exc

    def _run(self, assignment: ScopedTask, model: str, handle: RunHandle) -> AgentResult:
        if self.tools is None:
            raise EngineException(ErrorCode.ADAPTER_FAILED, "adapter is not bound")
        token = assignment.capability_token
        usage = None
        try:
            if assignment.task.type is TaskType.EDITORIAL_QA:
                packet = self.tools.call("qa_get_review_packet", {}, token)
                answer, usage = self._ask(
                    model,
                    assignment.instructions,
                    "Review this clip. Respond with JSON matching the schema.\n" + json.dumps(packet),
                    _FINDINGS_SCHEMA,
                )
                if handle.cancel.is_set():
                    raise EngineException(ErrorCode.JOB_CANCELED, "cancelled")
                self.tools.call("qa_submit_findings", {"submission": answer}, token)
            else:
                assignment_info = self.tools.call("production_get_assignment", {}, token)
                transcripts = []
                for source in assignment_info["sources"]:
                    if source["transcript_id"]:
                        transcripts.append(
                            self.tools.call(
                                "production_get_transcript",
                                {"transcript_id": source["transcript_id"], "start_word": 0, "limit": 300},
                                token,
                            )
                        )
                        page = transcripts[-1]
                        while page["has_more"] and page["start_word"] + 300 < MAX_DISCOVERY_WORDS:
                            page = self.tools.call(
                                "production_get_transcript",
                                {
                                    "transcript_id": source["transcript_id"],
                                    "start_word": page["start_word"] + 300,
                                    "limit": 300,
                                },
                                token,
                            )
                            transcripts[-1]["words"].extend(page["words"])
                answer, usage = self._ask(
                    model,
                    assignment.instructions,
                    "Propose clips citing word IDs. Respond with JSON matching the schema.\n"
                    + json.dumps({"assignment": assignment_info, "transcripts": transcripts}),
                    _PROPOSALS_SCHEMA,
                )
                self.tools.call("discovery_submit_candidates", {"proposals": answer["proposals"]}, token)
            self.tools.call("production_task_complete", {"summary": "API worker finished"}, token)
            return AgentResult(ok=True, usage=usage)
        except EngineException as exc:
            return AgentResult(ok=False, usage=usage, error=exc.error)
        except Exception as exc:  # worker boundary
            return AgentResult(
                ok=False,
                usage=usage,
                error=EngineError(
                    code=ErrorCode.ADAPTER_FAILED, message=f"API worker failed: {type(exc).__name__}"
                ),
            )

    def poll(self, handle: RunHandle) -> RunStatus:
        if handle.future is None:
            raise EngineException(ErrorCode.ADAPTER_FAILED, "run was never started")
        return RunStatus(state=RunState.FINISHED if handle.future.done() else RunState.RUNNING)

    def cancel(self, handle: RunHandle) -> None:
        handle.cancel.set()

    def collect_result(self, handle: RunHandle) -> AgentResult:
        if handle.future is None:
            raise EngineException(ErrorCode.ADAPTER_FAILED, "run was never started")
        return handle.future.result()
