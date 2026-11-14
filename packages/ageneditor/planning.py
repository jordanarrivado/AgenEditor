"""Validate externally authored cuts against immutable source and transcript evidence."""

from __future__ import annotations

from fractions import Fraction
from math import ceil

from ageneditor.contracts import (
    AssetRole,
    Author,
    Clip,
    EditPlan,
    EngineException,
    ErrorCode,
    HookCandidate,
    PlanDisposition,
    PlanResult,
    Provenance,
    TrackId,
    Transcript,
    ValidationIssue,
)
from ageneditor.media.hashing import sha256_file
from ageneditor.media.probe import video_frame_times
from ageneditor.storage import ProjectStore
from ageneditor.timebase import seconds_to_frame_floor, seconds_to_frames_round

MAX_PLAN_ATTEMPTS = 3


def candidate_hooks(transcript: Transcript, limit: int = 5) -> list[HookCandidate]:
    """Rank source segments with disclosed timing/text hints, leaving editorial choice to the agent."""
    words = {word.id: word for word in transcript.words}
    candidates: list[HookCandidate] = []
    for segment in transcript.segments:
        cited = [words[word_id] for word_id in segment.word_ids]
        if not cited:
            continue
        duration_us = segment.end_us - segment.start_us
        signals: list[str] = []
        score = 0.25
        if 3_000_000 <= duration_us <= 12_000_000:
            score += 0.2
            signals.append("short passage")
        if "?" in segment.text:
            score += 0.2
            signals.append("question phrasing")
        if segment.start_us <= transcript.duration_us // 5:
            score += 0.1
            signals.append("early in source")
        probabilities = [word.probability for word in cited if word.probability is not None]
        if probabilities and sum(probabilities) / len(probabilities) >= 0.8:
            score += 0.15
            signals.append("high transcription confidence")
        if cited[-1].text.endswith((".", "?", "!")):
            score += 0.1
            signals.append("sentence ending")
        candidates.append(
            HookCandidate(segment_id=segment.id, score=min(1, round(score, 3)), signals=signals)
        )
    return sorted(candidates, key=lambda item: (-item.score, item.segment_id))[:limit]


def _source_frame_bounds(store: ProjectStore, asset, start_us: int, end_us: int) -> tuple[int, int]:
    if asset.video.is_vfr:
        from bisect import bisect_right

        times = video_frame_times(store.safe_path(asset.relative_path))
        start = bisect_right(times, Fraction(start_us, 1_000_000)) - 1
        end = bisect_right(times, Fraction(end_us, 1_000_000) - Fraction(1, 1_000_000))
        return max(0, start), min(asset.video.duration_frames, max(start + 1, end))
    rate = asset.video.frame_rate.fraction
    return (
        seconds_to_frame_floor(Fraction(start_us, 1_000_000), rate),
        ceil(Fraction(end_us, 1_000_000) * rate),
    )


def submit_edit_plan(store: ProjectStore, plan: EditPlan, expected_revision: int) -> PlanResult:
    if plan.project_id != store.project_id:
        raise EngineException(ErrorCode.PERMISSION_DENIED, "plan belongs to another project")
    if store.current_revision() != expected_revision:
        raise EngineException(
            ErrorCode.REVISION_CONFLICT, "project revision changed", current_revision=store.current_revision()
        )
    attempt_id, attempt = store.reserve_plan_attempt(expected_revision, MAX_PLAN_ATTEMPTS)
    timeline = store.timeline(expected_revision)
    issues: list[ValidationIssue] = []

    def add(code: ErrorCode, message: str, index: int | None = None, severity: str = "error") -> None:
        issues.append(
            ValidationIssue(
                code=code,
                message=message,
                clip_id=f"planned_{index + 1}" if index is not None else None,
                severity=severity,
            )
        )

    if plan.target_duration_frames != timeline.target_duration_frames:
        add(ErrorCode.INVALID_ARGUMENT, "plan target differs from the timeline target")
    assets = store.assets()
    clips: list[Clip] = []
    total = 0
    selected: list[tuple[str, int, int]] = []
    transcripts_seen: dict[str, Transcript] = {}
    for index, segment in enumerate(plan.segments):
        asset = assets.get(segment.asset_id)
        if asset is None or asset.role is not AssetRole.PRIMARY or asset.video is None:
            add(ErrorCode.ASSET_NOT_FOUND, "plan cites a missing or non-primary video asset", index)
            continue
        if segment.source_out_frames > asset.video.duration_frames:
            add(ErrorCode.RANGE_OUT_OF_BOUNDS, "plan range exceeds the source", index)
            continue
        if sha256_file(store.safe_path(asset.relative_path)) != asset.sha256:
            add(ErrorCode.SOURCE_CHANGED, "registered source hash changed", index)
            continue
        try:
            transcript = store.transcript(segment.transcript_id)
        except EngineException:
            add(ErrorCode.TRANSCRIPT_NOT_FOUND, "plan cites an unknown transcript", index)
            continue
        if transcript.asset_id != asset.id or transcript.source_sha256 != asset.sha256:
            add(ErrorCode.SOURCE_CHANGED, "transcript does not belong to this source version", index)
            continue
        transcripts_seen[transcript.id] = transcript
        word_ids = [word.id for word in transcript.words]
        try:
            first = word_ids.index(segment.first_word_id)
            last = word_ids.index(segment.last_word_id)
        except ValueError:
            add(ErrorCode.INVALID_ARGUMENT, "plan cites a word absent from the transcript", index)
            continue
        if first > last:
            add(ErrorCode.INVALID_ARGUMENT, "cited word range runs backward", index)
            continue
        cited = transcript.words[first : last + 1]
        start_frame, end_frame = _source_frame_bounds(
            store, asset, cited[0].start_us, max(word.end_us for word in cited)
        )
        if segment.source_in_frames > start_frame or segment.source_out_frames < end_frame:
            add(ErrorCode.RANGE_OUT_OF_BOUNDS, "cut does not contain all cited speech", index)
            continue
        if asset.video.is_vfr:
            frame_times = video_frame_times(store.safe_path(asset.relative_path))
            cut_start_us = round(frame_times[segment.source_in_frames] * 1_000_000)
            cut_end_us = round(
                (
                    frame_times[segment.source_out_frames]
                    if segment.source_out_frames < len(frame_times)
                    else Fraction(transcript.duration_us, 1_000_000)
                )
                * 1_000_000
            )
        else:
            rate = asset.video.frame_rate.fraction
            cut_start_us = round(Fraction(segment.source_in_frames, 1) / rate * 1_000_000)
            cut_end_us = round(Fraction(segment.source_out_frames, 1) / rate * 1_000_000)
        if cited[0].start_us - cut_start_us > 1_500_000 or cut_end_us - cited[-1].end_us > 1_500_000:
            add(
                ErrorCode.INVALID_ARGUMENT,
                "cut contains more than 1.5 seconds of uncited lead or tail",
                index,
            )
            continue
        if first > 0:
            previous_word = transcript.words[first - 1]
            if cited[0].start_us - previous_word.end_us < 800_000 and not previous_word.text.endswith(
                (".", "?", "!")
            ):
                add(
                    ErrorCode.OUT_OF_CONTEXT,
                    "cut may begin mid-thought; review preceding words",
                    index,
                    "warning",
                )
        if last + 1 < len(transcript.words):
            next_word = transcript.words[last + 1]
            if next_word.start_us - cited[-1].end_us < 800_000 and not cited[-1].text.endswith(
                (".", "?", "!")
            ):
                add(
                    ErrorCode.OUT_OF_CONTEXT,
                    "cut may end mid-thought; review following words",
                    index,
                    "warning",
                )
        if any(
            a == asset.id and max(s, segment.source_in_frames) < min(e, segment.source_out_frames)
            for a, s, e in selected
        ):
            add(ErrorCode.OUT_OF_CONTEXT, "source range repeats speech already selected", index, "warning")
        selected.append((asset.id, segment.source_in_frames, segment.source_out_frames))
        if asset.video.is_vfr:
            times = video_frame_times(store.safe_path(asset.relative_path))
            if segment.source_out_frames >= len(times):
                end_time = Fraction(transcript.duration_us, 1_000_000)
            else:
                end_time = times[segment.source_out_frames]
            duration = end_time - times[segment.source_in_frames]
        else:
            duration = (
                Fraction(segment.source_out_frames - segment.source_in_frames, 1)
                / asset.video.frame_rate.fraction
            )
        output_frames = seconds_to_frames_round(duration, timeline.render_profile.frame_rate)
        if output_frames <= 0:
            add(ErrorCode.RANGE_OUT_OF_BOUNDS, "source range is shorter than one output frame", index)
            continue
        clips.append(
            Clip(
                clip_id=f"planned_{index + 1}",
                asset_id=asset.id,
                track=TrackId.V1,
                source_in_frames=segment.source_in_frames,
                source_out_frames=segment.source_out_frames,
                timeline_in_frames=total,
                timeline_duration_frames=output_frames,
                rationale=segment.rationale,
            )
        )
        total += output_frames
    tolerance = seconds_to_frames_round(2, timeline.render_profile.frame_rate)
    available_us = sum(
        sum(item.end_us - item.start_us for item in transcript.segments)
        for transcript in transcripts_seen.values()
    )
    needed_us = round(
        Fraction(plan.target_duration_frames - tolerance, 1)
        / timeline.render_profile.frame_rate.fraction
        * 1_000_000
    )
    # Cited cuts may legitimately include short lead/tail handles around speech.
    supported_us = available_us + len(plan.segments) * 3_000_000
    if transcripts_seen and supported_us < needed_us:
        add(
            ErrorCode.SOURCE_INSUFFICIENT,
            f"cited transcript passages and permitted handles provide at most {supported_us} "
            f"microseconds; target needs {needed_us}",
        )
    if abs(total - plan.target_duration_frames) > tolerance:
        add(
            ErrorCode.INVALID_TIMELINE,
            f"selected duration {total} frames is outside target {plan.target_duration_frames} ± {tolerance}",
        )
    if not clips:
        add(ErrorCode.SOURCE_INSUFFICIENT, "no source-backed speech ranges are available")
    candidate = (
        timeline.model_copy(
            update={
                "clips": clips,
                # A plan builds new V1 clips; earlier transitions referenced the old ones.
                "transitions": [],
                "provenance": Provenance(
                    author=Author.AGENT, tool="submit_edit_plan", summary=plan.brief[:2000]
                ),
            }
        )
        if clips
        else None
    )
    if candidate:
        issues.extend(store.validation_issues(candidate))
    valid = not any(issue.severity == "error" for issue in issues)
    disposition = (
        PlanDisposition.VALID
        if valid
        else (
            PlanDisposition.SOURCE_INSUFFICIENT
            if any(issue.code is ErrorCode.SOURCE_INSUFFICIENT for issue in issues)
            else PlanDisposition.MANUAL_REVIEW
            if attempt >= MAX_PLAN_ATTEMPTS
            else PlanDisposition.RETRY
        )
    )
    result = PlanResult(
        valid=valid,
        disposition=disposition,
        attempt=attempt,
        issues=issues,
        candidate_timeline=candidate if valid else None,
    )
    store.complete_plan_attempt(attempt_id, result.model_dump_json())
    return result
