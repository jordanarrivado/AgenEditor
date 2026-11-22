"""Source-linked captions: transcript words → output frames → two-line cues → SRT/ASS.

Every cue cites the transcript words it shows. Timing always comes from source word timestamps
mapped through the V1 cuts; manual corrections change text only and are versioned on the timeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from math import floor
from typing import TYPE_CHECKING

from ageneditor.contracts import (
    Asset,
    CaptionCue,
    CaptionPreset,
    EngineException,
    ErrorCode,
    Timeline,
    TrackId,
    Transcript,
    ValidationIssue,
)
from ageneditor.media.ranges import source_interval

if TYPE_CHECKING:
    from ageneditor.storage import ProjectStore

#: Readability limits (frames are derived from the profile rate).
MAX_LINES = 2
MAX_CUE_SECONDS = Fraction(3)
MIN_CUE_SECONDS = Fraction(7, 10)
MAX_WORD_GAP_SECONDS = Fraction(1, 2)
HOLD_SECONDS = Fraction(1, 5)
SENTENCE_END = (".", "?", "!")


@dataclass(frozen=True)
class MappedWord:
    transcript_id: str
    word_id: str
    asset_id: str
    clip_id: str
    text: str
    corrected: bool
    probability: float | None
    in_frame: int
    out_frame: int
    clip_out_frame: int


def chars_per_line(timeline: Timeline) -> int:
    profile = timeline.render_profile
    return 24 if profile.height > profile.width else 38


def _clean(text: str) -> str:
    return " ".join(text.split())


def corrected_text(timeline: Timeline) -> dict[tuple[str, str], str]:
    return {(fix.transcript_id, fix.word_id): _clean(fix.text) for fix in timeline.word_corrections}


def select_transcripts(
    store: ProjectStore, timeline: Timeline, transcript_ids: list[str] | None = None
) -> dict[str, Transcript]:
    """Map primary asset ID → transcript, honouring explicit pins, else the newest for the source hash."""
    assets = store.assets()
    pinned: dict[str, Transcript] = {}
    for transcript_id in transcript_ids or []:
        transcript = store.transcript(transcript_id)
        asset = assets.get(transcript.asset_id)
        if asset is None or asset.sha256 != transcript.source_sha256:
            raise EngineException(
                ErrorCode.SOURCE_CHANGED, "pinned transcript does not match its registered source"
            )
        pinned[asset.id] = transcript
    selected: dict[str, Transcript] = {}
    for clip in timeline.clips:
        if clip.track is not TrackId.V1 or clip.asset_id in selected:
            continue
        asset = assets[clip.asset_id]
        transcript = pinned.get(asset.id) or store.latest_transcript(asset.id, asset.sha256)
        if transcript is not None:
            selected[asset.id] = transcript
    return selected


def map_words(
    store: ProjectStore, timeline: Timeline, transcripts: dict[str, Transcript]
) -> list[MappedWord]:
    """Place each spoken word on the output grid. A word belongs to the V1 clip containing its midpoint."""
    assets = store.assets()
    fps = timeline.render_profile.frame_rate.fraction
    fixes = corrected_text(timeline)
    mapped: list[MappedWord] = []
    for clip in sorted(
        (c for c in timeline.clips if c.track is TrackId.V1), key=lambda c: c.timeline_in_frames
    ):
        transcript = transcripts.get(clip.asset_id)
        if transcript is None:
            continue
        asset = assets[clip.asset_id]
        start, end = source_interval(asset, clip, store.safe_path(asset.relative_path), fps)
        for word in transcript.words:
            w_start = Fraction(word.start_us, 1_000_000)
            w_end = Fraction(word.end_us, 1_000_000)
            if not start <= (w_start + w_end) / 2 < end:
                continue
            in_frame = clip.timeline_in_frames + floor((max(w_start, start) - start) * fps)
            out_frame = clip.timeline_in_frames + round((min(w_end, end) - start) * fps)
            out_frame = min(max(out_frame, in_frame + 1), clip.timeline_out_frames)
            in_frame = min(in_frame, out_frame - 1)
            key = (transcript.id, word.id)
            mapped.append(
                MappedWord(
                    transcript_id=transcript.id,
                    word_id=word.id,
                    asset_id=asset.id,
                    clip_id=clip.clip_id,
                    text=fixes.get(key, _clean(word.text)),
                    corrected=key in fixes,
                    probability=word.probability,
                    in_frame=in_frame,
                    out_frame=out_frame,
                    clip_out_frame=clip.timeline_out_frames,
                )
            )
    return mapped


def wrap_lines(words: list[str], limit: int) -> list[str] | None:
    """Balanced split into ≤ 2 lines of ≤ ``limit`` chars; ``None`` if it cannot fit."""
    single = " ".join(words)
    if len(single) <= limit:
        return [single]
    best: tuple[int, list[str]] | None = None
    for i in range(1, len(words)):
        top, bottom = " ".join(words[:i]), " ".join(words[i:])
        if len(top) <= limit and len(bottom) <= limit:
            width = max(len(top), len(bottom))
            if best is None or width < best[0]:
                best = (width, [top, bottom])
    return best[1] if best else None


def build_cues(timeline: Timeline, words: list[MappedWord]) -> list[CaptionCue]:
    fps = timeline.render_profile.frame_rate.fraction
    max_frames = round(MAX_CUE_SECONDS * fps)
    gap_frames = round(MAX_WORD_GAP_SECONDS * fps)
    limit = chars_per_line(timeline)
    groups: list[list[MappedWord]] = []
    for word in words:
        current = groups[-1] if groups else None
        if current is not None:
            last = current[-1]
            fits = wrap_lines([w.text for w in current] + [word.text], limit) is not None
            same_take = (
                last.clip_id == word.clip_id
                and word.in_frame - last.out_frame <= gap_frames
                and word.out_frame - current[0].in_frame <= max_frames
            )
            if fits and same_take and not last.text.endswith(SENTENCE_END):
                current.append(word)
                continue
        groups.append([word])
    cues: list[CaptionCue] = []
    min_frames = max(1, round(MIN_CUE_SECONDS * fps))
    hold = round(HOLD_SECONDS * fps)
    for index, group in enumerate(groups):
        next_in = groups[index + 1][0].in_frame if index + 1 < len(groups) else None
        ceiling = group[-1].clip_out_frame if next_in is None else min(next_in, group[-1].clip_out_frame)
        start = group[0].in_frame
        end = max(group[-1].out_frame + hold, start + min_frames)
        end = max(min(end, ceiling), group[-1].out_frame)
        lines = wrap_lines([w.text for w in group], limit) or [" ".join(w.text for w in group)]
        cues.append(
            CaptionCue(
                cue_id=f"cue_{index + 1:04d}",
                text="\n".join(lines),
                timeline_in_frames=start,
                timeline_out_frames=end,
                source_asset_id=group[0].asset_id,
                transcript_id=group[0].transcript_id,
                word_ids=[w.word_id for w in group],
                corrected=any(w.corrected for w in group),
            )
        )
    return cues


def build_captions(
    store: ProjectStore, timeline: Timeline, transcript_ids: list[str] | None = None
) -> list[CaptionCue]:
    transcripts = select_transcripts(store, timeline, transcript_ids)
    if not transcripts:
        raise EngineException(
            ErrorCode.TRANSCRIPT_NOT_FOUND, "no transcript exists for the primary clips' unchanged sources"
        )
    return build_cues(timeline, map_words(store, timeline, transcripts))


def caption_evidence_issues(
    timeline: Timeline, assets: dict[str, Asset], load_transcript
) -> list[ValidationIssue]:
    """Reject captions or corrections that cite words the registered transcript does not contain,
    and cue text that differs from the cited (optionally corrected) words."""
    issues: list[ValidationIssue] = []
    cache: dict[str, Transcript | None] = {}

    def get(transcript_id: str) -> Transcript | None:
        if transcript_id not in cache:
            try:
                cache[transcript_id] = load_transcript(transcript_id)
            except EngineException:
                cache[transcript_id] = None
        return cache[transcript_id]

    def add(code: ErrorCode, message: str, severity: str = "error") -> None:
        issues.append(ValidationIssue(code=code, message=message, severity=severity))

    for fix in timeline.word_corrections:
        transcript = get(fix.transcript_id)
        if transcript is None or all(w.id != fix.word_id for w in transcript.words):
            add(ErrorCode.TRANSCRIPT_NOT_FOUND, f"correction cites unknown word {fix.word_id!r}")
    fixes = corrected_text(timeline)
    for cue in timeline.captions:
        if not cue.word_ids:
            add(ErrorCode.INVALID_TIMELINE, f"caption {cue.cue_id} has no source words", "warning")
            continue
        transcript = get(cue.transcript_id or "")
        asset = assets.get(cue.source_asset_id)
        if transcript is None or asset is None:
            add(ErrorCode.TRANSCRIPT_NOT_FOUND, f"caption {cue.cue_id} cites an unknown transcript")
            continue
        if transcript.asset_id != asset.id or transcript.source_sha256 != asset.sha256:
            add(ErrorCode.SOURCE_CHANGED, f"caption {cue.cue_id} transcript does not match its source")
            continue
        words = {w.id: w for w in transcript.words}
        if any(word_id not in words for word_id in cue.word_ids):
            add(ErrorCode.TRANSCRIPT_NOT_FOUND, f"caption {cue.cue_id} cites a word absent from transcript")
            continue
        expected = " ".join(
            fixes.get((transcript.id, word_id), _clean(words[word_id].text)) for word_id in cue.word_ids
        )
        if _clean(cue.text) != expected:
            add(ErrorCode.INVALID_TIMELINE, f"caption {cue.cue_id} text differs from its cited words")
        elif cue.corrected != any((transcript.id, word_id) in fixes for word_id in cue.word_ids):
            add(ErrorCode.INVALID_TIMELINE, f"caption {cue.cue_id} corrected flag is inconsistent")
    return issues


def _ms(frames: int, timeline: Timeline) -> int:
    rate = timeline.render_profile.frame_rate
    return round(Fraction(frames * rate.den * 1000, rate.num))


def to_srt(timeline: Timeline) -> str:
    def stamp(ms: int) -> str:
        hours, rest = divmod(ms, 3_600_000)
        minutes, rest = divmod(rest, 60_000)
        seconds, millis = divmod(rest, 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

    blocks = []
    for n, cue in enumerate(sorted(timeline.captions, key=lambda c: c.timeline_in_frames), start=1):
        start, end = _ms(cue.timeline_in_frames, timeline), _ms(cue.timeline_out_frames, timeline)
        blocks.append(f"{n}\n{stamp(start)} --> {stamp(end)}\n{cue.text}\n")
    return "\n".join(blocks)


#: ASS colours are &HAABBGGRR. Styles are fixed per preset; no caller-supplied style text.
# ``align`` is the ASS numpad position (2 bottom, 5 middle, 8 top); ``scale`` multiplies the font size and is
# kept at or below 1, because the base size already fits the longest line to the frame width.
_PRESET_STYLE = {
    CaptionPreset.CLEAN_WHITE: {"primary": "&H00FFFFFF", "back": "&H64000000", "bold": 0, "border": 1},
    CaptionPreset.BOLD_YELLOW: {"primary": "&H0000FFFF", "back": "&H64000000", "bold": -1, "border": 1},
    CaptionPreset.BOXED: {"primary": "&H00FFFFFF", "back": "&H8C000000", "bold": 0, "border": 3},
    CaptionPreset.TOP_CLEAN: {
        "primary": "&H00FFFFFF", "back": "&H64000000", "bold": 0, "border": 1, "align": 8,
    },
    CaptionPreset.BIG_CENTER: {
        "primary": "&H00FFFFFF", "back": "&H96000000", "bold": -1, "border": 1, "align": 5, "outline": 0.12,
    },
    CaptionPreset.LOWER_MINIMAL: {
        "primary": "&H00FFFFFF", "back": "&H00000000", "bold": 0, "border": 1, "scale": 0.8, "outline": 0.05,
    },
}  # fmt: skip


def safe_margins(timeline: Timeline) -> tuple[int, int]:
    """(side, bottom) margins in pixels keeping captions clear of phone UI chrome."""
    profile = timeline.render_profile
    if profile.height > profile.width:
        return round(profile.width * 0.08), round(profile.height * 0.22)
    return round(profile.width * 0.06), round(profile.height * 0.08)


def caption_font_size(timeline: Timeline) -> int:
    profile = timeline.render_profile
    side, _ = safe_margins(timeline)
    by_width = (profile.width - 2 * side) / (chars_per_line(timeline) * 0.56)
    return max(8, floor(min(by_width, profile.height * 0.05)))


def _ass_escape(text: str) -> str:
    # Braces start override blocks and backslashes start tags in ASS: neutralise both.
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def to_ass(timeline: Timeline) -> str:
    profile = timeline.render_profile
    style = _PRESET_STYLE[timeline.caption_preset]
    side, bottom = safe_margins(timeline)
    size = max(8, round(caption_font_size(timeline) * style.get("scale", 1.0)))
    outline = max(1, round(size * style.get("outline", 0.08)))
    align = style.get("align", 2)
    # Top captions keep the same clearance from the top edge (status bar) as bottom ones from the bottom.
    vertical = {2: bottom, 8: round(profile.height * 0.12), 5: 0}[align]

    def stamp(frames: int) -> str:
        cs = round(Fraction(_ms(frames, timeline), 10))
        hours, rest = divmod(cs, 360_000)
        minutes, rest = divmod(rest, 6000)
        seconds, centis = divmod(rest, 100)
        return f"{hours}:{minutes:02d}:{seconds:02d}.{centis:02d}"

    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {profile.width}",
        f"PlayResY: {profile.height}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Caption,Poppins,{size},{style['primary']},&H00FFFFFF,&H00000000,{style['back']},"
        f"{style['bold']},0,0,0,100,100,0,0,{style['border']},{outline},1,{align},{side},{side},{vertical},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    events = [
        f"Dialogue: 0,{stamp(cue.timeline_in_frames)},{stamp(cue.timeline_out_frames)},Caption,,0,0,0,,"
        f"{_ass_escape(cue.text)}"
        for cue in sorted(timeline.captions, key=lambda c: c.timeline_in_frames)
    ]
    return "\n".join(header + events) + "\n"
