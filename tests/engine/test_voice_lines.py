"""Voice-line requests (roadmap Phase 2c): audio requests, the voice role, track A4 and the mix."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from ageneditor.contracts import (
    AssetChecklist,
    AssetOrigin,
    AssetProvenance,
    AssetRequest,
    AssetRole,
    AudioMix,
    CheckStatus,
    Clip,
    EngineException,
    RenderProfile,
    TrackId,
)
from ageneditor.editorial import editorial_flags
from ageneditor.media.render import render_timeline
from ageneditor.media.tools import find_tool
from ageneditor.storage import ProjectStore
from ageneditor.timebase import FrameRate

GENERATED = AssetProvenance(origin=AssetOrigin.GENERATED_EXTERNAL, tool_name="Google AI Studio")
PROFILE = RenderProfile(id="voice", width=180, height=320, frame_rate=FrameRate(num=30))


def _tone(path: Path, seconds: float, frequency: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(find_tool("ffmpeg")), "-v", "error", "-y", "-f", "lavfi",
            "-i", f"sine=frequency={frequency}:sample_rate=48000:duration={seconds}",
            "-ac", "2", str(path),
        ],
        check=True,
        timeout=60,
    )  # fmt: skip
    return path


def _mean_volume(path: Path, start: float, duration: float, music_only: bool = False) -> float:
    # The music fixture is a 220-330 Hz chord and the test voice a 2 kHz tone, so a low-pass
    # filter hears the music alone.
    filters = "lowpass=f=600,lowpass=f=600,volumedetect" if music_only else "volumedetect"
    result = subprocess.run(
        [
            str(find_tool("ffmpeg")), "-v", "info", "-ss", str(start), "-t", str(duration), "-i", str(path),
            "-af", filters, "-vn", "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )  # fmt: skip
    return float(re.search(r"mean_volume: (-?[\d.]+) dB", result.stderr).group(1))


def _voice_request(request_id: str = "voice_01") -> AssetRequest:
    return AssetRequest(
        id=request_id, kind="audio", purpose="intro", prompt="Warm, calm", line="Three tips for sleep."
    )


def test_voice_line_requests_carry_the_exact_words() -> None:
    request = _voice_request()
    assert request.model_dump(mode="json")["line"] == "Three tips for sleep."
    with pytest.raises(ValidationError):
        AssetRequest(id="voice_02", kind="audio", prompt="Warm, calm")
    with pytest.raises(ValidationError):
        AssetRequest(id="broll_01", kind="image", prompt="A street", line="Hello")
    # Image and video requests keep their existing wire format (no "line" key).
    image = AssetRequest(id="broll_01", kind="image", prompt="A street")
    assert "line" not in image.model_dump(mode="json")
    assert AssetRequest.model_validate_json(image.model_dump_json()) == image


@pytest.mark.media
def test_voice_role_and_track_rules(tmp_path: Path, media_dir: Path) -> None:
    store = ProjectStore.create(tmp_path, "voice roles")
    with pytest.raises(EngineException):
        store.import_asset(media_dir / "broll_9x16_10s.mp4", AssetRole.VOICE)
    voice = store.import_asset(_tone(tmp_path / "line.wav", 1.0, 440), AssetRole.VOICE, GENERATED)
    music = store.import_asset(media_dir / "music_bed_30s.m4a", AssetRole.MUSIC)
    primary = store.import_asset(media_dir / "talking_head_16x9_30fps_60s.mp4", AssetRole.PRIMARY)
    seed = store.create_timeline(PROFILE, 90)
    base = Clip(
        clip_id="v", asset_id=primary.id, track=TrackId.V1,
        source_in_frames=0, source_out_frames=90, timeline_in_frames=0,
    )  # fmt: skip

    def placed(clip_id: str, asset_id: str, track: TrackId, at: int = 0) -> Clip:
        return Clip(
            clip_id=clip_id, asset_id=asset_id, track=track,
            source_in_frames=0, source_out_frames=30, timeline_in_frames=at,
        )  # fmt: skip

    ok = seed.model_copy(update={"clips": [base, placed("line", voice.id, TrackId.A4)]})
    assert [i for i in store.validation_issues(ok) if i.severity == "error"] == []
    wrong = seed.model_copy(
        update={"clips": [base, placed("line", voice.id, TrackId.A2), placed("m", music.id, TrackId.A4, 40)]}
    )
    messages = [issue.message for issue in store.validation_issues(wrong)]
    assert any("voice asset cannot be placed on A2" in m for m in messages)
    assert any("music asset cannot be placed on A4" in m for m in messages)
    with pytest.raises(ValidationError, match="overlapping clips on A4"):
        seed.model_validate(
            {
                **seed.model_dump(mode="json"),
                "clips": [
                    base.model_dump(mode="json"),
                    placed("a", voice.id, TrackId.A4, 0).model_dump(mode="json"),
                    placed("b", voice.id, TrackId.A4, 20).model_dump(mode="json"),
                ],
            }
        )


@pytest.mark.media
def test_voice_requests_link_only_to_generated_voice_files(tmp_path: Path, media_dir: Path) -> None:
    store = ProjectStore.create(tmp_path, "voice checklist")
    saved = store.save_asset_checklist(
        AssetChecklist(project_id=store.project_id, configured=True, requests=[_voice_request()]), 0
    )
    music = store.import_asset(media_dir / "music_bed_30s.m4a", AssetRole.MUSIC, GENERATED)
    with pytest.raises(EngineException, match="matching AI-generated"):
        store.link_asset_request("voice_01", music.id, saved.version)
    recorded = store.import_asset(_tone(tmp_path / "mine.wav", 1.0, 440), AssetRole.VOICE)
    with pytest.raises(EngineException, match="matching AI-generated"):
        store.link_asset_request("voice_01", recorded.id, saved.version)
    voice = store.import_asset(_tone(tmp_path / "voice_01.wav", 1.0, 440), AssetRole.VOICE, GENERATED)
    linked = store.link_asset_request("voice_01", voice.id, saved.version)
    assert linked.links == {"voice_01": voice.id}
    assert ProjectStore(store.root).asset_checklist().requests[0].line == "Three tips for sleep."


@pytest.mark.media
def test_voice_line_renders_into_the_mix_and_music_ducks_under_it(tmp_path: Path, media_dir: Path) -> None:
    store = ProjectStore.create(tmp_path, "voice render")
    primary = store.import_asset(media_dir / "talking_head_16x9_30fps_60s.mp4", AssetRole.PRIMARY)
    music = store.import_asset(media_dir / "music_bed_30s.m4a", AssetRole.MUSIC)
    voice = store.import_asset(_tone(tmp_path / "line.wav", 1.0, 2000), AssetRole.VOICE, GENERATED)
    seed = store.create_timeline(PROFILE, 90)
    # The speaker is turned right down so the comparison measures music and voice only.
    speaker = Clip(
        clip_id="speaker", asset_id=primary.id, track=TrackId.V1, gain_db=-60,
        source_in_frames=0, source_out_frames=90, timeline_in_frames=0,
    )  # fmt: skip
    bed = Clip(
        clip_id="bed", asset_id=music.id, track=TrackId.A2,
        source_in_frames=0, source_out_frames=90, timeline_in_frames=0,
    )  # fmt: skip
    line = Clip(
        # FFmpeg's sine source is 1/8 of full scale (about -21 dBFS RMS); +6 dB brings it to a speech level.
        clip_id="line", asset_id=voice.id, track=TrackId.A4, gain_db=6, rationale="intro line",
        source_in_frames=0, source_out_frames=30, timeline_in_frames=30,
    )  # fmt: skip
    raw = AudioMix(normalize=False, duck_music=False)
    plain = store.commit_timeline(seed.model_copy(update={"clips": [speaker, bed], "audio": raw}), 0)
    voiced = store.commit_timeline(plain.model_copy(update={"clips": [speaker, bed, line]}), plain.revision)
    ducked = store.commit_timeline(
        voiced.model_copy(update={"audio": AudioMix(normalize=False, duck_music=True)}), voiced.revision
    )
    assert [i for i in store.validation_issues(ducked) if i.severity == "error"] == []
    outputs = {}
    for name, timeline in (("plain", plain), ("voiced", voiced), ("ducked", ducked)):
        outputs[name] = store.safe_path(f"renders/{name}.mp4")
        render_timeline(store, timeline, outputs[name])

    # The voice line is audible only where it is placed.
    assert _mean_volume(outputs["voiced"], 1.2, 0.6) > _mean_volume(outputs["plain"], 1.2, 0.6) + 3
    assert abs(_mean_volume(outputs["voiced"], 0.1, 0.6) - _mean_volume(outputs["plain"], 0.1, 0.6)) < 0.5

    # With ducking on, the music alone is pulled down under the line and left alone before it.
    def music(name: str, start: float, duration: float) -> float:
        return _mean_volume(outputs[name], start, duration, music_only=True)

    # Default ducking (-30 dB threshold, ratio 8) under a speech-level line.
    assert music("ducked", 1.4, 0.5) < music("voiced", 1.4, 0.5) - 10
    assert abs(music("ducked", 0.4, 0.5) - music("voiced", 0.4, 0.5)) < 0.5

    # The generated voice file is listed as AI-generated (external) in the QA report.
    from ageneditor.media.qa import run_qa

    report = run_qa(store, "job_voice", ducked.revision, outputs["ducked"])
    assert [item.asset_id for item in report.generated_assets] == [voice.id]


@pytest.mark.media
def test_editorial_flag_warns_when_a_voice_line_plays_over_the_speaker(
    tmp_path: Path, media_dir: Path
) -> None:
    store = ProjectStore.create(tmp_path, "voice editorial")
    primary = store.import_asset(media_dir / "talking_head_16x9_30fps_60s.mp4", AssetRole.PRIMARY)
    voice = store.import_asset(_tone(tmp_path / "line.wav", 1.0, 440), AssetRole.VOICE, GENERATED)
    seed = store.create_timeline(PROFILE, 90)
    line = Clip(
        clip_id="line", asset_id=voice.id, track=TrackId.A4,
        source_in_frames=0, source_out_frames=30, timeline_in_frames=30,
    )  # fmt: skip

    def check(gain_db: float):
        speaker = Clip(
            clip_id="speaker", asset_id=primary.id, track=TrackId.V1, gain_db=gain_db,
            source_in_frames=0, source_out_frames=90, timeline_in_frames=0,
        )  # fmt: skip
        timeline = seed.model_copy(update={"clips": [speaker, line]})
        return next(f for f in editorial_flags(store, timeline) if f.check_id == "voice_over_speaker")

    loud = check(0)
    assert loud.status is CheckStatus.WARN
    assert "line over speaker for 1.0s" in loud.measured
    assert check(-30).status is CheckStatus.PASS
    # No voice lines, no flag.
    assert all(f.check_id != "voice_over_speaker" for f in editorial_flags(store, seed))
