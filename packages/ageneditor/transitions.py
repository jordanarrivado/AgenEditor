"""Which transition styles the editor agent may use. Workspace-wide; set only by the person."""

from __future__ import annotations

import threading
import uuid
from pathlib import Path

from ageneditor.contracts import EngineException, ErrorCode, Timeline
from ageneditor.contracts.transition import TRANSITION_PRESETS, TransitionSettings, TransitionStyle

SETTINGS_DIR = "_transitions"

STYLE_DESCRIPTIONS: dict[TransitionStyle, tuple[str, str]] = {
    TransitionStyle.CLASSIC: ("Classic", "Dissolves, dips to black or white, and wipes."),
    TransitionStyle.MODERN: ("Modern", "Slides, smooth wipes, covers, reveals and squeezes."),
    TransitionStyle.MONTAGE: ("Montage", "Fast, graphic cuts: zooms, slices, wind, pixelize, diagonals."),
    TransitionStyle.CINEMATIC: ("Cinematic", "Slow fades, blur dissolves, iris and bar reveals."),
}

_lock = threading.Lock()


class TransitionLibrary:
    def __init__(self, workspace: Path):
        self.path = workspace.expanduser().resolve() / SETTINGS_DIR / "settings.json"

    def load(self) -> TransitionSettings:
        if not self.path.is_file():
            return TransitionSettings()
        return TransitionSettings.model_validate_json(self.path.read_text(encoding="utf-8"))

    def set_active(self, style: TransitionStyle, active: bool) -> TransitionSettings:
        with _lock:
            current = set(self.load().active_styles)
            current = current | {style} if active else current - {style}
            settings = TransitionSettings(active_styles=[s for s in TransitionStyle if s in current])
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(f".{uuid.uuid4().hex[:8]}.tmp")
            temporary.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
            temporary.replace(self.path)
        return settings

    def styles(self) -> list[dict[str, object]]:
        """Every style with its active state, for the desktop panel."""
        active = set(self.load().active_styles)
        return [
            {
                "id": style.value,
                "label": STYLE_DESCRIPTIONS[style][0],
                "description": STYLE_DESCRIPTIONS[style][1],
                "active": style in active,
                "examples": [p.label for p in TRANSITION_PRESETS.values() if p.style is style][:4],
                "count": sum(p.style is style for p in TRANSITION_PRESETS.values()),
            }
            for style in TransitionStyle
        ]

    def catalog(self) -> dict[str, object]:
        """Transitions the agent may use: presets from active styles only."""
        active = self.load().active_styles
        return {
            "active_styles": [style.value for style in active],
            # Named so the agent can tell the user what to tick; their presets stay unusable.
            "inactive_styles": [
                {
                    "style": style.value,
                    "label": STYLE_DESCRIPTIONS[style][0],
                    "contains": STYLE_DESCRIPTIONS[style][1],
                }
                for style in TransitionStyle
                if style not in active
            ],
            "transitions": [
                {
                    "id": preset_id,
                    "label": info.label,
                    "style": info.style.value,
                    "suggested_frames": info.default_frames,
                }
                for preset_id, info in TRANSITION_PRESETS.items()
                if info.style in active
            ],
        }

    def check_new_transitions(self, before: Timeline | None, candidate: Timeline) -> None:
        """Agent commits may only add or change transitions from active styles."""
        active = set(self.load().active_styles)
        existing = {(t.after_clip_id, t.preset) for t in before.transitions} if before else set()
        for transition in candidate.transitions:
            if (transition.after_clip_id, transition.preset) in existing:
                continue
            if transition.info.style not in active:
                raise EngineException(
                    ErrorCode.PERMISSION_DENIED,
                    f"{transition.info.label} is a {transition.info.style.value} transition, and that style "
                    "is not active. Use list_transitions or ask the user to tick it.",
                )
