"""Phase Two source evidence, local transcription mapping and MCP planning."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp import Client, StdioServerParameters

from ageneditor.contracts import (
    AssetRole,
    EditPlan,
    EngineException,
    ErrorCode,
    JobStatus,
    ModelPreset,
    PlanDisposition,
    PlanSegment,
    RenderProfile,
    Transcript,
    TranscriptSegment,
    TranscriptWord,
)
from ageneditor.mcp_server import build_server
from ageneditor.planning import submit_edit_plan
from ageneditor.storage import ProjectStore
from ageneditor.timebase import FrameRate
from ageneditor.transcription import TranscriptionManager, transcribe_local


def _project(tmp_path: Path, media_dir: Path) -> tuple[ProjectStore, str]:
    store = ProjectStore.create(tmp_path, "phase two")
    asset = store.import_asset(media_dir / "talking_head_16x9_30fps_60s.mp4", AssetRole.PRIMARY)
    store.create_timeline(
        RenderProfile(id="test_plan", width=180, height=320, frame_rate=FrameRate(num=30)),
        target_duration_frames=1200,
    )
    return store, asset.id


def _evidence(store: ProjectStore, asset_id: str) -> Transcript:
    asset = store.assets()[asset_id]
    words = []
    segments = []
    for group, base in enumerate((0, 30)):
        group_words = []
        for offset in range(10):
            n = len(words)
            word = TranscriptWord(
                id=f"word_{n:07d}",
                segment_id=f"segment_{group}",
                text=f"statement{n}." if offset == 9 else f"statement{n}",
                start_us=(base + offset * 2 + 1) * 1_000_000,
                end_us=(base + offset * 2 + 2) * 1_000_000,
                probability=0.9,
            )
            words.append(word)
            group_words.append(word)
        segments.append(
            TranscriptSegment(
                id=f"segment_{group}",
                start_us=group_words[0].start_us,
                end_us=group_words[-1].end_us,
                text=" ".join(w.text for w in group_words),
                word_ids=[w.id for w in group_words],
            )
        )
    transcript = Transcript(
        id="transcript_test",
        project_id=store.project_id,
        asset_id=asset_id,
        source_sha256=asset.sha256,
        model_preset=ModelPreset.BASE,
        model_version="test",
        language="en",
        language_probability=0.99,
        duration_us=60_000_000,
        words=words,
        segments=segments,
    )
    store.save_transcript(transcript)
    return transcript


def _plan(store: ProjectStore, asset_id: str) -> EditPlan:
    return EditPlan(
        project_id=store.project_id,
        target_duration_frames=1200,
        brief="Choose a coherent two-part explanation from the registered speaker.",
        segments=[
            PlanSegment(
                asset_id=asset_id,
                transcript_id="transcript_test",
                first_word_id="word_0000000",
                last_word_id="word_0000009",
                source_in_frames=0,
                source_out_frames=600,
                rationale="The opening states the topic and explains its first part.",
                score=0.9,
            ),
            PlanSegment(
                asset_id=asset_id,
                transcript_id="transcript_test",
                first_word_id="word_0000010",
                last_word_id="word_0000019",
                source_in_frames=900,
                source_out_frames=1500,
                rationale="The second passage completes the explanation without repetition.",
                score=0.8,
            ),
        ],
    )


@pytest.mark.media
def test_word_cited_plan_compiles_candidate_and_detects_unproven_audio(
    tmp_path: Path, media_dir: Path
) -> None:
    store, asset_id = _project(tmp_path, media_dir)
    _evidence(store, asset_id)
    plan = _plan(store, asset_id)
    result = submit_edit_plan(store, plan, expected_revision=0)
    assert result.valid and result.disposition is PlanDisposition.VALID
    assert result.candidate_timeline is not None
    assert result.candidate_timeline.duration_frames == 1200
    assert store.current_revision() == 0  # A proposal cannot mutate the timeline.
    bad = plan.model_copy(deep=True)
    bad.segments[0].first_word_id = "word_0000005"
    bad.segments[0].source_in_frames = 0
    rejected = submit_edit_plan(store, bad, expected_revision=0)
    assert not rejected.valid
    assert any("uncited" in issue.message for issue in rejected.issues)
    assert rejected.disposition is PlanDisposition.RETRY
    final = submit_edit_plan(store, bad, expected_revision=0)
    assert final.disposition is PlanDisposition.MANUAL_REVIEW
    with pytest.raises(EngineException) as exc:
        submit_edit_plan(store, plan, expected_revision=0)
    assert exc.value.error.code is ErrorCode.PLAN_RETRY_LIMIT


@pytest.mark.media
def test_transcribe_local_maps_words_and_preserves_silence(
    tmp_path: Path, media_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, asset_id = _project(tmp_path, media_dir)

    class FakeModel:
        def __init__(self, name: str, **options: object) -> None:
            assert name == "base"
            assert options["local_files_only"] is True

        def transcribe(self, _path: str, **options: object):
            assert options["word_timestamps"] is True
            assert options["vad_filter"] is True
            return iter(
                [
                    SimpleNamespace(
                        words=[
                            SimpleNamespace(word=" hello", start=5.0, end=5.5, probability=0.9),
                            SimpleNamespace(word=" world.", start=6.0, end=6.5, probability=0.8),
                        ],
                        text="hello world.",
                        end=6.5,
                    )
                ]
            ), SimpleNamespace(duration=60.0, language="en", language_probability=0.99)

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeModel))
    transcript = transcribe_local(store, asset_id, ModelPreset.BASE, "transcript_fake")
    assert [w.start_us for w in transcript.words] == [5_000_000, 6_000_000]
    assert transcript.words[0].segment_id == transcript.segments[0].id
    assert transcript.silence_candidates[0].start_us == 0
    assert transcript.silence_candidates[0].end_us == 5_000_000
    assert transcript.silence_candidates[-1].end_us == 60_000_000


@pytest.mark.media
@pytest.mark.parametrize("stdio", [False, True])
def test_mcp_transcript_page_and_plan(tmp_path: Path, media_dir: Path, stdio: bool) -> None:
    store, asset_id = _project(tmp_path, media_dir)
    _evidence(store, asset_id)
    server = (
        StdioServerParameters(
            command=sys.executable,
            args=["-m", "ageneditor.cli", "mcp", "--workspace", str(tmp_path)],
            env=os.environ.copy(),
        )
        if stdio
        else build_server(tmp_path)
    )

    async def exercise() -> None:
        async with Client(server, raise_exceptions=True) as client:
            listed = await client.list_tools()
            names = {tool.name for tool in listed.tools}
            assert {"transcribe_asset", "get_transcript", "submit_edit_plan"} <= names
            page = await client.call_tool(
                "get_transcript",
                {
                    "project_id": store.project_id,
                    "transcript_id": "transcript_test",
                    "start_word": 10,
                    "limit": 2,
                    "context_words": 2,
                },
            )
            assert not page.is_error
            assert page.structured_content["words"][0]["id"] == "word_0000008"
            assert page.structured_content["total_words"] == 20
            assert page.structured_content["hook_candidates"][0]["segment_id"] in {"segment_0", "segment_1"}
            proposed = await client.call_tool(
                "submit_edit_plan",
                {
                    "project_id": store.project_id,
                    "plan": _plan(store, asset_id).model_dump(mode="json"),
                    "expected_revision": 0,
                },
            )
            assert not proposed.is_error
            assert proposed.structured_content["valid"] is True
            assert proposed.structured_content["candidate_timeline"]["clips"][1]["asset_id"] == asset_id
            committed = await client.call_tool(
                "commit_timeline",
                {
                    "project_id": store.project_id,
                    "timeline": proposed.structured_content["candidate_timeline"],
                    "expected_revision": 0,
                },
            )
            assert not committed.is_error
            assert committed.structured_content["revision"] == 1

    asyncio.run(exercise())


@pytest.mark.media
def test_source_insufficient_is_explicit(tmp_path: Path, media_dir: Path) -> None:
    store, asset_id = _project(tmp_path, media_dir)
    asset = store.assets()[asset_id]
    store.save_transcript(
        Transcript(
            id="transcript_test",
            project_id=store.project_id,
            asset_id=asset_id,
            source_sha256=asset.sha256,
            model_preset=ModelPreset.BASE,
            model_version="test",
            language="en",
            duration_us=60_000_000,
        )
    )
    result = submit_edit_plan(store, _plan(store, asset_id), expected_revision=0)
    assert not result.valid
    assert result.disposition is PlanDisposition.SOURCE_INSUFFICIENT


@pytest.mark.media
def test_transcription_job_reports_missing_local_model(
    tmp_path: Path, media_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, asset_id = _project(tmp_path, media_dir)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "empty-model-cache"))
    manager = TranscriptionManager(store)
    job = manager.start(asset_id, ModelPreset.TINY)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not manager.store.get_job(job.id).is_terminal:
        time.sleep(0.1)
    result = manager.store.get_job(job.id)
    assert result.status is JobStatus.FAILED
    assert result.error is not None
    assert result.error.code is ErrorCode.TRANSCRIPTION_FAILED
    assert not store.safe_path(result.output_path).exists()


@pytest.mark.media
def test_optional_faster_whisper_decodes_fixture_audio(media_dir: Path) -> None:
    pytest.importorskip("faster_whisper")
    from faster_whisper.audio import decode_audio

    samples = decode_audio(str(media_dir / "talking_head_16x9_30fps_60s.mp4"))
    assert len(samples) >= 16_000
