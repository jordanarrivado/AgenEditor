"""Agent runtime adapters (spec §9, D02).

Every adapter runs exactly one ``ScopedTask`` per run. Adapters are interchangeable only for the task
types their verified capabilities list: the deterministic engine adapter runs engine handlers in a thread
pool; external adapters (``host_adapters``) drive an agent host or a model API. The interface is
synchronous and polled by the orchestrator, which fits the engine's threaded service.

An adapter's report is never evidence: the orchestrator verifies engine state before a task succeeds.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Protocol

from ageneditor.contracts import (
    AgentCapabilities,
    AgentResult,
    EngineError,
    EngineException,
    ErrorCode,
    RunState,
    RunStatus,
    ScopedTask,
    TaskType,
)
from ageneditor.production.common import new_id


@dataclass
class RunHandle:
    id: str
    adapter: str
    attempt_id: str
    cancel: threading.Event = field(default_factory=threading.Event)
    future: Future[AgentResult] | None = None
    data: dict[str, Any] = field(default_factory=dict)


class AgentRuntimeAdapter(Protocol):
    name: str

    def capabilities(self) -> AgentCapabilities: ...

    def available(self) -> tuple[bool, str]: ...

    def start_task(self, assignment: ScopedTask, model: str | None = None) -> RunHandle: ...

    def poll(self, handle: RunHandle) -> RunStatus: ...

    def cancel(self, handle: RunHandle) -> None: ...

    def collect_result(self, handle: RunHandle) -> AgentResult: ...


#: Signature of a deterministic engine handler: (assignment, cancel event) -> verified output dict.
Handler = Callable[[ScopedTask, threading.Event], dict[str, Any]]


class EngineToolAdapter:
    """Runs deterministic engine handlers. No model, no network."""

    name = "engine"

    def __init__(self, handlers: dict[TaskType, Handler], max_workers: int = 4):
        self.handlers = handlers
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ageneditor-production")

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(adapter_type=self.name, task_types=sorted(self.handlers), deterministic=True)

    def available(self) -> tuple[bool, str]:
        return True, ""

    def start_task(self, assignment: ScopedTask, model: str | None = None) -> RunHandle:
        del model
        handler = self.handlers.get(assignment.task.type)
        if handler is None:
            raise EngineException(
                ErrorCode.NO_CAPABLE_WORKER, f"no engine handler for {assignment.task.type.value}"
            )
        handle = RunHandle(id=new_id("run"), adapter=self.name, attempt_id=assignment.attempt_id)

        def run() -> AgentResult:
            try:
                return AgentResult(ok=True, output=handler(assignment, handle.cancel))
            except EngineException as exc:
                return AgentResult(ok=False, error=exc.error)
            except Exception as exc:  # worker boundary: failures are data, never a false success
                return AgentResult(
                    ok=False,
                    error=EngineError(
                        code=ErrorCode.ADAPTER_FAILED, message=f"engine handler failed: {type(exc).__name__}"
                    ),
                    diagnostic=str(exc)[:4000],
                )

        handle.future = self._pool.submit(run)
        return handle

    def poll(self, handle: RunHandle) -> RunStatus:
        if handle.future is None:
            raise EngineException(ErrorCode.ADAPTER_FAILED, "run was never started")
        if not handle.future.done():
            return RunStatus(state=RunState.RUNNING)
        return RunStatus(state=RunState.CANCELLED if handle.cancel.is_set() else RunState.FINISHED)

    def cancel(self, handle: RunHandle) -> None:
        handle.cancel.set()

    def collect_result(self, handle: RunHandle) -> AgentResult:
        if handle.future is None:
            raise EngineException(ErrorCode.ADAPTER_FAILED, "run was never started")
        return handle.future.result()

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
