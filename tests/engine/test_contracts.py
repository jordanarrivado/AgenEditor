import json

import pytest
from pydantic import ValidationError

from ageneditor.contracts import (
    REEL_FINAL,
    Asset,
    AssetKind,
    AssetRole,
    AudioStream,
    CaptionCue,
    CheckKind,
    CheckStatus,
    Clip,
    EngineError,
    ErrorCode,
    Job,
    JobKind,
    JobStatus,
    Provenance,
    QACheck,
    QAReport,
    RenderProfile,
    Timeline,
    TrackId,
    VideoStream,
    export_json_schemas,
    validate_against_assets,
)
from ageneditor.timebase import FrameRate

SHA = "a" * 64


def speaker(duration_frames: int = 21_600) -> Asset:
    return Asset(
        id="speaker_01",
        project_id="reel_001",
        kind=AssetKind.VIDEO,
        role=AssetRole.PRIMARY,
        relative_path="originals/interview.mp4",
        original_filename="interview.mp4",
        sha256=SHA,
        size_bytes=1,
        video=VideoStream(
            index=0,
            codec="h264",
            width=1920,
            height=1080,
            frame_rate=FrameRate(num=30),
            time_base_num=1,
            time_base_den=15360,
            duration_frames=duration_frames,
        ),
        audio=AudioStream(
            index=1,
            codec="aac",
            sample_rate=48000,
            channels=2,
            time_base_num=1,
            time_base_den=48000,
            duration_samples=duration_frames * 1600,
        ),
    )


def spec_example(**overrides) -> Timeline:
    """Spec §5.2 example as a v1 Timeline: three cuts, 1200 frames."""
    cuts = [(360, 510), (1350, 1800), (2760, 3360)]
    clips, t = [], 0
    for i, (a, b) in enumerate(cuts):
        clips.append(
            Clip(
                clip_id=f"v{i}",
                asset_id="speaker_01",
                track=TrackId.V1,
                source_in_frames=a,
                source_out_frames=b,
                timeline_in_frames=t,
            )
        )
        clips.append(
            Clip(
                clip_id=f"a{i}",
                asset_id="speaker_01",
                track=TrackId.A1,
                source_in_frames=a,
                source_out_frames=b,
                timeline_in_frames=t,
            )
        )
        t += b - a
    data = dict(
        project_id="reel_001",
        revision=3,
        parent_revision=2,
        target_duration_frames=1200,
        render_profile=REEL_FINAL,
        clips=clips,
        provenance=Provenance(author="agent", tool="commit_timeline"),
    )
    data.update(overrides)
    return Timeline(**data)


def test_spec_example_validates_and_totals_40s():
    tl = spec_example()
    assert tl.duration_frames == 1200
    assert validate_against_assets(tl, {"speaker_01": speaker()}, duration_tolerance_frames=7) == []


@pytest.mark.parametrize(
    "path",
    [
        "../secrets.txt",
        "/etc/passwd",
        "C:/Windows/x.mp4",
        "originals\\a.mp4",
        "originals/../../a",
        "",
        "originals//a",
        "\\\\server\\share\\a.mp4",
        "originals/./a.mp4",
    ],
)
def test_asset_paths_cannot_escape_project(path):
    with pytest.raises(ValidationError):
        speaker().model_copy(update={}).model_validate({**speaker().model_dump(), "relative_path": path})


def test_assets_must_live_under_originals():
    with pytest.raises(ValidationError):
        Asset.model_validate({**speaker().model_dump(), "relative_path": "renders/x.mp4"})


def test_unknown_fields_rejected():
    with pytest.raises(ValidationError):
        Timeline.model_validate({**spec_example().model_dump(), "ffmpeg_filter": "drawtext=..."})


def test_v1_gaps_overlaps_and_bad_ranges_rejected():
    with pytest.raises(ValidationError, match="gap"):
        Timeline.model_validate(
            {
                **spec_example().model_dump(),
                "clips": [
                    Clip(
                        clip_id="v0",
                        asset_id="speaker_01",
                        track="V1",
                        source_in_frames=0,
                        source_out_frames=30,
                        timeline_in_frames=10,
                    ).model_dump()
                ],
            }
        )
    with pytest.raises(ValidationError):
        Clip(
            clip_id="x",
            asset_id="s",
            track="V1",
            source_in_frames=30,
            source_out_frames=30,
            timeline_in_frames=0,
        )
    with pytest.raises(ValidationError, match="parent_revision"):
        spec_example(revision=2, parent_revision=2)


def test_semantic_validation_reports_all_issues():
    tl = spec_example()
    clips = [c.model_copy(update={"asset_id": "ghost"}) if c.clip_id == "a0" else c for c in tl.clips]
    tl = spec_example(clips=clips)
    issues = validate_against_assets(tl, {"speaker_01": speaker(duration_frames=3000)})
    codes = sorted(i.code for i in issues)
    assert ErrorCode.ASSET_NOT_FOUND in codes
    assert ErrorCode.RANGE_OUT_OF_BOUNDS in codes  # 3360 > 3000


def test_role_must_match_track():
    tl = spec_example()
    broll_on_v1 = speaker().model_copy(update={"role": AssetRole.BROLL})
    issues = validate_against_assets(tl, {"speaker_01": broll_on_v1})
    assert any(i.code == ErrorCode.INVALID_TIMELINE and "cannot be placed" in i.message for i in issues)


def test_captions_need_provenance():
    with pytest.raises(ValidationError, match="word_ids"):
        CaptionCue(
            cue_id="c1",
            text="Hello",
            timeline_in_frames=0,
            timeline_out_frames=30,
            source_asset_id="speaker_01",
        )
    CaptionCue(
        cue_id="c1",
        text="Hello",
        timeline_in_frames=0,
        timeline_out_frames=30,
        source_asset_id="speaker_01",
        word_ids=["w1"],
        transcript_id="transcript_1",
    )


def test_render_profile_is_enumerated_and_even():
    assert (REEL_FINAL.width, REEL_FINAL.height, str(REEL_FINAL.frame_rate)) == (1080, 1920, "30/1")
    assert REEL_FINAL.loudness_lufs == -16.0 and REEL_FINAL.true_peak_dbtp == -1.0
    with pytest.raises(ValidationError):
        RenderProfile(id="odd", width=1081, height=1920, frame_rate=FrameRate(num=30))
    with pytest.raises(ValidationError):
        RenderProfile.model_validate({**REEL_FINAL.model_dump(), "video_codec": "prores"})


def test_job_rules():
    with pytest.raises(ValidationError, match="pin"):
        Job(id="j1", project_id="reel_001", kind=JobKind.FINAL_RENDER)
    with pytest.raises(ValidationError, match="error"):
        Job(id="j1", project_id="reel_001", kind=JobKind.PROBE, status=JobStatus.FAILED)
    with pytest.raises(ValidationError, match="progress"):
        Job(id="j1", project_id="reel_001", kind=JobKind.PROBE, status=JobStatus.SUCCEEDED, progress=0.5)
    j = Job(
        id="j1",
        project_id="reel_001",
        kind=JobKind.PREVIEW_RENDER,
        timeline_revision=3,
        status=JobStatus.INTERRUPTED,
        error=EngineError(code=ErrorCode.RENDER_FAILED, message="engine stopped"),
    )
    assert j.is_terminal


def test_qa_overall_never_passes_on_skips_or_failures():
    def report(*statuses, kind=CheckKind.TECHNICAL):
        return QAReport(
            id="q",
            job_id="j",
            project_id="reel_001",
            timeline_revision=1,
            checks=[QACheck(check_id=f"c{i}", status=s, kind=kind) for i, s in enumerate(statuses)],
        )

    assert report(CheckStatus.PASS, CheckStatus.PASS).overall is CheckStatus.PASS
    assert report(CheckStatus.PASS, CheckStatus.SKIPPED).overall is CheckStatus.WARN
    assert report(CheckStatus.PASS, CheckStatus.FAIL).overall is CheckStatus.FAIL
    assert (
        report(CheckStatus.PASS, kind=CheckKind.EDITORIAL).overall is CheckStatus.FAIL
    )  # no technical checks
    assert json.loads(report(CheckStatus.PASS).model_dump_json())["overall"] == "pass"


def test_json_roundtrip_and_schema_export(tmp_path):
    tl = spec_example()
    assert Timeline.model_validate_json(tl.model_dump_json()) == tl
    written = export_json_schemas(tmp_path)
    names = sorted(p.name for p in written)
    assert "timeline.v1.schema.json" in names and "qa_report.v2.schema.json" in names
    assert "asset.v2.schema.json" in names
    schema = json.loads((tmp_path / "timeline.v1.schema.json").read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False


def test_committed_schemas_are_up_to_date(tmp_path):
    """schemas/*.json must be regenerated (`ageneditor schema`) whenever contracts change."""
    from pathlib import Path

    committed = Path(__file__).resolve().parents[2] / "schemas"
    for fresh in export_json_schemas(tmp_path):
        assert (committed / fresh.name).read_text(encoding="utf-8") == fresh.read_text(encoding="utf-8"), (
            f"{fresh.name} is stale — run `uv run ageneditor schema`"
        )
