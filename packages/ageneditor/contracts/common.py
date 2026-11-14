"""Shared contract primitives: IDs, safe relative paths, error codes."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

SCHEMA_VERSION: Literal[1] = 1

#: Stable identifiers: lowercase, digits, ``_`` and ``-``; never paths or URLs.
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_\-]{0,63}$")]

Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

_DRIVE = re.compile(r"^[A-Za-z]:")
#: Windows device names are special in every directory and with any extension (``nul.mp4``).
_RESERVED = re.compile(r"^(con|prn|aux|nul|conin\$|conout\$|com[0-9¹²³]|lpt[0-9¹²³])(\..*)?$", re.IGNORECASE)


def _check_relative_path(value: str) -> str:
    """Reject anything that could escape the project root (spec §11).

    Accepts POSIX-style relative paths only: no absolute paths, drive letters,
    UNC prefixes, backslashes, ``..`` segments, empty segments or NUL bytes.
    Symlink escapes are checked at I/O time by the storage layer, not here.
    """
    if not value or "\x00" in value:
        raise ValueError("path must be a non-empty string")
    if "\\" in value:
        raise ValueError("use forward slashes in project-relative paths")
    if value.startswith("/") or _DRIVE.match(value) or PureWindowsPath(value).anchor:
        raise ValueError("path must be relative to the project root")
    parts = PurePosixPath(value).parts
    if any(p in ("..", ".", "") for p in value.split("/")) or not parts:
        raise ValueError("path must not contain '.', '..' or empty segments")
    if ":" in value or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError("path must not contain ':' (drives, alternate data streams) or control characters")
    for segment in value.split("/"):
        if segment != segment.rstrip(". "):
            raise ValueError("path segments must not end with '.' or a space (Windows strips them)")
        if _RESERVED.match(segment):
            raise ValueError("path segment is a reserved Windows device name")
    return value


#: A path relative to a project root, e.g. ``originals/interview.mp4``.
ProjectRelativePath = Annotated[str, AfterValidator(_check_relative_path)]


def utcnow() -> datetime:
    return datetime.now(UTC)


class ContractModel(BaseModel):
    """Base for all wire contracts: strict about unknown fields, JSON-friendly."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ErrorCode(StrEnum):
    """Structured error codes returned by engine services and MCP tools (spec §6)."""

    ASSET_NOT_FOUND = "ASSET_NOT_FOUND"
    RANGE_OUT_OF_BOUNDS = "RANGE_OUT_OF_BOUNDS"
    REVISION_CONFLICT = "REVISION_CONFLICT"
    SOURCE_CHANGED = "SOURCE_CHANGED"
    UNSUPPORTED_CODEC = "UNSUPPORTED_CODEC"
    RENDER_FAILED = "RENDER_FAILED"
    QA_FAILED = "QA_FAILED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    INVALID_TIMELINE = "INVALID_TIMELINE"
    PROBE_FAILED = "PROBE_FAILED"
    PROJECT_NOT_FOUND = "PROJECT_NOT_FOUND"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    JOB_CANCELED = "JOB_CANCELED"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    TRANSCRIPTION_FAILED = "TRANSCRIPTION_FAILED"
    TRANSCRIPT_NOT_FOUND = "TRANSCRIPT_NOT_FOUND"
    SOURCE_INSUFFICIENT = "SOURCE_INSUFFICIENT"
    PLAN_RETRY_LIMIT = "PLAN_RETRY_LIMIT"
    OUT_OF_CONTEXT = "OUT_OF_CONTEXT"
    INSUFFICIENT_DISK = "INSUFFICIENT_DISK"


class EngineError(ContractModel):
    code: ErrorCode
    message: str = Field(max_length=2000)
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class EngineException(Exception):
    """Raised by engine code; converts losslessly to an ``EngineError`` for the wire."""

    def __init__(self, code: ErrorCode, message: str, **details: str | int | float | bool | None):
        super().__init__(f"{code}: {message}")
        self.error = EngineError(code=code, message=message, details=details)
