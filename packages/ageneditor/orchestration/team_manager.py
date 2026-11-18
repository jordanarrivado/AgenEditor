"""Adaptive pool of logical production teams (spec §9, D01, D03).

A team is an allocation that owns one clip at a time, not a set of permanently running model processes.
The pool grows up to the configured team slots as clips need editing, gives each batch a fair share when
several compete, and retires idle teams. A released team never carries context to its next clip: the
next assignment always starts a fresh team session.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from datetime import datetime

from ageneditor.production.common import new_id


@dataclass
class Team:
    id: str
    clip_key: tuple[str, str] | None = None
    batch_key: tuple[str, str] | None = None
    assigned_at: datetime | None = None
    clips_completed: int = 0
    history: list[str] = field(default_factory=list)


class TeamManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._teams: dict[str, Team] = {}

    def team_for(self, clip_key: tuple[str, str]) -> str | None:
        with self._lock:
            return next((team.id for team in self._teams.values() if team.clip_key == clip_key), None)

    def assign(
        self,
        clip_key: tuple[str, str],
        batch_key: tuple[str, str],
        capacity: int,
        waiting_batches: set[tuple[str, str]],
        batch_cap: int | None,
        now: datetime,
    ) -> str | None:
        """Give the clip a team if a slot and the batch's fair share allow. Returns the team ID or None."""
        with self._lock:
            for team in self._teams.values():
                if team.clip_key == clip_key:
                    return team.id
            busy = [team for team in self._teams.values() if team.clip_key is not None]
            if len(busy) >= capacity:
                return None
            competing = max(
                1, len(waiting_batches | {batch_key, *(t.batch_key for t in busy if t.batch_key)})
            )
            share = max(1, math.ceil(capacity / competing))
            held = sum(1 for team in busy if team.batch_key == batch_key)
            if held >= share or (batch_cap is not None and held >= batch_cap):
                return None
            # Fresh team ID per assignment: no transient session context survives a release.
            team = Team(id=new_id("team"), clip_key=clip_key, batch_key=batch_key, assigned_at=now)
            self._teams[team.id] = team
            return team.id

    def release(self, clip_key: tuple[str, str]) -> str | None:
        with self._lock:
            for team_id, team in list(self._teams.items()):
                if team.clip_key == clip_key:
                    del self._teams[team_id]
                    return team_id
        return None

    def active(self) -> list[dict[str, object]]:
        with self._lock:
            return [
                {
                    "team_id": team.id,
                    "project_id": team.clip_key[0] if team.clip_key else None,
                    "clip_id": team.clip_key[1] if team.clip_key else None,
                    "batch_id": team.batch_key[1] if team.batch_key else None,
                    "assigned_at": team.assigned_at.isoformat() if team.assigned_at else None,
                }
                for team in self._teams.values()
                if team.clip_key is not None
            ]
