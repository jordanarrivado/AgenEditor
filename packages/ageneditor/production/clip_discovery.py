"""AI Layer 1 — source-linked clip discovery (spec §5).

Two paths produce the same ``ClipCandidate`` records:

* ``discover_deterministic`` windows the cached transcripts into self-contained passages and ranks them
  with disclosed, advisory signals (no model involved).
* ``validate_proposals`` checks candidates a discovery agent proposed. Agents cite word IDs only; the
  engine derives times and the exact quote from the transcript, so no dialogue can be invented.

Neither path pads results to meet a requested count: a shortfall is reported instead.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from ageneditor.contracts import (
    Batch,
    CandidateOverlap,
    CandidateProposal,
    ClipCandidate,
    ContextSnapshot,
    DiscoveryReport,
    EngineException,
    ErrorCode,
    EvidenceRange,
    QualityScores,
    Transcript,
    TranscriptWord,
)
from ageneditor.contracts.production import BatchTargetMode
from ageneditor.planning import candidate_hooks
from ageneditor.production.common import new_id
from ageneditor.storage import ProjectStore

AUTO_LIMIT = 12
MAX_LISTED = 50
MAX_INTERNAL_PAUSE_US = 2_000_000
METHOD_NOTE = (
    "Deterministic discovery: transcript passages ranked by hook signals, sentence boundaries, word "
    "confidence and pauses. Scores are advisory and not validated against human review."
)
_ENDINGS = (".", "?", "!")


@dataclass(frozen=True)
class _Window:
    asset_id: str
    transcript: Transcript
    words: list[TranscriptWord]

    @property
    def start_us(self) -> int:
        return self.words[0].start_us

    @property
    def end_us(self) -> int:
        return max(word.end_us for word in self.words)

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)


def source_transcripts(store: ProjectStore, snapshot: ContextSnapshot) -> dict[str, Transcript]:
    """The newest transcript of each in-scope source version. Missing analysis is an error."""
    found: dict[str, Transcript] = {}
    for asset_id, sha256 in snapshot.source_asset_hashes.items():
        transcript = store.latest_transcript(asset_id, sha256)
        if transcript is None:
            raise EngineException(
                ErrorCode.TRANSCRIPT_NOT_FOUND, f"source {asset_id} has no transcript for its current version"
            )
        found[asset_id] = transcript
    return found


def _excluded(text: str, exclusions: Sequence[str]) -> str | None:
    lowered = text.lower()
    return next((phrase for phrase in exclusions if phrase.lower() in lowered), None)


def _assess(window: _Window, hook_scores: dict[str, float]) -> tuple[QualityScores, bool, list[str]]:
    transcript, words = window.transcript, window.words
    index = {word.id: i for i, word in enumerate(transcript.words)}
    first = index[words[0].id]
    starts_clean = first == 0 or transcript.words[first - 1].text.endswith(_ENDINGS)
    ends_clean = words[-1].text.endswith(_ENDINGS)
    pauses = [b.start_us - a.end_us for a, b in pairwise(words)]
    longest_pause = max(pauses, default=0)
    caveats: list[str] = []
    if not starts_clean:
        caveats.append("May begin mid-thought; review the preceding words.")
    if not ends_clean:
        caveats.append("Ends without sentence punctuation.")
    if longest_pause > MAX_INTERNAL_PAUSE_US:
        caveats.append(f"Contains a {longest_pause / 1_000_000:.1f} s pause.")
    probabilities = [word.probability for word in words if word.probability is not None]
    clarity = sum(probabilities) / len(probabilities) if probabilities else 0.5
    if probabilities and clarity < 0.7:
        caveats.append("Low transcription confidence; check the words.")
    speech = sum(word.end_us - word.start_us for word in words)
    coverage = min(1.0, speech / max(1, window.end_us - window.start_us))
    source_condition = round(
        min(1.0, coverage / 0.7) * (0.6 if longest_pause > MAX_INTERNAL_PAUSE_US else 1.0), 3
    )
    coherence = round(0.2 + 0.4 * starts_clean + 0.4 * ends_clean, 3)
    hook = round(hook_scores.get(words[0].segment_id, 0.25), 3)
    return (
        QualityScores(
            hook=hook, coherence=coherence, clarity=round(clarity, 3), source_condition=source_condition
        ),
        starts_clean and ends_clean,
        caveats,
    )


def _overall(scores: QualityScores) -> float:
    base = 0.35 * scores.hook + 0.3 * scores.coherence + 0.2 * scores.clarity + 0.15 * scores.source_condition
    extras = [value for value in (scores.relevance, scores.visual_potential) if value is not None]
    if extras:
        base = 0.8 * base + 0.2 * (sum(extras) / len(extras))
    return round(min(1.0, base), 4)


def _first_sentence(words: Sequence[TranscriptWord]) -> str:
    sentence: list[str] = []
    for word in words:
        sentence.append(word.text)
        if word.text.endswith(_ENDINGS) or len(sentence) >= 30:
            break
    return " ".join(sentence)[:500]


def _candidate(
    batch: Batch,
    window: _Window,
    scores: QualityScores,
    complete: bool,
    caveats: list[str],
    method: str,
    topic: str | None = None,
    rationale: str = "",
) -> ClipCandidate:
    text = window.text
    return ClipCandidate(
        id=new_id("cand"),
        batch_id=batch.id,
        rank=1,
        topic=(topic or _first_sentence(window.words))[:200],
        hook_text=_first_sentence(window.words),
        quote=text[:4000],
        evidence=[
            EvidenceRange(
                asset_id=window.asset_id,
                transcript_id=window.transcript.id,
                first_word_id=window.words[0].id,
                last_word_id=window.words[-1].id,
                start_us=window.start_us,
                end_us=window.end_us,
            )
        ],
        duration_us=window.end_us - window.start_us,
        scores=scores,
        overall_score=_overall(scores),
        narrative_complete=complete,
        method=method,  # type: ignore[arg-type]
        rationale=rationale[:1000],
        caveats=caveats,
    )


def _overlap(a: ClipCandidate, b: ClipCandidate) -> int:
    total = 0
    for x in a.evidence:
        for y in b.evidence:
            if x.asset_id == y.asset_id:
                total += max(0, min(x.end_us, y.end_us) - max(x.start_us, y.start_us))
    return total


def _finalize(
    batch: Batch, candidates: list[ClipCandidate], method: str, notes: list[str], drop_overlapping: bool
) -> DiscoveryReport:
    ordered = sorted(
        candidates, key=lambda c: (-c.overall_score, c.evidence[0].asset_id, c.evidence[0].start_us)
    )
    kept: list[ClipCandidate] = []
    for candidate in ordered:
        if drop_overlapping and any(_overlap(candidate, other) > 0 for other in kept):
            continue
        kept.append(candidate)
    viable = len(kept)
    # Auto mode shows the strongest few; count mode shows every viable option (bounded) to choose from.
    kept = kept[:AUTO_LIMIT] if batch.policy.target_mode is BatchTargetMode.AUTO else kept[:MAX_LISTED]
    final: list[ClipCandidate] = []
    for rank, candidate in enumerate(kept, start=1):
        overlaps = [
            CandidateOverlap(candidate_id=other.id, overlap_us=amount)
            for other in kept
            if other.id != candidate.id and (amount := _overlap(candidate, other)) > 0
        ]
        final.append(candidate.model_copy(update={"rank": rank, "overlaps": overlaps}))
    requested = batch.policy.target_count
    recommended = min(viable, requested) if requested else min(viable, AUTO_LIMIT)
    shortfall = max(0, (requested or 0) - viable)
    if shortfall:
        notes.append(
            f"The sources support {viable} self-contained clip(s); {shortfall} fewer than requested. "
            "No filler or invented material was added."
        )
    return DiscoveryReport(
        batch_id=batch.id,
        method=method,  # type: ignore[arg-type]
        requested=requested,
        viable_count=viable,
        recommended_count=recommended,
        shortfall=shortfall,
        candidates=final,
        notes=notes[:20],
    )


def discover_deterministic(store: ProjectStore, batch: Batch, snapshot: ContextSnapshot) -> DiscoveryReport:
    policy = batch.policy
    windows: list[ClipCandidate] = []
    notes = [METHOD_NOTE]
    for asset_id, transcript in source_transcripts(store, snapshot).items():
        hooks = {hook.segment_id: hook.score for hook in candidate_hooks(transcript, limit=10_000)}
        words = {word.id: word for word in transcript.words}
        segments = sorted(transcript.segments, key=lambda segment: segment.start_us)
        for i in range(len(segments)):
            best: ClipCandidate | None = None
            for j in range(i, len(segments)):
                duration = segments[j].end_us - segments[i].start_us
                if duration > policy.max_duration_us:
                    break
                if duration < policy.min_duration_us:
                    continue
                cited = [words[word_id] for segment in segments[i : j + 1] for word_id in segment.word_ids]
                window = _Window(asset_id, transcript, cited)
                if _excluded(window.text, policy.exclusions):
                    continue
                scores, complete, caveats = _assess(window, hooks)
                candidate = _candidate(batch, window, scores, complete, caveats, "deterministic")
                if best is None or candidate.overall_score > best.overall_score:
                    best = candidate
            if best is not None:
                windows.append(best)
    if not windows:
        notes.append("No transcript passage fits the requested duration range and exclusions.")
    return _finalize(batch, windows, "deterministic", notes, drop_overlapping=True)


def validate_proposals(
    store: ProjectStore, batch: Batch, snapshot: ContextSnapshot, proposals: Sequence[CandidateProposal]
) -> tuple[DiscoveryReport, list[str]]:
    """Validate agent proposals. Returns the report and one message per rejected proposal."""
    policy = batch.policy
    transcripts = source_transcripts(store, snapshot)
    by_id = {transcript.id: (asset_id, transcript) for asset_id, transcript in transcripts.items()}
    accepted: list[ClipCandidate] = []
    rejected: list[str] = []
    for number, proposal in enumerate(proposals, start=1):
        try:
            windows: list[_Window] = []
            for citation in proposal.evidence:
                if citation.transcript_id not in by_id:
                    raise ValueError("cites a transcript outside this batch's sources")
                asset_id, transcript = by_id[citation.transcript_id]
                ids = [word.id for word in transcript.words]
                if citation.first_word_id not in ids or citation.last_word_id not in ids:
                    raise ValueError("cites a word that is not in the transcript")
                first, last = ids.index(citation.first_word_id), ids.index(citation.last_word_id)
                if first > last:
                    raise ValueError("cited word range runs backward")
                windows.append(_Window(asset_id, transcript, transcript.words[first : last + 1]))
            duration = sum(window.end_us - window.start_us for window in windows)
            if not policy.min_duration_us <= duration <= policy.max_duration_us:
                raise ValueError(
                    f"cited speech lasts {duration} us, outside "
                    f"{policy.min_duration_us}-{policy.max_duration_us}"
                )
            text = " ".join(window.text for window in windows)
            if phrase := _excluded(text, policy.exclusions):
                raise ValueError(f"contains the excluded phrase {phrase!r}")
            hooks = {
                hook.segment_id: hook.score for hook in candidate_hooks(windows[0].transcript, limit=10_000)
            }
            assessed = [_assess(window, hooks) for window in windows]
            scores = QualityScores(
                hook=proposal.hook if proposal.hook is not None else assessed[0][0].hook,
                coherence=min(item[0].coherence for item in assessed),
                clarity=round(sum(item[0].clarity for item in assessed) / len(assessed), 3),
                source_condition=min(item[0].source_condition for item in assessed),
                relevance=proposal.relevance,
                visual_potential=proposal.visual_potential,
            )
            candidate = _candidate(
                batch,
                windows[0],
                scores,
                all(item[1] for item in assessed),
                [caveat for item in assessed for caveat in item[2]][:10],
                "agent",
                proposal.topic,
                proposal.rationale,
            )
            candidate = candidate.model_copy(
                update={
                    "evidence": [
                        EvidenceRange(
                            asset_id=window.asset_id,
                            transcript_id=window.transcript.id,
                            first_word_id=window.words[0].id,
                            last_word_id=window.words[-1].id,
                            start_us=window.start_us,
                            end_us=window.end_us,
                        )
                        for window in windows
                    ],
                    "quote": text[:4000],
                    "duration_us": duration,
                }
            )
            accepted.append(candidate)
        except ValueError as exc:
            rejected.append(f"proposal {number}: {exc}")
    notes = ["Agent-proposed candidates; times and quotes were derived from the cited transcript words."]
    if rejected:
        notes.append(f"{len(rejected)} proposal(s) were rejected by source validation.")
    return _finalize(batch, accepted, "agent", notes, drop_overlapping=False), rejected
