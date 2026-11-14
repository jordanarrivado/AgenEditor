"""Phase 3: output-time captions, corrections, B-roll, audio mix, revisions/undo and QA flags.

Transcripts are synthetic evidence (no ASR model in CI); media, mapping, rendering and QA are real.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from mcp import Client

from ageneditor.broll import suggest_broll
from ageneditor.captions import build_captions, to_ass, to_srt
from ageneditor.contracts import (
    AddOverlay,
    AssetRole,
    AudioMix,
    BuildCaptions,
    CaptionCue,
    CheckKind,
    CheckStatus,
    Clip,
    ColorPreset,
    CorrectWord,
    EngineException,
    ErrorCode,
    ModelPreset,
    MoveClip,
    RemoveClip,
    RenderProfile,
    RevisionPatch,
    SetAudioMix,
    SetColorPreset,
    SetTransform,
    Timeline,
    TrackId,
    Transcript,
    TranscriptSegment,
    TranscriptWord,
    Transform,
    TrimClip,
)
from ageneditor.mcp_server import build_server
from ageneditor.media.analysis import analyze_output
from ageneditor.media.hashing import sha256_file
from ageneditor.media.probe import probe
from ageneditor.media.qa import run_qa
from ageneditor.media.render import render_timeline
from ageneditor.media.tools import find_tool
from ageneditor.revisions import apply_patch, revert_to
from ageneditor.storage import ProjectStore
from ageneditor.timebase import FrameRate

FPS = 30
VOCAB = ["we", "brewed", "coffee", "every", "morning", "before", "the", "studio", "opened", "today."]


def _transcript(store: ProjectStore, asset_id: str) -> Transcript:
    """One word every 0.5 s (0.4 s long) across the 60 s source, ten-word sentences."""
    asset = store.assets()[asset_id]
    words, segments = [], []
    for s in range(12):
        seg_words = []
        for i in range(10):
            n = s * 10 + i
            start = n * 500_000
            word = TranscriptWord(
                id=f"word_{n:07d}",
                segment_id=f"segment_{s:04d}",
                text=VOCAB[i],
                start_us=start,
                end_us=start + 400_000,
                probability=0.4 if n == 3 else 0.95,
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
        id="transcript_p3",
        project_id=store.project_id,
        asset_id=asset_id,
        source_sha256=asset.sha256,
        model_preset=ModelPreset.BASE,
        model_version="synthetic",
        language="en",
        duration_us=60_000_000,
        words=words,
        segments=segments,
    )
    store.save_transcript(transcript)
    return transcript


@pytest.fixture
def project(tmp_path: Path, media_dir: Path) -> dict:
    store = ProjectStore.create(tmp_path, "phase three")
    primary = store.import_asset(media_dir / "talking_head_16x9_30fps_60s.mp4", AssetRole.PRIMARY)
    broll = store.import_asset(media_dir / "broll_9x16_10s.mp4", AssetRole.BROLL)
    music = store.import_asset(media_dir / "music_bed_30s.m4a", AssetRole.MUSIC)
    store.create_timeline(
        RenderProfile(id="p3_test", width=180, height=320, frame_rate=FrameRate(num=FPS)),
        target_duration_frames=180,
    )
    _transcript(store, primary.id)
    base = store.timeline(0)
    # Two cuts: source 0-3 s and 30-33 s → output 0-90 and 90-180.
    clips = [
        Clip(clip_id="cut_a", asset_id=primary.id, track=TrackId.V1, source_in_frames=0,
             source_out_frames=90, timeline_in_frames=0),
        Clip(clip_id="cut_b", asset_id=primary.id, track=TrackId.V1, source_in_frames=900,
             source_out_frames=990, timeline_in_frames=90),
    ]  # fmt: skip
    store.commit_timeline(base.model_copy(update={"clips": clips}), 0)
    return {"store": store, "primary": primary.id, "broll": broll.id, "music": music.id}


def _patch(store: ProjectStore, *ops, summary: str = "test revision") -> RevisionPatch:
    return RevisionPatch(project_id=store.project_id, summary=summary, ops=list(ops))


def test_captions_map_words_to_output_time(project: dict) -> None:
    store: ProjectStore = project["store"]
    result = apply_patch(store, _patch(store, BuildCaptions()), store.current_revision())
    cues = result.timeline.captions
    assert cues and result.diff.captions_changed
    cited = [w for cue in cues for w in cue.word_ids]
    # Words 0-5 lie in source 0-3 s; words 60-65 in 30-33 s. Nothing else may be captioned.
    assert cited == [f"word_{n:07d}" for n in [*range(6), *range(60, 66)]]
    second = next(cue for cue in cues if "word_0000060" in cue.word_ids)
    assert second.timeline_in_frames == 90  # source 30.0 s → output frame 90 exactly
    for cue in cues:
        assert cue.text.count("\n") <= 1
        assert all(len(line) <= 24 for line in cue.text.split("\n"))
        # No cue straddles the cut at frame 90.
        assert cue.timeline_out_frames <= 90 or cue.timeline_in_frames >= 90
    srt = to_srt(result.timeline)
    assert srt.startswith("1\n00:00:00,000 --> ")
    assert "00:00:03,000 --> " in srt  # second take starts exactly at 3 s of output


def test_word_correction_is_versioned_without_retranscription(
    project: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    store: ProjectStore = project["store"]
    monkeypatch.setattr(
        "ageneditor.transcription.transcribe_local",
        lambda *a, **k: pytest.fail("revisions must not re-transcribe"),
    )
    built = apply_patch(store, _patch(store, BuildCaptions()), store.current_revision()).timeline
    fixed = apply_patch(
        store,
        _patch(store, CorrectWord(transcript_id="transcript_p3", word_id="word_0000002", text="cocoa")),
        built.revision,
    )
    cue = next(c for c in fixed.timeline.captions if "word_0000002" in c.word_ids)
    before = next(c for c in built.captions if "word_0000002" in c.word_ids)
    assert "cocoa" in cue.text and "coffee" not in cue.text and cue.corrected
    assert (cue.timeline_in_frames, cue.timeline_out_frames) == (
        before.timeline_in_frames,
        before.timeline_out_frames,
    )
    assert fixed.diff.corrected_word_ids == ["word_0000002"]
    # Removing the correction restores the transcript text in yet another revision.
    restored = apply_patch(
        store,
        _patch(store, CorrectWord(transcript_id="transcript_p3", word_id="word_0000002")),
        fixed.timeline.revision,
    )
    assert "coffee" in next(c for c in restored.timeline.captions if "word_0000002" in c.word_ids).text


def test_fabricated_or_unknown_caption_words_are_rejected(project: dict) -> None:
    store: ProjectStore = project["store"]
    current = store.timeline()
    forged = CaptionCue(
        cue_id="cue_forged",
        text="we never brewed coffee",
        timeline_in_frames=0,
        timeline_out_frames=30,
        source_asset_id=project["primary"],
        transcript_id="transcript_p3",
        word_ids=["word_0000000", "word_0000001", "word_0000002"],
    )
    with pytest.raises(EngineException) as info:
        store.commit_timeline(current.model_copy(update={"captions": [forged]}), current.revision)
    assert info.value.error.code is ErrorCode.INVALID_TIMELINE
    ghost = forged.model_copy(update={"text": "hello", "word_ids": ["word_9999999"]})
    issues = store.validation_issues(current.model_copy(update={"captions": [ghost]}))
    assert any(i.code is ErrorCode.TRANSCRIPT_NOT_FOUND for i in issues)


def test_ass_escapes_override_syntax(project: dict) -> None:
    store: ProjectStore = project["store"]
    apply_patch(
        store,
        _patch(
            store,
            CorrectWord(transcript_id="transcript_p3", word_id="word_0000000", text="{\\b1}we"),
            BuildCaptions(),
        ),
        store.current_revision(),
    )
    ass = to_ass(store.timeline())
    events = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    assert events and "(/b1)we" in events[0] and "{" not in "".join(events)


def test_revisions_ripple_carry_overlays_and_revert(project: dict) -> None:
    store: ProjectStore = project["store"]
    start = store.current_revision()
    broll = Clip(
        clip_id="broll_1",
        asset_id=project["broll"],
        track=TrackId.V2,
        source_in_frames=0,
        source_out_frames=45,
        timeline_in_frames=100,
    )
    added = apply_patch(store, _patch(store, AddOverlay(clip=broll), BuildCaptions()), start)
    assert added.diff.added_clip_ids == ["broll_1"]
    moved = apply_patch(store, _patch(store, MoveClip(clip_id="cut_b", to_index=0)), added.timeline.revision)
    by_id = {c.clip_id: c for c in moved.timeline.clips}
    assert by_id["cut_b"].timeline_in_frames == 0 and by_id["cut_a"].timeline_in_frames == 90
    assert by_id["broll_1"].timeline_in_frames == 10  # stayed 10 frames into cut_b
    assert moved.timeline.captions[0].word_ids[0] == "word_0000060"  # captions follow the new order
    trimmed = apply_patch(
        store,
        _patch(store, TrimClip(clip_id="cut_a", source_in_frames=0, source_out_frames=60)),
        moved.timeline.revision,
    )
    assert trimmed.timeline.duration_frames == 150
    removed = apply_patch(store, _patch(store, RemoveClip(clip_id="cut_b")), trimmed.timeline.revision)
    assert "broll_1" not in {c.clip_id for c in removed.timeline.clips}
    assert any("overlay removed" in i.message for i in removed.issues)
    with pytest.raises(EngineException) as stale:
        apply_patch(store, _patch(store, SetColorPreset(preset=ColorPreset.WARM)), start)
    assert stale.value.error.code is ErrorCode.REVISION_CONFLICT
    reverted = revert_to(store, added.timeline.revision, removed.timeline.revision)
    assert reverted.timeline.revision == removed.timeline.revision + 1
    assert reverted.timeline.clips == added.timeline.clips
    assert reverted.timeline.captions == added.timeline.captions
    assert store.timeline(removed.timeline.revision).clips == removed.timeline.clips  # history kept


def _frame(path: Path, frame: int) -> bytes:
    args = [
        str(find_tool("ffmpeg")), "-v", "error", "-i", str(path), "-vf", f"select=eq(n\\,{frame})",
        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]  # fmt: skip
    return subprocess.run(args, capture_output=True, check=True, timeout=60).stdout


def _difference(a: bytes, b: bytes) -> float:
    assert len(a) == len(b) and a
    return sum(abs(x - y) for x, y in zip(a, b, strict=True)) / len(a)


@pytest.mark.media
def test_full_render_with_broll_captions_music_and_qa(project: dict, tmp_path: Path) -> None:
    store: ProjectStore = project["store"]
    hashes = {a.id: a.sha256 for a in store.assets().values()}
    plain = apply_patch(
        store,
        _patch(store, BuildCaptions(), SetTransform(clip_id="cut_b", transform=Transform(scale=1.2))),
        store.current_revision(),
    ).timeline
    music = Clip(
        clip_id="music_1",
        asset_id=project["music"],
        track=TrackId.A2,
        source_in_frames=0,
        source_out_frames=180,
        timeline_in_frames=0,
        gain_db=-6,
        fade_out_frames=15,
    )
    broll = Clip(
        clip_id="broll_1",
        asset_id=project["broll"],
        track=TrackId.V2,
        source_in_frames=0,
        source_out_frames=45,
        timeline_in_frames=100,
        fade_in_frames=3,
    )
    full = apply_patch(
        store,
        _patch(
            store,
            AddOverlay(clip=broll),
            AddOverlay(clip=music),
            SetColorPreset(preset=ColorPreset.WARM),
        ),
        plain.revision,
    ).timeline
    without_broll, with_broll = tmp_path / "plain.mp4", tmp_path / "full.mp4"
    render_timeline(store, plain, without_broll)
    render_timeline(store, full, with_broll)
    assert not list(tmp_path.glob(".captions.*"))  # temporary caption script cleaned up
    no_captions = tmp_path / "no_captions.mp4"
    render_timeline(store, plain.model_copy(update={"captions": []}), no_captions)
    # Burned-in text must actually appear in the caption band (bottom safe area), not elsewhere.
    band = slice(180 * 170, 180 * 260)  # rows 170-260 of a 180 px wide gray frame
    with_text, without_text = _frame(without_broll, 15), _frame(no_captions, 15)
    assert _difference(with_text[band], without_text[band]) > 3
    assert _difference(with_text[: 180 * 100], without_text[: 180 * 100]) < 1.5

    meta = probe(with_broll)
    assert (meta.video.width, meta.video.height, meta.video.duration_frames) == (180, 320, 180)
    # B-roll replaces the picture only inside its frames; captions burn in where cues are.
    assert _difference(_frame(without_broll, 120), _frame(with_broll, 120)) > 20
    assert _difference(_frame(without_broll, 60), _frame(with_broll, 60)) < 12

    report = run_qa(store, "job_p3_full", full.revision, with_broll)
    status = {c.check_id: c for c in report.checks}
    technical = [c for c in report.checks if c.kind is CheckKind.TECHNICAL]
    assert all(c.status is CheckStatus.PASS for c in technical if c.check_id != "silent_gaps"), technical
    assert status["caption_provenance"].status is CheckStatus.PASS
    assert status["loudness"].status is CheckStatus.PASS
    assert status["true_peak"].status is CheckStatus.PASS
    assert status["uncertain_caption_words"].status is CheckStatus.WARN  # word 3 has p=0.4
    assert {c.check_id for c in report.checks if c.kind is CheckKind.EDITORIAL} >= {
        "jump_cuts",
        "framing",
        "broll_balance",
        "hook_timing",
    }
    assert {a.id: sha256_file(store.safe_path(a.relative_path)) for a in store.assets().values()} == hashes


@pytest.mark.media
def test_broll_overlay_is_frame_exact(project: dict, tmp_path: Path) -> None:
    """The B-roll fixture switches colour at its frame 150; cite 149-151 and check each output frame."""
    store: ProjectStore = project["store"]
    broll = Clip(
        clip_id="broll_edge",
        asset_id=project["broll"],
        track=TrackId.V2,
        source_in_frames=149,
        source_out_frames=152,
        timeline_in_frames=100,
    )
    timeline = apply_patch(store, _patch(store, AddOverlay(clip=broll)), store.current_revision()).timeline
    out = tmp_path / "edge.mp4"
    render_timeline(store, timeline, out)

    def top_luma(frame: int) -> float:
        pixels = _frame(out, frame)[: 180 * 40]  # plain colour band above any slate text
        return sum(pixels) / len(pixels)

    speaker, first, second, third, after = (top_luma(f) for f in (99, 100, 101, 102, 103))
    assert abs(first - second) > 10, "frame 100 must show B-roll frame 149 (first colour section)"
    assert abs(second - third) < 3  # 150 and 151 share the second section
    assert abs(speaker - after) < 3 and abs(speaker - first) > 10  # back to the speaker at 103


@pytest.mark.media
def test_music_ducks_under_dialogue(project: dict, tmp_path: Path) -> None:
    store: ProjectStore = project["store"]
    music = Clip(
        clip_id="music_1",
        asset_id=project["music"],
        track=TrackId.A2,
        source_in_frames=0,
        source_out_frames=180,
        timeline_in_frames=0,
        gain_db=0,
    )
    raw = AudioMix(normalize=False, duck_music=False)
    ducked = raw.model_copy(update={"duck_music": True, "duck_ratio": 20, "duck_threshold_db": -40})
    loudness = {}
    for name, mix in (("raw", raw), ("ducked", ducked)):
        result = apply_patch(
            store,
            _patch(store, *([AddOverlay(clip=music)] if name == "raw" else []), SetAudioMix(audio=mix)),
            store.current_revision(),
        )
        out = tmp_path / f"{name}.mp4"
        render_timeline(store, result.timeline, out)
        loudness[name] = analyze_output(out, 6_000_000).integrated_lufs
    assert loudness["ducked"] < loudness["raw"] - 0.5, loudness


def test_broll_suggestions_and_mcp_phase_three_tools(project: dict, tmp_path: Path) -> None:
    store: ProjectStore = project["store"]
    store.set_asset_labels(project["broll"], ["coffee pour", "studio door"])
    suggestions = suggest_broll(store, store.timeline())
    assert suggestions and suggestions[0].asset_id == project["broll"]
    assert "coffee" in suggestions[0].matched_terms
    first = suggestions[0]
    assert first.timeline_out_frames - first.timeline_in_frames >= 45  # ≥ 1.5 s

    async def exercise() -> None:
        async with Client(build_server(store.root.parent), raise_exceptions=True) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            assert {
                "apply_revision",
                "revert_revision",
                "suggest_broll",
                "set_asset_labels",
                "get_captions_srt",
            } <= names
            pid = store.project_id
            rev = store.current_revision()
            applied = await client.call_tool(
                "apply_revision",
                {
                    "project_id": pid,
                    "expected_revision": rev,
                    "patch": {
                        "project_id": pid,
                        "summary": "Add captions and a warm look",
                        "ops": [{"op": "build_captions"}, {"op": "set_color_preset", "preset": "warm"}],
                    },
                },
            )
            assert not applied.is_error, applied
            assert applied.structured_content["diff"]["settings_changed"] == ["color_preset"]
            srt = await client.call_tool("get_captions_srt", {"project_id": pid})
            assert srt.structured_content["cue_count"] > 0 and "-->" in srt.structured_content["srt"]
            bad = await client.call_tool(
                "set_asset_labels", {"project_id": pid, "asset_id": project["broll"], "labels": ["{\\x}"]}
            )
            assert bad.is_error
            undo = await client.call_tool(
                "revert_revision", {"project_id": pid, "target_revision": rev, "expected_revision": rev + 1}
            )
            assert not undo.is_error and undo.structured_content["timeline"]["captions"] == []

    asyncio.run(exercise())


def test_build_captions_requires_transcript(tmp_path: Path, media_dir: Path) -> None:
    store = ProjectStore.create(tmp_path, "no transcript")
    asset = store.import_asset(media_dir / "talking_head_16x9_30fps_60s.mp4", AssetRole.PRIMARY)
    store.create_timeline(
        RenderProfile(id="p3_none", width=180, height=320, frame_rate=FrameRate(num=FPS)), 90
    )
    clip = Clip(clip_id="c", asset_id=asset.id, track=TrackId.V1, source_in_frames=0, source_out_frames=90,
                timeline_in_frames=0)  # fmt: skip
    timeline: Timeline = store.timeline(0).model_copy(update={"clips": [clip]})
    with pytest.raises(EngineException) as info:
        build_captions(store, timeline)
    assert info.value.error.code is ErrorCode.TRANSCRIPT_NOT_FOUND
