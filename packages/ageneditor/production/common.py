"""Small helpers shared by the production, orchestration and quality packages."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

_TS = "%Y-%m-%dT%H:%M:%S.%fZ"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def utc_now() -> datetime:
    return datetime.now(UTC)


def ts(value: datetime) -> str:
    """Fixed-width UTC timestamp, so stored values also compare correctly as text."""
    return value.astimezone(UTC).strftime(_TS)


def parse_ts(value: str) -> datetime:
    return datetime.strptime(value, _TS).replace(tzinfo=UTC)


def canonical(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def content_hash(value: Any) -> str:
    """SHA-256 of a value's canonical JSON form."""
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()
