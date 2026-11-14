"""Phase 2 exit gate: a valid source-linked ~40 s first-cut plan from a multi-minute recording.

The transcript is synthetic evidence (no ASR model in CI); the media, hashing, frame mapping and
plan validation are real.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ageneditor.contracts import (
    AssetRole,
    EditPlan,
    ErrorCode,
    ModelPreset,
    PlanDisposition,
    PlanSegment,
    RenderProfile,
    Transcript,
    TranscriptSegment,
    TranscriptWord,
)
from ageneditor.media.tools import find_tool
from ageneditor.planning import MAX_PLAN_ATTEMPTS, submit_edit_plan
from ageneditor.storage import ProjectStore
from ageneditor.timebase import FrameRate

SOURCE_S = 300  # 5 minutes: the low end of the roadmap's 5-20 minute range
FPS = 30
SENTENCE_S = 10  # one 9-word sentence every 10 s, words 1 s apart


@pytest.fixture(scope="module")
def long_source(tmp_path_factory: pytest.TempPathFactory, media_dir: Path) -> Path:
    out = tmp_path_factory.mktemp("long") / "talk_5min.mp4"
    args = [
        str(find_tool("ffmpeg")),
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size=96x54:rate={FPS}:duration={SOURCE_S}",
        "-f",
        "lavfi",
        "-i",
        f"sine=f=440:sample_rate=48000:duration={SOURCE_S}",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(out),
    ]
    subprocess.run(args, check=True, timeout=300)
    return out


def _transcript(store: ProjectStore, asset_id: str) -> Transcript:
    asset = store.assets()[asset_id]
    words: list[TranscriptWord] = []
    segments: list[TranscriptSegment] = []
    for s in range(SOURCE_S // SENTENCE_S):
        seg_words = []
        for w in range(9):
            n = len(words)
            start = (s * SENTENCE_S + w) * 1_000_000 + 200_000
            word = TranscriptWord(
                id=f"word_{n:07d}",
                segment_id=f"segment_{s:04d}",
                text=f"w{n}." if w == 8 else f"w{n}",
                start_us=start,
                end_us=start + 700_000,
                probability=0.9,
            )
            words.append(word)
            seg_words.append(word)
        segments.append(
            TranscriptSegment(
                id=f"segment_{s:04d}",
                start_us=seg_words[0].start_us,
                end_us=seg_words[-1].end_us,
                text=" ".join(w.text for w in seg_words),
                word_ids=[w.id for w in seg_words],
            )
        )
    transcript = Transcript(
        id="transcript_long",
        project_id=store.project_id,
        asset_id=asset_id,
        source_sha256=asset.sha256,
        model_preset=ModelPreset.BASE,
        model_version="synthetic",
        language="en",
        duration_us=SOURCE_S * 1_000_000,
        words=words,
        segments=segments,
    )
    store.save_transcript(transcript)
    return transcript


def _segment(asset_id: str, sentence: int, rationale: str) -> PlanSegment:
    # Sentence spans [s*10+0.2, s*10+8.9] s; cut the whole 10 s slot on frame boundaries.
    first = sentence * 9
    return PlanSegment(
        asset_id=asset_id,
        transcript_id="transcript_long",
        first_word_id=f"word_{first:07d}",
        last_word_id=f"word_{first + 8:07d}",
        source_in_frames=sentence * SENTENCE_S * FPS,
        source_out_frames=(sentence + 1) * SENTENCE_S * FPS,
        rationale=rationale,
        score=0.8,
    )


@pytest.fixture
def project(tmp_path: Path, long_source: Path) -> tuple[ProjectStore, str]:
    store = ProjectStore.create(tmp_path, "exit gate")
    asset = store.import_asset(long_source, AssetRole.PRIMARY)
    store.create_timeline(
        RenderProfile(id="reel_test", width=180, height=320, frame_rate=FrameRate(num=FPS)),
        target_duration_frames=40 * FPS,
    )
    _transcript(store, asset.id)
    return store, asset.id


def _plan(store: ProjectStore, segments: list[PlanSegment]) -> EditPlan:
    return EditPlan(
        project_id=store.project_id,
        target_duration_frames=40 * FPS,
        brief="Forty-second first cut: hook, two supporting points, and the closing line.",
        segments=segments,
    )


@pytest.mark.media
def test_forty_second_plan_from_five_minute_source(project: tuple[ProjectStore, str]) -> None:
    store, asset_id = project
    plan = _plan(
        store,
        [
            _segment(asset_id, 2, "Hook: the speaker states the core claim plainly."),
            _segment(asset_id, 11, "First supporting point with a concrete example."),
            _segment(asset_id, 17, "Second supporting point that builds on the first."),
            _segment(asset_id, 28, "Closing line that resolves the opening claim."),
        ],
    )
    before = store.current_revision()
    result = submit_edit_plan(store, plan, expected_revision=before)
    assert result.disposition is PlanDisposition.VALID, result.issues
    timeline = result.candidate_timeline
    assert timeline is not None
    assert sum(c.timeline_duration_frames for c in timeline.clips) == 40 * FPS
    assert all(c.asset_id == asset_id for c in timeline.clips)
    # Proposal only: nothing is committed until explicitly requested.
    assert store.current_revision() == before


@pytest.mark.media
def test_hallucinated_word_is_rejected_then_retry_cap_applies(project: tuple[ProjectStore, str]) -> None:
    store, asset_id = project
    bad = _segment(asset_id, 3, "Cites a word the transcript never contained.")
    bad = bad.model_copy(update={"last_word_id": "word_9999999"})
    segments = [_segment(asset_id, s, "Valid supporting passage from the source.") for s in (5, 9, 14)]
    plan = _plan(store, [bad, *segments])
    dispositions = []
    for _ in range(MAX_PLAN_ATTEMPTS):
        result = submit_edit_plan(store, plan, expected_revision=store.current_revision())
        assert not result.valid and result.candidate_timeline is None
        assert any(i.code is ErrorCode.INVALID_ARGUMENT for i in result.issues)
        dispositions.append(result.disposition)
    assert dispositions[:-1] == [PlanDisposition.RETRY] * (MAX_PLAN_ATTEMPTS - 1)
    assert dispositions[-1] is PlanDisposition.MANUAL_REVIEW
