"""A clip-scoped view of a project store.

The existing engine services (``apply_patch``, ``revert_to``, caption building, B-roll suggestions,
rendering and technical QA) read and commit timelines through ``current_revision``/``timeline``/
``commit_timeline``. This view routes those calls to one production clip's own revision history, carrying
the worker's fencing token, and passes everything else (assets, transcripts, safe paths) to the project.
No second engine and no duplicated asset store.
"""

from __future__ import annotations

from typing import Any

from ageneditor.contracts import EngineException, ErrorCode, Timeline
from ageneditor.production.store import ProductionStore


class ClipTimelineStore:
    def __init__(
        self,
        production: ProductionStore,
        clip_id: str,
        attempt_id: str | None = None,
        fencing_token: int | None = None,
    ):
        self._production = production
        self._project = production.project
        self.clip_id = clip_id
        self.attempt_id = attempt_id
        self.fencing_token = fencing_token
        self.project_id = production.project_id
        self.root = self._project.root

    def __getattr__(self, name: str) -> Any:
        # Only called for attributes not defined here: assets, transcripts, safe paths, brief...
        return getattr(self._project, name)

    def current_revision(self) -> int:
        revision = self._production.clip(self.clip_id).current_revision
        if revision is None:
            raise EngineException(ErrorCode.INVALID_TIMELINE, "clip has no timeline yet")
        return revision

    def timeline(self, revision: int | None = None) -> Timeline:
        return self._production.clip_timeline(self.clip_id, revision)

    def commit_timeline(self, candidate: Timeline, expected_revision: int) -> Timeline:
        return self._production.commit_clip_timeline(
            self.clip_id, candidate, expected_revision, self.attempt_id, self.fencing_token
        )

    def create_timeline(self, *_: Any, **__: Any) -> Timeline:
        raise EngineException(ErrorCode.INVALID_ARGUMENT, "clip timelines are created by the planner")

    def reserve_plan_attempt(self, *_: Any, **__: Any) -> tuple[str, int]:
        raise EngineException(
            ErrorCode.INVALID_ARGUMENT, "use the clip plan, not submit_edit_plan, for clips"
        )


class PinnedTimelineStore(ClipTimelineStore):
    """Serves one fixed timeline object for its revision (e.g. with the preview render profile), so
    technical QA measures a preview against the profile it was actually rendered with."""

    def __init__(self, base: ClipTimelineStore, pinned: Timeline):
        super().__init__(base._production, base.clip_id, base.attempt_id, base.fencing_token)
        self._pinned = pinned

    def timeline(self, revision: int | None = None) -> Timeline:
        if revision is None or revision == self._pinned.revision:
            return self._pinned
        return super().timeline(revision)
