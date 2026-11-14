"""B-roll suggestions from user-supplied shot labels (BROLL-001, first iteration).

Deterministic lexical matching between the labels a user typed on registered B-roll assets and the
spoken words placed on the timeline. No model indexing, no media generation, no network access.
Suggestions are proposals; placing one requires an explicit ``add_overlay`` revision.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from fractions import Fraction

from ageneditor.captions import map_words, select_transcripts
from ageneditor.contracts import AssetRole, Timeline
from ageneditor.storage import ProjectStore
from ageneditor.timebase import seconds_to_frames_round

METHOD = "lexical match of user shot labels against mapped transcript words"
MIN_SECONDS = Fraction(3, 2)
MAX_SECONDS = Fraction(4)
_STOPWORDS = frozenset(
    "a an and are as at be but by for from has have i in is it its me my of on or our so that the "
    "their them then there they this to was we were what when with you your".split()
)
_TOKEN = re.compile(r"[a-z0-9']+")


def _stem(token: str) -> str:
    token = token.strip("'")
    for suffix in ("ing", "es", "s"):
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def _tokens(text: str) -> set[str]:
    return {_stem(t) for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1}


@dataclass(frozen=True)
class BrollSuggestion:
    asset_id: str
    label: str
    matched_terms: list[str]
    word_ids: list[str]
    transcript_id: str
    timeline_in_frames: int
    timeline_out_frames: int
    source_in_frames: int
    source_out_frames: int
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def suggest_broll(store: ProjectStore, timeline: Timeline, limit: int = 10) -> list[BrollSuggestion]:
    assets = store.assets()
    broll = [a for a in assets.values() if a.role is AssetRole.BROLL and a.video and a.labels]
    if not broll:
        return []
    transcripts = select_transcripts(store, timeline)
    words = map_words(store, timeline, transcripts)
    fps = timeline.render_profile.frame_rate
    end_of_video = timeline.duration_frames
    suggestions: list[BrollSuggestion] = []
    used_spans: list[tuple[int, int]] = []
    for word_index, word in enumerate(words):
        word_terms = _tokens(word.text)
        if not word_terms:
            continue
        for asset in broll:
            for label in asset.labels:
                label_terms = _tokens(label)
                matched = sorted(word_terms & label_terms)
                if not matched:
                    continue
                source_rate = asset.video.frame_rate.fraction
                want = min(MAX_SECONDS, Fraction(asset.video.duration_frames, 1) / source_rate)
                span = seconds_to_frames_round(want, fps)
                if span < seconds_to_frames_round(MIN_SECONDS, fps):
                    continue
                start = word.in_frame
                end = min(start + span, end_of_video)
                if end - start < seconds_to_frames_round(MIN_SECONDS, fps):
                    continue
                if any(start < b and a < end for a, b in used_spans):
                    continue
                source_frames = seconds_to_frames_round(Fraction(end - start, 1) / fps.fraction, source_rate)
                nearby = [w.word_id for w in words[word_index : word_index + 6] if w.in_frame < end]
                suggestions.append(
                    BrollSuggestion(
                        asset_id=asset.id,
                        label=label,
                        matched_terms=matched,
                        word_ids=nearby,
                        transcript_id=word.transcript_id,
                        timeline_in_frames=start,
                        timeline_out_frames=end,
                        source_in_frames=0,
                        source_out_frames=min(source_frames, asset.video.duration_frames),
                        reason=f"spoken {word.text!r} matches shot label {label!r}",
                    )
                )
                used_spans.append((start, end))
                break
    return suggestions[:limit]
