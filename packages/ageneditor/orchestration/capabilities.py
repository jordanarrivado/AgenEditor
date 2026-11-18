"""Per-attempt scoped capability tokens (spec §9 security rules).

A token names exactly one project, batch, clip (optional), task and attempt, a role and the tools that
role may call. Only its SHA-256 is stored. Tools check the token *inside the engine* on every call, and a
token stops working when the attempt ends, its lease expires, or it is revoked.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from ageneditor.contracts import EngineException, ErrorCode, ScopeRole, Task, TaskType
from ageneditor.production.common import parse_ts, ts
from ageneditor.production.store import ProductionStore

#: Scoped tools per role. Lease, approval, delivery and administration are never agent tools.
ROLE_TOOLS: dict[ScopeRole, tuple[str, ...]] = {
    ScopeRole.DISCOVERY: (
        "production_get_assignment",
        "production_get_transcript",
        "discovery_submit_candidates",
        "production_task_complete",
    ),
    ScopeRole.PLANNER: (
        "production_get_assignment",
        "production_get_transcript",
        "plan_submit",
        "production_task_complete",
    ),
    ScopeRole.TEAM: (
        "production_get_assignment",
        "production_get_transcript",
        "clip_get_timeline",
        "clip_validate_timeline",
        "clip_apply_revision",
        "clip_commit_timeline",
        "clip_suggest_broll",
        "production_list_transitions",
        "production_task_complete",
    ),
    ScopeRole.QA: (
        "production_get_assignment",
        "production_get_transcript",
        "clip_get_timeline",
        "qa_get_review_packet",
        "qa_submit_findings",
        "production_task_complete",
    ),
}

ROLE_FOR_TASK: dict[TaskType, ScopeRole] = {
    TaskType.DISCOVER_CLIPS: ScopeRole.DISCOVERY,
    TaskType.PLAN_CLIP: ScopeRole.PLANNER,
    TaskType.EDITORIAL_QA: ScopeRole.QA,
}


def role_for(task: Task) -> ScopeRole:
    return ROLE_FOR_TASK.get(task.type, ScopeRole.TEAM)


@dataclass(frozen=True)
class Scope:
    project_id: str
    batch_id: str
    clip_id: str | None
    task_id: str
    attempt_id: str
    role: ScopeRole
    tools: tuple[str, ...]
    expires_at: datetime

    def require(self, tool: str) -> None:
        if tool not in self.tools:
            raise EngineException(ErrorCode.PERMISSION_DENIED, f"this task may not call {tool}")


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_project(token: str) -> str:
    """The project a token claims (routing only; the secret part is still verified against that DB)."""
    project_id, _, secret = token.partition(".")
    if not project_id or len(secret) < 32 or len(token) > 128:
        raise EngineException(ErrorCode.PERMISSION_DENIED, "malformed task token")
    return project_id


def issue_token(
    production: ProductionStore, db: sqlite3.Connection, task: Task, attempt_id: str, expires_at: datetime
) -> tuple[str, Scope]:
    role = role_for(task)
    token = f"{production.project_id}.{secrets.token_urlsafe(32)}"
    scope = Scope(
        project_id=production.project_id,
        batch_id=task.batch_id,
        clip_id=task.clip_id,
        task_id=task.id,
        attempt_id=attempt_id,
        role=role,
        tools=ROLE_TOOLS[role],
        expires_at=expires_at,
    )
    body = {
        "project_id": scope.project_id,
        "batch_id": scope.batch_id,
        "clip_id": scope.clip_id,
        "task_id": scope.task_id,
        "attempt_id": scope.attempt_id,
        "role": scope.role.value,
        "tools": list(scope.tools),
    }
    db.execute(
        "INSERT INTO capability_tokens VALUES (?, ?, ?, ?, NULL)",
        (_digest(token), attempt_id, json.dumps(body), ts(expires_at)),
    )
    return token, scope


def verify_token(production: ProductionStore, token: str) -> Scope:
    if token_project(token) != production.project_id:
        raise EngineException(ErrorCode.PERMISSION_DENIED, "task token belongs to another project")
    with production.connect() as db:
        row = db.execute(
            "SELECT attempt_id, scope, expires_at, revoked_at FROM capability_tokens WHERE token_hash=?",
            (_digest(token),),
        ).fetchone()
        if row is None or row["revoked_at"] is not None:
            raise EngineException(ErrorCode.PERMISSION_DENIED, "task token is not valid")
        if parse_ts(row["expires_at"]) <= production.now():
            raise EngineException(ErrorCode.LEASE_EXPIRED, "task token expired")
        # The attempt must still hold its lease: an expired or reassigned worker loses every tool.
        production.check_live_attempt(db, row["attempt_id"])
    body = json.loads(row["scope"])
    return Scope(
        project_id=body["project_id"],
        batch_id=body["batch_id"],
        clip_id=body["clip_id"],
        task_id=body["task_id"],
        attempt_id=body["attempt_id"],
        role=ScopeRole(body["role"]),
        tools=tuple(body["tools"]),
        expires_at=parse_ts(row["expires_at"]),
    )


def extend_tokens(db: sqlite3.Connection, attempt_id: str, expires_at: datetime) -> None:
    db.execute(
        "UPDATE capability_tokens SET expires_at=? WHERE attempt_id=? AND revoked_at IS NULL",
        (ts(expires_at), attempt_id),
    )


def revoke_tokens(db: sqlite3.Connection, attempt_id: str, when: datetime) -> None:
    db.execute(
        "UPDATE capability_tokens SET revoked_at=? WHERE attempt_id=? AND revoked_at IS NULL",
        (ts(when), attempt_id),
    )
