"""Phase 3 QA: measured audio/picture checks, caption bounds/provenance and editorial review flags.

Technical checks can FAIL a render. Editorial flags are only ever PASS or WARN: they point a human
reviewer at likely problems (awkward joins, uncertain caption words, crop risk) and never claim polish.
"""

from __future__ import annotations

from contextlib import suppress
from fractions import Fraction
from itertools import pairwise

from ageneditor.captions import caption_evidence_issues, chars_per_line, map_words
from ageneditor.contracts import (
    CheckKind,
    CheckStatus,
    EngineException,
    FitMode,
    QACheck,
    Timeline,
    TrackId,
)
from ageneditor.media.analysis import OutputAnalysis
from ageneditor.storage import ProjectStore

UNCERTAIN_WORD_PROBABILITY = 0.6
MAX_BROLL_SHARE = Fraction(1, 2)
MAX_BROLL_SECONDS = 6
MAX_UPSCALE = 1.5
#: Speaker audio quieter than this under a voice line is treated as deliberately turned down.
VOICE_SPEAKER_MAX_GAIN_DB = -20.0


def _list(items: list[str], limit: int = 6) -> str:
    shown = ", ".join(items[:limit])
    return (shown + (f" (+{len(items) - limit} more)" if len(items) > limit else ""))[:200]


def _seconds(us: int) -> str:
    return f"{us / 1_000_000:.2f}s"


def technical_checks(store: ProjectStore, timeline: Timeline, analysis: OutputAnalysis) -> list[QACheck]:
    profile = timeline.render_profile
    checks: list[QACheck] = []
    target, ceiling = profile.loudness_lufs, profile.true_peak_dbtp
    lufs = analysis.integrated_lufs
    if lufs is None:
        status, measured = CheckStatus.WARN, "unmeasurable (output is silent)"
    else:
        off = abs(lufs - target)
        status = CheckStatus.PASS if off <= 1 else CheckStatus.WARN if off <= 2 else CheckStatus.FAIL
        if status is CheckStatus.FAIL and not timeline.audio.normalize:
            status = CheckStatus.WARN
        measured = f"{lufs:.1f} LUFS"
    checks.append(
        QACheck(
            check_id="loudness",
            status=status,
            expected=f"{target:.1f} LUFS ± 1",
            measured=measured,
            message="EBU R128 integrated loudness measured on the rendered output",
        )
    )
    peak = analysis.true_peak_dbtp
    if peak is None:
        status, measured = CheckStatus.WARN, "unmeasurable (output is silent)"
    else:
        status = (
            CheckStatus.PASS
            if peak <= ceiling
            else CheckStatus.WARN
            if peak <= ceiling + 1
            else CheckStatus.FAIL
        )
        measured = f"{peak:.1f} dBTP"
    checks.append(
        QACheck(check_id="true_peak", status=status, expected=f"≤ {ceiling:.1f} dBTP", measured=measured)
    )
    checks.append(
        QACheck(
            check_id="black_gaps",
            status=CheckStatus.WARN if analysis.black else CheckStatus.PASS,
            expected="no black interval ≥ 0.5s",
            measured=_list([f"{_seconds(a)}-{_seconds(b)}" for a, b in analysis.black]) or "none",
        )
    )
    checks.append(
        QACheck(
            check_id="silent_gaps",
            status=CheckStatus.WARN if analysis.silence else CheckStatus.PASS,
            expected="no silence ≥ 1.5s below -50 dB",
            measured=_list([f"{_seconds(s.start_us)}-{_seconds(s.end_us)}" for s in analysis.silence])
            or "none",
        )
    )
    if timeline.captions:
        checks.extend(_caption_checks(store, timeline))
    return checks


def _caption_checks(store: ProjectStore, timeline: Timeline) -> list[QACheck]:
    limit = chars_per_line(timeline)
    out_of_bounds = [
        cue.cue_id
        for cue in timeline.captions
        if cue.timeline_out_frames > timeline.duration_frames or cue.text.count("\n") > 1
    ]
    long_lines = [
        cue.cue_id for cue in timeline.captions if any(len(line) > limit for line in cue.text.split("\n"))
    ]
    checks = [
        QACheck(
            check_id="caption_bounds",
            status=CheckStatus.FAIL
            if out_of_bounds
            else CheckStatus.WARN
            if long_lines
            else CheckStatus.PASS,
            expected=f"cues inside the video, ≤ 2 lines of ≤ {limit} chars, phone-safe margins",
            measured=_list(out_of_bounds + long_lines) or f"{len(timeline.captions)} cues in bounds",
            message="long single words can exceed the line width" if long_lines and not out_of_bounds else "",
        )
    ]
    problems = [i.message for i in caption_evidence_issues(timeline, store.assets(), store.transcript)]
    transcripts = {}
    for cue in timeline.captions:
        if cue.transcript_id and cue.source_asset_id not in transcripts:
            with suppress(EngineException):  # already reported by caption_evidence_issues
                transcripts[cue.source_asset_id] = store.transcript(cue.transcript_id)
    placed = {(w.transcript_id, w.word_id): w for w in map_words(store, timeline, transcripts)}
    for cue in timeline.captions:
        for word_id in cue.word_ids:
            word = placed.get((cue.transcript_id, word_id))
            if word is None:
                problems.append(f"{cue.cue_id}: word {word_id} is not inside any primary clip")
                break
            if not (cue.timeline_in_frames <= word.in_frame and word.out_frame <= cue.timeline_out_frames):
                problems.append(f"{cue.cue_id}: word {word_id} is spoken outside the cue's time")
                break
    checks.append(
        QACheck(
            check_id="caption_provenance",
            status=CheckStatus.FAIL if problems else CheckStatus.PASS,
            expected="every caption word maps to transcript speech inside the cut",
            measured=_list(problems, 3)
            or f"{sum(len(c.word_ids) for c in timeline.captions)} words verified",
        )
    )
    return checks


def editorial_flags(store: ProjectStore, timeline: Timeline) -> list[QACheck]:
    assets = store.assets()
    fps = timeline.render_profile.frame_rate.fraction
    profile = timeline.render_profile
    flags: list[QACheck] = []

    def flag(check_id: str, problems: list[str], expected: str, ok: str, message: str = "") -> None:
        flags.append(
            QACheck(
                check_id=check_id,
                kind=CheckKind.EDITORIAL,
                status=CheckStatus.WARN if problems else CheckStatus.PASS,
                expected=expected,
                measured=_list(problems) or ok,
                message=message if problems else "",
            )
        )

    v1 = sorted((c for c in timeline.clips if c.track is TrackId.V1), key=lambda c: c.timeline_in_frames)
    v2 = [c for c in timeline.clips if c.track is TrackId.V2]

    def covered(frame: int) -> bool:
        return any(c.timeline_in_frames <= frame < c.timeline_out_frames for c in v2)

    jumps = [
        f"{float(b.timeline_in_frames / fps):.2f}s"
        for a, b in pairwise(v1)
        if a.asset_id == b.asset_id
        and a.source_out_frames != b.source_in_frames
        and not covered(b.timeline_in_frames)
    ]
    flag(
        "jump_cuts",
        jumps,
        "same-angle cuts covered by B-roll or a punch-in change",
        "none uncovered",
        "visible jump cut on the same camera angle; consider B-roll or a punch-in change",
    )

    framing = []
    for clip in [*v1, *v2]:
        video = assets[clip.asset_id].video
        if video is None:
            continue
        base = max(profile.width / video.width, profile.height / video.height)
        if clip.fit_mode is FitMode.CONTAIN:
            base = min(profile.width / video.width, profile.height / video.height)
        upscale = base * clip.transform.scale
        if upscale > MAX_UPSCALE:
            framing.append(f"{clip.clip_id} upscaled {upscale:.1f}x")
        if clip.fit_mode is FitMode.COVER and clip.track is TrackId.V1:
            kept = (profile.width / profile.height) / (video.width / video.height) / clip.transform.scale
            if kept < 0.5 and clip.transform.center_x == 0.5:
                framing.append(f"{clip.clip_id} keeps {kept:.0%} of width at default centre")
    flag(
        "framing",
        framing,
        f"≤ {MAX_UPSCALE}x upscale; speaker inside tight crops",
        "no crop or softness risk detected",
        "check that the speaker stays in frame and the image is not visibly soft",
    )

    duration = timeline.duration_frames or 1
    broll_frames = sum(c.timeline_span_frames for c in v2)
    broll = []
    if Fraction(broll_frames, duration) > MAX_BROLL_SHARE:
        broll.append(f"B-roll covers {broll_frames / duration:.0%}")
    broll += [
        f"{c.clip_id} hides speaker {c.timeline_span_frames / fps:.1f}s"
        for c in v2
        if c.timeline_span_frames > MAX_BROLL_SECONDS * fps
    ]
    repeated = {c.asset_id for c in v2 if sum(o.asset_id == c.asset_id for o in v2) > 1}
    broll += [f"{asset_id} repeated" for asset_id in sorted(repeated)]
    flag(
        "broll_balance",
        broll,
        f"B-roll ≤ {MAX_BROLL_SHARE:.0%} of runtime, ≤ {MAX_BROLL_SECONDS}s each, no repeats",
        f"{len(v2)} overlays, {broll_frames / duration:.0%} coverage",
        "speaker should stay visible when B-roll is not justified",
    )

    voice = sorted((c for c in timeline.clips if c.track is TrackId.A4), key=lambda c: c.timeline_in_frames)
    if voice:
        # Gain is per clip, so this only knows whether the speaker's clip was turned down, not
        # whether anyone is actually talking at that moment.
        clashes = []
        for line in voice:
            for clip in v1:
                if assets[clip.asset_id].audio is None or clip.gain_db <= VOICE_SPEAKER_MAX_GAIN_DB:
                    continue
                overlap = min(line.timeline_out_frames, clip.timeline_out_frames) - max(
                    line.timeline_in_frames, clip.timeline_in_frames
                )
                if overlap > 0:
                    clashes.append(f"{line.clip_id} over {clip.clip_id} for {overlap / fps:.1f}s")
        flag(
            "voice_over_speaker",
            clashes,
            f"speaker audio at ≤ {VOICE_SPEAKER_MAX_GAIN_DB:g} dB wherever a voice line plays",
            f"{len(voice)} voice line{'s' if len(voice) != 1 else ''} clear of the speaker's audio",
            "a voice line plays over the speaker's own audio; listen, then lower that V1 clip's gain "
            "or move the line",
        )

    if timeline.captions:
        uncertain: list[str] = []
        estimated: list[str] = []
        fixed = {(f.transcript_id, f.word_id) for f in timeline.word_corrections}
        for transcript_id in {c.transcript_id for c in timeline.captions if c.transcript_id}:
            words = {w.id: w for w in store.transcript(transcript_id).words}
            for cue in timeline.captions:
                if cue.transcript_id != transcript_id:
                    continue
                uncertain += [
                    f"{words[w].text!r}@{cue.cue_id}"
                    for w in cue.word_ids
                    if w in words
                    and words[w].probability is not None
                    and words[w].probability < UNCERTAIN_WORD_PROBABILITY
                    and (transcript_id, w) not in fixed
                ]
                if any(w in words and words[w].timing == "estimated" for w in cue.word_ids):
                    estimated.append(f"{cue.cue_id} at {cue.timeline_in_frames / fps:.2f}s")
        flag(
            "estimated_caption_timing",
            estimated,
            "captions timed from aligned words",
            "all caption words aligned",
            "these captions use word times estimated by syllables (the recognizer gave none); "
            "watch them against the speech",
        )
        flag(
            "uncertain_caption_words",
            uncertain,
            f"caption words with ASR confidence ≥ {UNCERTAIN_WORD_PROBABILITY} or manually corrected",
            "none",
            "check names and terms; fix with a correct_word revision",
        )
        first = min(c.timeline_in_frames for c in timeline.captions)
        flag(
            "hook_timing",
            [f"first words at {first / fps:.2f}s"] if first > fps else [],
            "speech starts within the first second",
            f"speech starts at {first / fps:.2f}s",
            "the opening has no speech; the hook may land late",
        )
    return flags
