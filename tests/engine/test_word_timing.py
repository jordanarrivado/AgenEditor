"""Word-timing fallback (roadmap Phase 2c): estimate times the recognizer did not give, never drop speech."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_phase2 import _evidence, _plan, _project

from ageneditor.contracts import ModelPreset, Transcript
from ageneditor.planning import submit_edit_plan
from ageneditor.transcription import transcribe_local
from ageneditor.word_timing import RawWord, estimate_syllables, split_tokens, spread, time_segment

CJK_HELLO = "".join(chr(c) for c in (0x4F60, 0x597D, 0x4E16, 0x754C, 0x3002))  # "hello world." in Chinese


def test_tokens_follow_spaces_or_characters() -> None:
    assert split_tokens("hello, world.") == ["hello,", "world."]
    assert split_tokens(CJK_HELLO) == [CJK_HELLO[0], CJK_HELLO[1], CJK_HELLO[2], CJK_HELLO[3:]]
    mixed = "".join(chr(c) for c in (0x3053, 0x3093)) + "AI"  # two kana then Latin
    assert split_tokens(mixed) == [chr(0x3053), chr(0x3093), "AI"]
    samples = ("beautiful", "hi", "rhythm", CJK_HELLO[0], "42")
    assert [estimate_syllables(t) for t in samples] == [3, 1, 1, 1, 1]


def test_spread_is_back_to_back_and_weighted() -> None:
    placed = spread(["a", "beautiful", "hi"], 1_000, 2_000)
    assert placed == [("a", 1_000, 1_200), ("beautiful", 1_200, 1_800), ("hi", 1_800, 2_000)]
    assert spread(["a", "b", "c"], 0, 2) is None  # cannot give each word a microsecond
    tight = spread(["a", "b", "c"], 0, 3)
    assert [(s, e) for _, s, e in tight] == [(0, 1), (1, 2), (2, 3)]


def test_aligned_words_are_kept_and_gaps_are_filled_between_neighbours() -> None:
    aligned = [RawWord("one", 0, 100, 0.9), RawWord("two", 100, 200, 0.8)]
    assert [w.estimated for w in time_segment(aligned, "one two", 0, 200)] == [False, False]

    gap = [
        RawWord("one", 0, 100, 0.9),
        RawWord("two", None, None, 0.5),
        RawWord("three", 300, 400, 0.9),
    ]
    timed = time_segment(gap, "one two three", 0, 400)
    assert [(w.text, w.start_us, w.end_us, w.estimated) for w in timed] == [
        ("one", 0, 100, False),
        ("two", 100, 300, True),
        ("three", 300, 400, False),
    ]


def test_segments_without_usable_word_times_are_estimated_not_dropped() -> None:
    untimed = [RawWord(" hello", None, None, 0.7), RawWord(" beautiful", 50, 50, 0.6)]
    timed = time_segment(untimed, "hello beautiful", 1_000, 1_400)
    # 2 + 3 estimated syllables share the 400 us segment.
    assert [(w.text, w.start_us, w.end_us) for w in timed] == [
        ("hello", 1_000, 1_160),
        ("beautiful", 1_160, 1_400),
    ]
    assert all(w.estimated for w in timed) and timed[0].probability == 0.7

    from_text = time_segment([], CJK_HELLO, 0, 400_000)
    assert [w.text for w in from_text] == split_tokens(CJK_HELLO)
    assert from_text[0].start_us == 0 and from_text[-1].end_us == 400_000

    backwards = [RawWord("a", 500, 600), RawWord("b", 100, 200), RawWord("c", 700, 800)]
    assert [w.estimated for w in time_segment(backwards, "a b c", 0, 1_000)] == [False, True, False]
    assert time_segment([RawWord("a", None, None)], "a", None, None) == []


@pytest.mark.media
def test_transcription_keeps_speech_the_recognizer_could_not_time(
    tmp_path: Path, media_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, asset_id = _project(tmp_path, media_dir)

    class FakeModel:
        def __init__(self, name: str, **options: object) -> None:
            pass

        def transcribe(self, _path: str, **options: object):
            word = SimpleNamespace
            return iter(
                [
                    SimpleNamespace(  # aligned as usual
                        words=[word(word=" hello", start=1.0, end=1.5, probability=0.9)],
                        text="hello", start=1.0, end=1.5,
                    ),
                    SimpleNamespace(  # words with no times (as for some languages)
                        words=[word(word=c, start=None, end=None, probability=0.5) for c in CJK_HELLO[:4]],
                        text=CJK_HELLO, start=2.0, end=3.0,
                    ),
                    SimpleNamespace(words=[], text="no words at all", start=4.0, end=5.5),  # text only
                ]
            ), SimpleNamespace(duration=60.0, language="zh", language_probability=0.8)  # fmt: skip

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeModel))
    transcript = transcribe_local(store, asset_id, ModelPreset.BASE, "transcript_fallback")
    assert [w.text for w in transcript.words] == ["hello", *CJK_HELLO[:4], "no", "words", "at", "all"]
    assert [w.timing for w in transcript.words] == ["aligned"] + ["estimated"] * 8
    assert len(transcript.segments) == 3
    assert transcript.words[1].start_us == 2_000_000 and transcript.words[4].end_us == 3_000_000
    assert transcript.words[-1].end_us == 5_500_000
    # Aligned words keep the existing wire format; estimated ones say so.
    dumped = transcript.model_dump(mode="json")["words"]
    assert "timing" not in dumped[0] and dumped[1]["timing"] == "estimated"
    assert Transcript.model_validate_json(transcript.model_dump_json()) == transcript


@pytest.mark.media
def test_a_cut_on_an_estimated_word_gets_a_warning(tmp_path: Path, media_dir: Path) -> None:
    store, asset_id = _project(tmp_path, media_dir)
    evidence = _evidence(store, asset_id)
    words = [
        w.model_copy(update={"timing": "estimated"}) if w.id == "word_0000009" else w for w in evidence.words
    ]
    # Transcripts are immutable, so the estimated copy gets its own ID and the plan cites it.
    store.save_transcript(evidence.model_copy(update={"id": "transcript_est", "words": words}))
    plan = _plan(store, asset_id)
    cited = [seg.model_copy(update={"transcript_id": "transcript_est"}) for seg in plan.segments]
    plan = plan.model_copy(update={"segments": cited})
    result = submit_edit_plan(store, plan, expected_revision=0)
    messages = [issue.message for issue in result.issues]
    assert any("estimated, not aligned" in message for message in messages)
    assert result.valid  # a warning, not a refusal


@pytest.mark.media
def test_captions_on_estimated_words_are_flagged_for_review(tmp_path: Path, media_dir: Path) -> None:
    from ageneditor.contracts import CaptionCue, CheckStatus
    from ageneditor.editorial import editorial_flags

    store, asset_id = _project(tmp_path, media_dir)
    evidence = _evidence(store, asset_id)
    words = [
        w.model_copy(update={"timing": "estimated"}) if w.id == "word_0000001" else w for w in evidence.words
    ]
    store.save_transcript(evidence.model_copy(update={"id": "transcript_est", "words": words}))

    def cue(cue_id: str, transcript_id: str, word_id: str) -> CaptionCue:
        return CaptionCue(
            cue_id=cue_id, text="x", timeline_in_frames=30, timeline_out_frames=60, source_asset_id=asset_id,
            transcript_id=transcript_id, word_ids=[word_id],
        )  # fmt: skip

    timeline = store.timeline()
    flagged = timeline.model_copy(update={"captions": [cue("est", "transcript_est", "word_0000001")]})
    clean = timeline.model_copy(update={"captions": [cue("ok", "transcript_test", "word_0000001")]})

    def status(t):
        return next(f for f in editorial_flags(store, t) if f.check_id == "estimated_caption_timing")

    assert status(flagged).status is CheckStatus.WARN and status(flagged).measured == "est at 1.00s"
    assert status(clean).status is CheckStatus.PASS
