"""Workspace-level production registry: clients, versioned instruction profiles, provider settings.

Lives in ``<workspace>/production.sqlite3`` beside the project folders. Project media and clip work stay
in each project's own database; this file only holds what spans projects.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from ageneditor.contracts import (
    Client,
    EngineException,
    ErrorCode,
    InstructionProfile,
    ProfileScope,
    ProviderConfig,
    ProviderModel,
    ResourceConfig,
    ResourceSnapshot,
    TaskType,
)
from ageneditor.production.common import content_hash, new_id, ts, utc_now
from ageneditor.production.schema import WORKSPACE_DDL

REGISTRY_NAME = "production.sqlite3"

#: Providers AgenEditor knows how to drive. Both start disabled: enabling one is the person's explicit
#: permission to send minimum task context off this machine.
DEFAULT_PROVIDERS = (
    ProviderConfig(
        id="claude_code",
        adapter_type="claude_code",
        label="Claude Code (your subscription)",
        models=[
            ProviderModel(
                model="claude-sonnet-5-5",
                task_types=[
                    TaskType.DISCOVER_CLIPS,
                    TaskType.PLAN_CLIP,
                    TaskType.STRUCTURAL_EDIT,
                    TaskType.CAPTIONS,
                    TaskType.VISUAL_POLISH,
                    TaskType.AUDIO_POLISH,
                    TaskType.ENHANCEMENT,
                    TaskType.REVISION,
                ],
                quality=60,
            ),
            ProviderModel(
                model="claude-opus-5-5",
                task_types=[TaskType.DISCOVER_CLIPS, TaskType.EDITORIAL_QA, TaskType.REVISION],
                quality=75,
            ),
        ],
        max_concurrency=2,
        requests_per_minute=10,
    ),
    ProviderConfig(
        id="anthropic_api",
        adapter_type="anthropic_api",
        label="Anthropic API (your API key)",
        api_key_env="ANTHROPIC_API_KEY",
        models=[
            ProviderModel(
                model="claude-opus-5-5",
                task_types=[TaskType.DISCOVER_CLIPS, TaskType.EDITORIAL_QA],
                input_micros_per_mtok=4_000_000,
                output_micros_per_mtok=20_000_000,
                quality=75,
                max_output_tokens=8000,
            ),
            ProviderModel(
                model="claude-haiku-5-5",
                task_types=[TaskType.DISCOVER_CLIPS],
                input_micros_per_mtok=100_000,
                output_micros_per_mtok=500_000,
                quality=45,
                max_output_tokens=4000,
            ),
        ],
        max_concurrency=2,
        requests_per_minute=30,
    ),
)


class WorkspaceRegistry:
    def __init__(self, workspace: Path):
        self.workspace = workspace.expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.db_path = self.workspace / REGISTRY_NAME
        if self.db_path.is_symlink() or self.db_path.is_junction():
            raise EngineException(ErrorCode.PERMISSION_DENIED, "production registry must not be a link")
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(WORKSPACE_DDL)
            for provider in DEFAULT_PROVIDERS:
                db.execute(
                    "INSERT OR IGNORE INTO providers VALUES (?, ?)", (provider.id, provider.model_dump_json())
                )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path, timeout=30)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db
        finally:
            db.close()

    # ------------------------------------------------------------ clients

    def create_client(self, name: str) -> Client:
        client = Client(id=new_id("client"), name=name)
        with self._connect() as db:
            if db.execute("SELECT 1 FROM clients WHERE lower(name)=lower(?)", (client.name,)).fetchone():
                raise EngineException(ErrorCode.INVALID_ARGUMENT, "a client with this name already exists")
            db.execute(
                "INSERT INTO clients VALUES (?, ?, ?, ?)",
                (client.id, client.name, client.status.value, client.model_dump_json()),
            )
        return client

    def clients(self) -> list[Client]:
        with self._connect() as db:
            rows = db.execute("SELECT data FROM clients ORDER BY name").fetchall()
        return [Client.model_validate_json(row[0]) for row in rows]

    def client(self, client_id: str) -> Client:
        with self._connect() as db:
            row = db.execute("SELECT data FROM clients WHERE id=?", (client_id,)).fetchone()
        if row is None:
            raise EngineException(ErrorCode.INVALID_ARGUMENT, "client does not exist")
        return Client.model_validate_json(row[0])

    def bind_project(self, project_id: str, client_id: str, has_batches: bool) -> None:
        """Attach a project to one client. A project with batches can never change client."""
        self.client(client_id)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT client_id FROM project_clients WHERE project_id=?", (project_id,)
            ).fetchone()
            if row is not None and row[0] == client_id:
                return
            if row is not None and has_batches:
                raise EngineException(
                    ErrorCode.PERMISSION_DENIED, "a project with batches cannot move to another client"
                )
            db.execute(
                "INSERT INTO project_clients VALUES (?, ?, ?) ON CONFLICT(project_id) DO UPDATE SET "
                "client_id=excluded.client_id, bound_at=excluded.bound_at",
                (project_id, client_id, ts(utc_now())),
            )

    def project_client(self, project_id: str) -> str | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT client_id FROM project_clients WHERE project_id=?", (project_id,)
            ).fetchone()
        return None if row is None else str(row[0])

    def project_clients(self) -> dict[str, str]:
        """Every project bound to a client, as ``project_id → client_id``."""
        with self._connect() as db:
            rows = db.execute("SELECT project_id, client_id FROM project_clients").fetchall()
        return {str(row[0]): str(row[1]) for row in rows}

    # ------------------------------------------------------------ profiles

    @staticmethod
    def _scope_key(scope: ProfileScope, client_id: str | None) -> str:
        return "global" if scope is ProfileScope.GLOBAL else f"client:{client_id}"

    def profile(self, scope: ProfileScope, client_id: str | None = None) -> InstructionProfile:
        """Latest revision of a profile layer (an empty revision 0 when none was saved)."""
        if scope is ProfileScope.CLIENT:
            self.client(str(client_id))
        with self._connect() as db:
            row = db.execute(
                "SELECT data FROM profile_revisions WHERE scope_key=? ORDER BY revision DESC LIMIT 1",
                (self._scope_key(scope, client_id),),
            ).fetchone()
        if row is None:
            return InstructionProfile(scope=scope, client_id=client_id)
        return InstructionProfile.model_validate_json(row[0])

    def profile_revision(
        self, scope: ProfileScope, client_id: str | None, revision: int
    ) -> InstructionProfile:
        with self._connect() as db:
            row = db.execute(
                "SELECT data FROM profile_revisions WHERE scope_key=? AND revision=?",
                (self._scope_key(scope, client_id), revision),
            ).fetchone()
        if row is None:
            raise EngineException(ErrorCode.INVALID_ARGUMENT, "profile revision does not exist")
        return InstructionProfile.model_validate_json(row[0])

    def save_profile(self, candidate: InstructionProfile, expected_revision: int) -> InstructionProfile:
        """Append a new immutable profile revision (old revisions stay for snapshots that cite them)."""
        if candidate.scope is ProfileScope.CLIENT:
            self.client(str(candidate.client_id))
        key = self._scope_key(candidate.scope, candidate.client_id)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT max(revision) FROM profile_revisions WHERE scope_key=?", (key,)
            ).fetchone()
            current = int(row[0] or 0)
            if current != expected_revision:
                raise EngineException(
                    ErrorCode.REVISION_CONFLICT,
                    "profile changed; refresh it before saving",
                    current_revision=current,
                )
            saved = candidate.model_copy(update={"revision": current + 1})
            saved = InstructionProfile.model_validate(saved.model_dump(mode="json"))
            db.execute(
                "INSERT INTO profile_revisions VALUES (?, ?, ?, ?, ?, ?)",
                (
                    new_id("profile"),
                    key,
                    saved.revision,
                    content_hash(saved),
                    saved.model_dump_json(),
                    ts(utc_now()),
                ),
            )
        return saved

    # ------------------------------------------------------------ providers and resources

    def providers(self) -> list[ProviderConfig]:
        with self._connect() as db:
            rows = db.execute("SELECT data FROM providers ORDER BY id").fetchall()
        return [ProviderConfig.model_validate_json(row[0]) for row in rows]

    def provider(self, provider_id: str) -> ProviderConfig:
        for provider in self.providers():
            if provider.id == provider_id:
                return provider
        raise EngineException(ErrorCode.INVALID_ARGUMENT, "provider does not exist")

    def save_provider(self, provider: ProviderConfig) -> ProviderConfig:
        provider = ProviderConfig.model_validate(provider.model_dump(mode="json"))
        with self._connect() as db:
            db.execute(
                "INSERT INTO providers VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (provider.id, provider.model_dump_json()),
            )
        return provider

    def set_provider_enabled(self, provider_id: str, enabled: bool) -> ProviderConfig:
        return self.save_provider(self.provider(provider_id).model_copy(update={"enabled": enabled}))

    def resource_config(self) -> ResourceConfig:
        with self._connect() as db:
            row = db.execute("SELECT data FROM settings WHERE key='resources'").fetchone()
        return ResourceConfig() if row is None else ResourceConfig.model_validate_json(row[0])

    def save_resource_config(self, config: ResourceConfig) -> ResourceConfig:
        config = ResourceConfig.model_validate(config.model_dump(mode="json"))
        with self._connect() as db:
            db.execute(
                "INSERT INTO settings VALUES ('resources', ?) "
                "ON CONFLICT(key) DO UPDATE SET data=excluded.data",
                (config.model_dump_json(),),
            )
        return config

    # ------------------------------------------------------------ resource history

    #: How long machine-pressure samples are kept.
    SAMPLE_RETENTION = timedelta(days=14)

    def record_resource_sample(self, snapshot: ResourceSnapshot) -> None:
        """Append one machine-pressure sample and drop samples older than the retention window."""
        with self._connect() as db:
            db.execute(
                "INSERT INTO resource_samples VALUES (?, ?)",
                (ts(snapshot.measured_at), snapshot.model_dump_json()),
            )
            db.execute(
                "DELETE FROM resource_samples WHERE measured_at < ?",
                (ts(snapshot.measured_at - self.SAMPLE_RETENTION),),
            )

    def resource_samples(self, since: datetime, until: datetime) -> list[ResourceSnapshot]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT data FROM resource_samples WHERE measured_at >= ? AND measured_at <= ? "
                "ORDER BY measured_at",
                (ts(since), ts(until)),
            ).fetchall()
        return [ResourceSnapshot.model_validate_json(row[0]) for row in rows]
