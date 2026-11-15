"""Local project storage: immutable source copies, SQLite jobs and timeline history."""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter

from ageneditor.contracts import (
    AcceptedIssue,
    Asset,
    AssetChecklist,
    AssetKind,
    AssetOrigin,
    AssetProvenance,
    AssetRole,
    Author,
    EngineError,
    EngineException,
    ErrorCode,
    Job,
    JobKind,
    JobStatus,
    ProjectBrief,
    Provenance,
    QAReport,
    RenderProfile,
    Timeline,
    Transcript,
    ValidationIssue,
    validate_against_assets,
)
from ageneditor.contracts.common import ProjectRelativePath
from ageneditor.media.hashing import sha256_file
from ageneditor.media.probe import probe
from ageneditor.media.ranges import source_interval
from ageneditor.timebase import seconds_to_frames_round


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


_PATH_ADAPTER = TypeAdapter(ProjectRelativePath)
_UNSAFE_FILENAME = re.compile(r"[^\w.\- ()\[\]]+")
_STILL_IMAGE_CODECS = {".png": "png", ".jpg": "mjpeg", ".jpeg": "mjpeg"}


def _safe_filename(name: str) -> str:
    """Keep a readable, path-safe copy of the source file name (it is display-only after import)."""
    cleaned = _UNSAFE_FILENAME.sub("_", Path(name).name)[:180].rstrip(". ")
    return cleaned or "media"


class ProjectStore:
    """SQLite-backed project store. Every resolved project path is checked at I/O time."""

    def __init__(self, root: Path):
        root = root.expanduser()
        if root.is_symlink() or root.is_junction():
            raise EngineException(ErrorCode.PERMISSION_DENIED, "project root must not be a link or junction")
        self.root = root.expanduser().resolve()
        self.db_path = self.root / "project.sqlite3"
        if self.db_path.is_symlink() or self.db_path.is_junction():
            raise EngineException(ErrorCode.PERMISSION_DENIED, "project database must not be a link")
        if not self.db_path.is_file():
            raise EngineException(ErrorCode.PROJECT_NOT_FOUND, "project database does not exist")
        self._init_db()

    @classmethod
    def create(cls, parent: Path, name: str) -> ProjectStore:
        if not name.strip() or len(name) > 80:
            raise EngineException(ErrorCode.INVALID_ARGUMENT, "project name must be 1 to 80 characters")
        parent = parent.expanduser().resolve()
        if not parent.is_dir():
            raise EngineException(ErrorCode.PERMISSION_DENIED, "project parent directory does not exist")
        project_id = _id("project")
        safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip()).strip("-")[:48] or "project"
        root = parent / f"{safe_name}-{project_id[-8:]}"
        root.mkdir()
        for folder in (
            "imports",
            "originals",
            "timelines",
            "renders",
            "reports",
            "proxies",
            "thumbnails",
            "transcripts",
        ):
            (root / folder).mkdir()
        store = cls.__new__(cls)
        store.root, store.db_path = root.resolve(), root / "project.sqlite3"
        store._init_db()
        with store._connect() as db:
            db.execute(
                "INSERT INTO project VALUES (?, ?, ?, ?)",
                (project_id, name, datetime.now(UTC).isoformat(), 0),
            )
        return store

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Commit on success, roll back on error, and always close.

        ``sqlite3.Connection``'s own context manager only handles the transaction; without the explicit
        close, handles linger until garbage collection and keep project files locked on Windows.
        """
        db = sqlite3.connect(self.db_path, timeout=30)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db
        finally:
            db.close()

    def _init_db(self) -> None:
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS project(
                    id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    created_at TEXT NOT NULL, current_revision INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS removed_assets(
                    asset_id TEXT PRIMARY KEY, removed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS asset_checklist(
                    project_id TEXT PRIMARY KEY, version INTEGER NOT NULL, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_brief(
                    project_id TEXT PRIMARY KEY, version INTEGER NOT NULL, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS timelines(
                    revision INTEGER PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS qa_reports(id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS transcripts(
                    id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, source_sha256 TEXT NOT NULL,
                    model_preset TEXT NOT NULL, data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS transcripts_asset
                    ON transcripts(asset_id, source_sha256, model_preset);
                CREATE TABLE IF NOT EXISTS edit_plan_attempts(
                    id TEXT PRIMARY KEY, revision INTEGER NOT NULL, result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_events(
                    id TEXT PRIMARY KEY, action TEXT NOT NULL, args_json TEXT NOT NULL,
                    result_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
            """)

    @property
    def project_id(self) -> str:
        with self._connect() as db:
            row = db.execute("SELECT id FROM project LIMIT 1").fetchone()
        if row is None:
            raise EngineException(ErrorCode.PROJECT_NOT_FOUND, "project metadata is missing")
        return str(row[0])

    def safe_path(self, relative: str) -> Path:
        try:
            relative = _PATH_ADAPTER.validate_python(relative)
        except ValueError as exc:
            raise EngineException(ErrorCode.PERMISSION_DENIED, "invalid project-relative path") from exc
        candidate = self.root.joinpath(*relative.split("/"))
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(self.root) or candidate.is_symlink():
            raise EngineException(ErrorCode.PERMISSION_DENIED, "project path escapes through a symlink")
        # Reject symlinks in every existing ancestor, including junctions resolved outside root.
        current = self.root
        for part in relative.split("/"):
            current = current / part
            if current.is_symlink():
                raise EngineException(ErrorCode.PERMISSION_DENIED, "symlink paths are not allowed")
        return resolved

    def write_artifact(self, relative: str, content: str) -> Path:
        path = self.safe_path(relative)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.safe_path(f"{relative}.tmp")
            temporary.write_text(content + "\n", encoding="utf-8")
            temporary.replace(path)
        except OSError as exc:
            raise EngineException(ErrorCode.PERMISSION_DENIED, "could not write project artifact") from exc
        return path

    def record_agent_event(
        self, action: str, redacted_args: dict[str, str | int | bool], result: str
    ) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO agent_events VALUES (?, ?, ?, ?, ?)",
                (
                    _id("event"),
                    action,
                    json.dumps(redacted_args, sort_keys=True),
                    json.dumps({"status": result}),
                    datetime.now(UTC).isoformat(),
                ),
            )

    @contextmanager
    def audited(self, action: str, **redacted_args: str | int | bool) -> Iterator[None]:
        try:
            yield
        except EngineException as exc:
            self.record_agent_event(action, redacted_args, exc.error.code.value)
            raise
        else:
            self.record_agent_event(action, redacted_args, "ok")

    def import_asset(self, source: Path, role: AssetRole, provenance: AssetProvenance | None = None) -> Asset:
        provenance = provenance or AssetProvenance()
        source = source.expanduser().resolve(strict=False)
        if not source.is_file():
            raise EngineException(ErrorCode.ASSET_NOT_FOUND, "source is not a regular file")
        try:
            before_hash = sha256_file(source)
        except OSError as exc:
            raise EngineException(ErrorCode.PERMISSION_DENIED, "source could not be read") from exc
        metadata = probe(source)
        image_codec = _STILL_IMAGE_CODECS.get(source.suffix.lower())
        if image_codec is not None:
            if metadata.video is None or metadata.video.codec != image_codec or metadata.audio is not None:
                raise EngineException(ErrorCode.UNSUPPORTED_CODEC, "image must be a PNG or JPEG still")
            kind = AssetKind.IMAGE
        else:
            kind = AssetKind.VIDEO if metadata.video else AssetKind.AUDIO
        if kind is AssetKind.VIDEO and role in {AssetRole.MUSIC, AssetRole.SFX}:
            raise EngineException(ErrorCode.INVALID_ARGUMENT, f"video asset cannot have {role} role")
        if kind is AssetKind.AUDIO and role not in {AssetRole.MUSIC, AssetRole.REFERENCE, AssetRole.SFX}:
            raise EngineException(
                ErrorCode.INVALID_ARGUMENT, "audio-only assets must use music, sfx or reference role"
            )
        if kind is AssetKind.IMAGE and role not in {AssetRole.BROLL, AssetRole.LOGO, AssetRole.REFERENCE}:
            raise EngineException(
                ErrorCode.INVALID_ARGUMENT, "still images need B-roll, logo or reference role"
            )
        asset_id = _id("asset")
        filename = _safe_filename(source.name)
        relative = f"originals/{asset_id}_{filename}"
        destination = self.safe_path(relative)
        from ageneditor.maintenance import ensure_free_space

        ensure_free_space(destination.parent, source.stat().st_size, "importing this source")
        try:
            shutil.copyfile(source, destination)
        except OSError as exc:
            destination.unlink(missing_ok=True)
            raise EngineException(ErrorCode.PERMISSION_DENIED, "could not copy source into project") from exc
        try:
            digest = sha256_file(destination)
            after_hash = sha256_file(source)
        except OSError as exc:
            destination.unlink(missing_ok=True)
            raise EngineException(
                ErrorCode.SOURCE_CHANGED, "source became unreadable while importing"
            ) from exc
        if digest != before_hash or after_hash != before_hash:
            destination.unlink(missing_ok=True)
            raise EngineException(ErrorCode.SOURCE_CHANGED, "source changed while importing")
        try:
            destination.chmod(destination.stat().st_mode & ~0o222)
        except OSError as exc:
            destination.unlink(missing_ok=True)
            raise EngineException(ErrorCode.PERMISSION_DENIED, "could not protect imported source") from exc
        asset = Asset(
            id=asset_id,
            project_id=self.project_id,
            kind=kind,
            role=role,
            relative_path=relative,
            original_filename=filename,
            sha256=digest,
            size_bytes=destination.stat().st_size,
            container=metadata.container,
            video=(
                metadata.video.model_copy(update={"duration_frames": 1, "is_vfr": False})
                if kind is AssetKind.IMAGE and metadata.video
                else metadata.video
            ),
            audio=metadata.audio,
            provenance=provenance,
        )
        with self._connect() as db:
            db.execute("INSERT INTO assets VALUES (?, ?)", (asset.id, asset.model_dump_json()))
        return asset

    def register_import(
        self, relative_path: str, role: AssetRole, provenance: AssetProvenance | None = None
    ) -> Asset:
        if (
            relative_path.startswith("/")
            or "\\" in relative_path
            or ":" in relative_path
            or "\x00" in relative_path
            or any(part in {"", ".", ".."} for part in relative_path.split("/"))
        ):
            raise EngineException(
                ErrorCode.PERMISSION_DENIED, "import path must stay inside the project imports folder"
            )
        return self.import_asset(self.safe_path(f"imports/{relative_path}"), role, provenance)

    def assets(self) -> dict[str, Asset]:
        """Every asset ever registered, removed ones included, so earlier revisions still render."""
        with self._connect() as db:
            rows = db.execute("SELECT data FROM assets").fetchall()
        return {a.id: a for row in rows if (a := Asset.model_validate_json(row[0]))}

    def removed_asset_ids(self) -> set[str]:
        with self._connect() as db:
            return {str(row[0]) for row in db.execute("SELECT asset_id FROM removed_assets")}

    def active_assets(self) -> dict[str, Asset]:
        """Assets the person has not removed: what the UI lists and the editor may use."""
        removed = self.removed_asset_ids()
        return {asset_id: asset for asset_id, asset in self.assets().items() if asset_id not in removed}

    def remove_asset(self, asset_id: str) -> None:
        """Take an asset out of the project without deleting anything.

        The read-only copy under ``originals/`` stays, because earlier revisions and renders may
        cite it; the person's own file was never touched. The asset disappears from listings and
        new revisions may not use it. Refused while the current timeline or a checklist link uses it.
        """
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM assets WHERE id=?", (asset_id,)).fetchone() is None:
                raise EngineException(ErrorCode.ASSET_NOT_FOUND, "asset does not exist")
            if db.execute("SELECT 1 FROM removed_assets WHERE asset_id=?", (asset_id,)).fetchone():
                raise EngineException(ErrorCode.ASSET_NOT_FOUND, "asset was already removed")
            revision = int(db.execute("SELECT current_revision FROM project").fetchone()[0])
            row = db.execute("SELECT data FROM timelines WHERE revision=?", (revision,)).fetchone()
            if row is not None:
                timeline = Timeline.model_validate_json(row[0])
                cited = {clip.asset_id for clip in timeline.clips} | {
                    cue.source_asset_id for cue in timeline.captions
                }
                if asset_id in cited:
                    raise EngineException(
                        ErrorCode.PERMISSION_DENIED,
                        f"this file is used in the current edit (revision {revision}); "
                        "ask Claude to take it out first",
                    )
            if asset_id in self._checklist_in(db).links.values():
                raise EngineException(
                    ErrorCode.PERMISSION_DENIED, "this file is linked to an asset request; unlink it first"
                )
            db.execute("INSERT INTO removed_assets VALUES (?, ?)", (asset_id, datetime.now(UTC).isoformat()))

    def _assets_cited_by_revisions(self) -> set[str]:
        """Every asset any stored revision's clips or captions cite."""
        with self._connect() as db:
            rows = db.execute("SELECT data FROM timelines").fetchall()
        cited: set[str] = set()
        for row in rows:
            data = json.loads(row[0])
            cited.update(clip["asset_id"] for clip in data.get("clips", []))
            cited.update(cue["source_asset_id"] for cue in data.get("captions", []))
        return cited

    def removed_asset_issues(self, timeline: Timeline) -> list[ValidationIssue]:
        """Errors for clips or captions that newly cite an asset the person removed.

        An asset that an earlier revision already used may stay in revisions derived from it (revert,
        patches), so history can always be restored; removal only stops brand-new uses.
        """
        removed = self.removed_asset_ids()
        if removed:
            removed -= self._assets_cited_by_revisions()
        issues = [
            ValidationIssue(
                code=ErrorCode.ASSET_NOT_FOUND,
                message=f"asset {clip.asset_id!r} was removed from the project",
                clip_id=clip.clip_id,
            )
            for clip in timeline.clips
            if clip.asset_id in removed
        ]
        issues.extend(
            ValidationIssue(
                code=ErrorCode.ASSET_NOT_FOUND,
                message=f"caption {cue.cue_id} cites removed asset {cue.source_asset_id!r}",
            )
            for cue in timeline.captions
            if cue.source_asset_id in removed
        )
        return issues

    def _checklist_in(self, db: sqlite3.Connection) -> AssetChecklist:
        project = db.execute("SELECT id FROM project LIMIT 1").fetchone()
        if project is None:
            raise EngineException(ErrorCode.PROJECT_NOT_FOUND, "project metadata is missing")
        project_id = str(project[0])
        row = db.execute("SELECT data FROM asset_checklist WHERE project_id=?", (project_id,)).fetchone()
        return (
            AssetChecklist.model_validate_json(row[0])
            if row is not None
            else AssetChecklist(project_id=project_id)
        )

    def asset_checklist(self) -> AssetChecklist:
        with self._connect() as db:
            return self._checklist_in(db)

    def _validate_checklist_links(self, db: sqlite3.Connection, checklist: AssetChecklist) -> None:
        requests = {request.id: request for request in checklist.requests}
        linked_assets: set[str] = set()
        for request_id, link in checklist.links.items():
            if link == "dropped":
                continue
            if link in linked_assets:
                raise EngineException(ErrorCode.INVALID_ARGUMENT, "one asset cannot satisfy two requests")
            linked_assets.add(link)
            if db.execute("SELECT 1 FROM removed_assets WHERE asset_id=?", (link,)).fetchone():
                raise EngineException(ErrorCode.ASSET_NOT_FOUND, "that file was removed from the project")
            row = db.execute("SELECT data FROM assets WHERE id=?", (link,)).fetchone()
            if row is None:
                raise EngineException(ErrorCode.ASSET_NOT_FOUND, "checklist asset is not registered")
            asset = Asset.model_validate_json(row[0])
            if (
                asset.project_id != checklist.project_id
                or asset.provenance.origin is not AssetOrigin.GENERATED_EXTERNAL
                or asset.kind.value != requests[request_id].kind
                or (requests[request_id].kind == "video" and asset.role is not AssetRole.BROLL)
            ):
                raise EngineException(
                    ErrorCode.INVALID_ARGUMENT,
                    "checklist links need a matching AI-generated (external) asset",
                )

    def save_asset_checklist(self, candidate: AssetChecklist, expected_version: int) -> AssetChecklist:
        if candidate.project_id != self.project_id:
            raise EngineException(ErrorCode.PERMISSION_DENIED, "checklist belongs to another project")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._checklist_in(db)
            if current.version != expected_version:
                raise EngineException(
                    ErrorCode.REVISION_CONFLICT,
                    "asset checklist changed; refresh it before saving",
                    current_version=current.version,
                )
            updated = candidate.model_copy(update={"version": current.version + 1})
            self._validate_checklist_links(db, updated)
            db.execute(
                "INSERT INTO asset_checklist(project_id, version, data) VALUES (?, ?, ?) "
                "ON CONFLICT(project_id) DO UPDATE SET version=excluded.version, data=excluded.data",
                (self.project_id, updated.version, updated.model_dump_json()),
            )
        return updated

    def link_asset_request(self, request_id: str, asset_id: str, expected_version: int) -> AssetChecklist:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._checklist_in(db)
            if current.version != expected_version:
                raise EngineException(
                    ErrorCode.REVISION_CONFLICT,
                    "asset checklist changed; refresh it before linking",
                    current_version=current.version,
                )
            if not current.configured or request_id not in {request.id for request in current.requests}:
                raise EngineException(ErrorCode.INVALID_ARGUMENT, "asset request does not exist")
            if request_id in current.links:
                raise EngineException(ErrorCode.INVALID_ARGUMENT, "reset the request before linking an asset")
            self._require_storyboard_ready(current, request_id)
            updated = current.model_copy(
                update={"version": current.version + 1, "links": {**current.links, request_id: asset_id}}
            )
            self._validate_checklist_links(db, updated)
            db.execute(
                "UPDATE asset_checklist SET version=?, data=? WHERE project_id=?",
                (updated.version, updated.model_dump_json(), self.project_id),
            )
        return updated

    @staticmethod
    def _require_storyboard_ready(current: AssetChecklist, request_id: str) -> None:
        storyboard = f"{request_id}_storyboard"
        if (
            current.review_required
            and storyboard in {request.id for request in current.requests}
            and storyboard not in current.approved
            and current.links.get(storyboard) != "dropped"
        ):
            raise EngineException(
                ErrorCode.INVALID_ARGUMENT, "approve (or skip) the storyboard before adding this video"
            )

    @staticmethod
    def _require_video_not_made(current: AssetChecklist, request_id: str) -> None:
        video = request_id.removesuffix("_storyboard")
        if request_id.endswith("_storyboard") and current.links.get(video) not in (None, "dropped"):
            raise EngineException(
                ErrorCode.INVALID_ARGUMENT, "reset the video first; it was made from this storyboard"
            )

    def replace_asset_request_link(
        self, request_id: str, asset_id: str, expected_version: int
    ) -> AssetChecklist:
        """Swap a request's file for a new one in one step, so a failure keeps the old file and approval."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._checklist_in(db)
            self._require_checklist_version(current, expected_version, "replacing the file")
            if not current.configured or request_id not in {request.id for request in current.requests}:
                raise EngineException(ErrorCode.INVALID_ARGUMENT, "asset request does not exist")
            self._require_video_not_made(current, request_id)
            self._require_storyboard_ready(current, request_id)
            updated = current.model_copy(
                update={
                    "version": current.version + 1,
                    "links": {**current.links, request_id: asset_id},
                    "approved": [key for key in current.approved if key != request_id],
                    "accepted": {key: value for key, value in current.accepted.items() if key != request_id},
                }
            )
            self._validate_checklist_links(db, updated)
            self._save_checklist_in(db, updated)
        return updated

    def approve_asset_request(self, request_id: str, expected_version: int) -> AssetChecklist:
        """Record the person's approval of the file linked to a request."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._checklist_in(db)
            self._require_checklist_version(current, expected_version, "approving")
            link = current.links.get(request_id)
            if link is None or link == "dropped":
                raise EngineException(
                    ErrorCode.INVALID_ARGUMENT, "add a file to this request before approving it"
                )
            if request_id in current.approved:
                return current
            updated = current.model_copy(
                update={"version": current.version + 1, "approved": [*current.approved, request_id]}
            )
            self._save_checklist_in(db, updated)
        return updated

    def reset_asset_request(self, request_id: str, expected_version: int) -> AssetChecklist:
        """Clear a request's link, approval and accepted issue so a new file can be added."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._checklist_in(db)
            self._require_checklist_version(current, expected_version, "resetting")
            if request_id not in {request.id for request in current.requests}:
                raise EngineException(ErrorCode.INVALID_ARGUMENT, "asset request does not exist")
            if (
                request_id not in current.links
                and request_id not in current.approved
                and request_id not in current.accepted
            ):
                return current
            self._require_video_not_made(current, request_id)
            links = {key: value for key, value in current.links.items() if key != request_id}
            updated = current.model_copy(
                update={
                    "version": current.version + 1,
                    "links": links,
                    "approved": [key for key in current.approved if key != request_id],
                    "accepted": {key: value for key, value in current.accepted.items() if key != request_id},
                }
            )
            self._save_checklist_in(db, updated)
        return updated

    @staticmethod
    def _require_checklist_version(current: AssetChecklist, expected: int, action: str) -> None:
        if current.version != expected:
            raise EngineException(
                ErrorCode.REVISION_CONFLICT,
                f"asset checklist changed; refresh it before {action}",
                current_version=current.version,
            )

    def _save_checklist_in(self, db: sqlite3.Connection, checklist: AssetChecklist) -> None:
        db.execute(
            "UPDATE asset_checklist SET version=?, data=? WHERE project_id=?",
            (checklist.version, checklist.model_dump_json(), self.project_id),
        )

    def brief(self) -> ProjectBrief:
        with self._connect() as db:
            return self._brief_in(db)

    def _brief_in(self, db: sqlite3.Connection) -> ProjectBrief:
        row = db.execute("SELECT data FROM project_brief WHERE project_id=?", (self.project_id,)).fetchone()
        return (
            ProjectBrief.model_validate_json(row[0])
            if row is not None
            else ProjectBrief(project_id=self.project_id)
        )

    def save_brief(self, candidate: ProjectBrief, expected_version: int) -> ProjectBrief:
        """Replace the brief. Written by a person; agents only read it."""
        if candidate.project_id != self.project_id:
            raise EngineException(ErrorCode.PERMISSION_DENIED, "brief belongs to another project")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._brief_in(db)
            if current.version != expected_version:
                raise EngineException(
                    ErrorCode.REVISION_CONFLICT,
                    "brief changed; refresh it before saving",
                    current_version=current.version,
                )
            updated = candidate.model_copy(update={"version": current.version + 1})
            db.execute(
                "INSERT INTO project_brief(project_id, version, data) VALUES (?, ?, ?) "
                "ON CONFLICT(project_id) DO UPDATE SET version=excluded.version, data=excluded.data",
                (self.project_id, updated.version, updated.model_dump_json()),
            )
        return updated

    def accept_asset_issue(self, request_id: str, note: str | None, expected_version: int) -> AssetChecklist:
        """Record (or, with ``note=None``, clear) acceptance of a linked file with a known issue.

        A person decides this; there is deliberately no MCP tool for it.
        """
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._checklist_in(db)
            if current.version != expected_version:
                raise EngineException(
                    ErrorCode.REVISION_CONFLICT,
                    "asset checklist changed; refresh it before accepting an issue",
                    current_version=current.version,
                )
            link = current.links.get(request_id)
            if link is None or link == "dropped":
                raise EngineException(
                    ErrorCode.INVALID_ARGUMENT, "link a file to the request before accepting an issue"
                )
            accepted = {key: value for key, value in current.accepted.items() if key != request_id}
            if note is not None:
                accepted[request_id] = AcceptedIssue(asset_id=link, note=note)
            updated = AssetChecklist.model_validate(
                {**current.model_dump(), "version": current.version + 1, "accepted": accepted}
            )
            db.execute(
                "UPDATE asset_checklist SET version=?, data=? WHERE project_id=?",
                (updated.version, updated.model_dump_json(), self.project_id),
            )
        return updated

    def require_asset_requests_ready(self) -> None:
        with self._connect() as db:
            self._require_asset_requests_ready_in(db)

    def _require_asset_requests_ready_in(self, db: sqlite3.Connection) -> None:
        checklist = self._checklist_in(db)
        if not checklist.configured:
            return
        self._validate_checklist_links(db, checklist)
        if any(request.id not in checklist.links for request in checklist.requests):
            raise EngineException(
                ErrorCode.INVALID_ARGUMENT,
                "asset requests are pending; import and link each file or drop its request",
            )
        if checklist.review_required and any(
            checklist.links[request.id] != "dropped" and request.id not in checklist.approved
            for request in checklist.requests
        ):
            raise EngineException(
                ErrorCode.INVALID_ARGUMENT,
                "asset requests are waiting for the person to approve each file or skip it",
            )

    def set_asset_labels(self, asset_id: str, labels: list[str]) -> Asset:
        """Replace user-supplied shot labels. Media and hash stay untouched."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM assets WHERE id=?", (asset_id,)).fetchone()
            if row is None:
                raise EngineException(ErrorCode.ASSET_NOT_FOUND, "asset is not registered")
            current = Asset.model_validate_json(row[0])
            updated = Asset.model_validate({**current.model_dump(mode="json"), "labels": labels})
            db.execute("UPDATE assets SET data=? WHERE id=?", (updated.model_dump_json(), asset_id))
        return updated

    def current_revision(self) -> int:
        with self._connect() as db:
            return int(db.execute("SELECT current_revision FROM project").fetchone()[0])

    def timeline(self, revision: int | None = None) -> Timeline:
        revision = self.current_revision() if revision is None else revision
        with self._connect() as db:
            row = db.execute("SELECT data FROM timelines WHERE revision=?", (revision,)).fetchone()
        if row is None:
            raise EngineException(
                ErrorCode.INVALID_TIMELINE, "timeline revision does not exist", revision=revision
            )
        return Timeline.model_validate_json(row[0])

    def validation_issues(self, timeline: Timeline) -> list[ValidationIssue]:
        assets = self.assets()
        issues = validate_against_assets(timeline, assets)
        if timeline.project_id != self.project_id:
            issues.append(
                ValidationIssue(
                    code=ErrorCode.PERMISSION_DENIED, message="timeline belongs to another project"
                )
            )
        if timeline.captions or timeline.word_corrections:
            from ageneditor.captions import caption_evidence_issues

            issues.extend(caption_evidence_issues(timeline, assets, self.transcript))
        for clip in timeline.clips:
            asset = assets.get(clip.asset_id)
            if asset is None or asset.video is None or not asset.video.is_vfr:
                continue
            try:
                start, end = source_interval(
                    asset,
                    clip,
                    self.safe_path(asset.relative_path),
                    timeline.render_profile.frame_rate.fraction,
                )
            except EngineException as exc:
                issues.append(
                    ValidationIssue(code=exc.error.code, message=exc.error.message, clip_id=clip.clip_id)
                )
                continue
            expected = seconds_to_frames_round(end - start, timeline.render_profile.frame_rate)
            if clip.timeline_span_frames != expected:
                issues.append(
                    ValidationIssue(
                        code=ErrorCode.INVALID_TIMELINE,
                        message=f"clip needs {expected} output frames; set timeline_duration_frames",
                        clip_id=clip.clip_id,
                    )
                )
        return issues

    def commit_timeline(self, candidate: Timeline, expected_revision: int) -> Timeline:
        candidate = Timeline.model_validate(candidate.model_dump(mode="json"))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_asset_requests_ready_in(db)
            row = db.execute("SELECT id, current_revision FROM project").fetchone()
            if candidate.project_id != row["id"]:
                raise EngineException(ErrorCode.PERMISSION_DENIED, "timeline belongs to another project")
            if row["current_revision"] != expected_revision:
                raise EngineException(
                    ErrorCode.REVISION_CONFLICT,
                    "project revision changed",
                    current_revision=row["current_revision"],
                )
            issues = [*self.validation_issues(candidate), *self.removed_asset_issues(candidate)]
            errors = [item for item in issues if item.severity == "error"]
            if errors:
                raise EngineException(
                    ErrorCode.INVALID_TIMELINE, "timeline failed asset validation", issues=len(errors)
                )
            committed = candidate.model_copy(
                update={
                    "revision": expected_revision + 1,
                    "parent_revision": expected_revision,
                }
            )
            db.execute(
                "INSERT INTO timelines VALUES (?, ?, ?)",
                (committed.revision, committed.model_dump_json(), datetime.now(UTC).isoformat()),
            )
            db.execute("UPDATE project SET current_revision=?", (committed.revision,))
            self.write_artifact(f"timelines/r{committed.revision}.json", committed.model_dump_json(indent=2))
            return committed

    def start_job(self, job: Job) -> None:
        with self._connect() as db:
            if job.kind in {JobKind.FINAL_RENDER, JobKind.PREVIEW_RENDER}:
                db.execute("BEGIN IMMEDIATE")
                self._require_asset_requests_ready_in(db)
            db.execute("INSERT INTO jobs VALUES (?, ?)", (job.id, job.model_dump_json()))

    def update_job(self, job: Job) -> None:
        with self._connect() as db:
            db.execute("UPDATE jobs SET data=? WHERE id=?", (job.model_dump_json(), job.id))

    def get_job(self, job_id: str) -> Job:
        with self._connect() as db:
            row = db.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise EngineException(ErrorCode.JOB_NOT_FOUND, "job does not exist", job_id=job_id)
        return Job.model_validate_json(row[0])

    def get_qa_report(self, job_id: str) -> QAReport:
        with self._connect() as db:
            rows = db.execute("SELECT data FROM qa_reports ORDER BY rowid DESC").fetchall()
        for row in rows:
            stored = json.loads(row[0])
            stored.pop("overall", None)
            report = QAReport.model_validate(stored)
            if report.job_id == job_id:
                return report
        raise EngineException(ErrorCode.QA_FAILED, "QA report does not exist", job_id=job_id)

    def save_transcript(self, transcript: Transcript) -> None:
        if transcript.project_id != self.project_id:
            raise EngineException(ErrorCode.PERMISSION_DENIED, "transcript belongs to another project")
        asset = self.assets().get(transcript.asset_id)
        if asset is None or asset.sha256 != transcript.source_sha256:
            raise EngineException(ErrorCode.SOURCE_CHANGED, "transcript source hash does not match")
        if sha256_file(self.safe_path(asset.relative_path)) != asset.sha256:
            raise EngineException(ErrorCode.SOURCE_CHANGED, "registered source hash changed")
        self.write_artifact(f"transcripts/{transcript.id}.json", transcript.model_dump_json(indent=2))
        with self._connect() as db:
            db.execute(
                "INSERT INTO transcripts VALUES (?, ?, ?, ?, ?)",
                (
                    transcript.id,
                    transcript.asset_id,
                    transcript.source_sha256,
                    transcript.model_preset.value,
                    transcript.model_dump_json(),
                ),
            )

    def transcript(self, transcript_id: str) -> Transcript:
        with self._connect() as db:
            row = db.execute("SELECT data FROM transcripts WHERE id=?", (transcript_id,)).fetchone()
        if row is None:
            raise EngineException(ErrorCode.TRANSCRIPT_NOT_FOUND, "transcript does not exist")
        return Transcript.model_validate_json(row[0])

    def cached_transcript(self, asset_id: str, source_hash: str, model_preset: str) -> Transcript | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT data FROM transcripts WHERE asset_id=? AND source_sha256=? AND model_preset=? "
                "ORDER BY rowid DESC LIMIT 1",
                (asset_id, source_hash, model_preset),
            ).fetchone()
        return Transcript.model_validate_json(row[0]) if row else None

    def latest_transcript(self, asset_id: str, source_hash: str) -> Transcript | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT data FROM transcripts WHERE asset_id=? AND source_sha256=? "
                "ORDER BY rowid DESC LIMIT 1",
                (asset_id, source_hash),
            ).fetchone()
        return Transcript.model_validate_json(row[0]) if row else None

    def reserve_plan_attempt(self, revision: int, max_attempts: int) -> tuple[str, int]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = int(
                db.execute(
                    "SELECT count(*) FROM edit_plan_attempts WHERE revision=?", (revision,)
                ).fetchone()[0]
            )
            if count >= max_attempts:
                raise EngineException(
                    ErrorCode.PLAN_RETRY_LIMIT, "plan retry cap reached; manual review required"
                )
            attempt_id = _id("plan")
            db.execute(
                "INSERT INTO edit_plan_attempts VALUES (?, ?, ?, ?)",
                (attempt_id, revision, "{}", datetime.now(UTC).isoformat()),
            )
            return attempt_id, count + 1

    def complete_plan_attempt(self, attempt_id: str, result_json: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE edit_plan_attempts SET result_json=? WHERE id=?", (result_json, attempt_id))

    def recover_interrupted_jobs(self) -> None:
        with self._connect() as db:
            rows = db.execute("SELECT id, data FROM jobs").fetchall()
        for row in rows:
            job = Job.model_validate_json(row["data"])
            if job.status in {JobStatus.RUNNING, JobStatus.QUEUED}:
                job = job.model_copy(
                    update={
                        "status": JobStatus.INTERRUPTED,
                        "ended_at": datetime.now(UTC),
                        "error": EngineError(
                            code=(
                                ErrorCode.TRANSCRIPTION_FAILED
                                if job.kind is JobKind.TRANSCRIBE
                                else ErrorCode.RENDER_FAILED
                            ),
                            message="engine stopped while job was running",
                        ),
                    }
                )
                self.update_job(job)

    def create_timeline(self, profile: RenderProfile, target_duration_frames: int) -> Timeline:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT data FROM timelines WHERE revision=0").fetchone()
            if existing is not None:
                return Timeline.model_validate_json(existing[0])
            candidate = Timeline(
                project_id=self.project_id,
                revision=0,
                target_duration_frames=target_duration_frames,
                render_profile=profile,
                provenance=Provenance(author=Author.USER, summary="Initial empty timeline"),
            )
            db.execute(
                "INSERT INTO timelines VALUES (0, ?, ?)",
                (candidate.model_dump_json(), datetime.now(UTC).isoformat()),
            )
            self.write_artifact("timelines/r0.json", candidate.model_dump_json(indent=2))
            return candidate
