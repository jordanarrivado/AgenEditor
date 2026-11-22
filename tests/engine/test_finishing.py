"""Phase 6 finishing: more caption templates, speech repair and B-roll colour matching."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from ageneditor.captions import to_ass
from ageneditor.contracts import (
    AssetRole,
    AudioMix,
    CaptionCue,
    CaptionPreset,
    Clip,
    RenderProfile,
    Timeline,
    TrackId,
)
from ageneditor.contracts.timeline import Author, Provenance
from ageneditor.media.render import render_timeline
from ageneditor.media.tools import find_tool
from ageneditor.storage import ProjectStore
from ageneditor.timebase import FrameRate

PROFILE = RenderProfile(id="finish", width=180, height=320, frame_rate=FrameRate(num=30))


def _ffmpeg(*args: str) -> None:
    subprocess.run([str(find_tool("ffmpeg")), "-v", "error", "-y", *args], check=True, timeout=120)


def _mean_volume(path: Path, start: float, duration: float) -> float:
    result = subprocess.run(
        [str(find_tool("ffmpeg")), "-v", "info", "-ss", str(start), "-t", str(duration), "-i", str(path),
         "-af", "volumedetect", "-vn", "-f", "null", "-"],
        capture_output=True, text=True, timeout=120,
    )  # fmt: skip
    return float(re.search(r"mean_volume: (-?[\d.]+) dB", result.stderr).group(1))


def _luma(path: Path, seconds: float) -> float:
    result = subprocess.run(
        [str(find_tool("ffmpeg")), "-v", "info", "-ss", str(seconds), "-i", str(path), "-frames:v", "1",
         "-vf", "signalstats,metadata=mode=print", "-f", "null", "-"],
        capture_output=True, text=True, timeout=120,
    )  # fmt: skip
    return float(re.search(r"lavfi\.signalstats\.YAVG=([\d.]+)", result.stderr).group(1))


def test_caption_templates_place_text_where_they_say() -> None:
    cue = CaptionCue(
        cue_id="c",
        text="Hello",
        timeline_in_frames=0,
        timeline_out_frames=30,
        source_asset_id="a",
        corrected=True,
    )
    hd = RenderProfile(id="hd", width=1080, height=1920, frame_rate=FrameRate(num=30))
    base = Timeline(
        project_id="p", revision=1, target_duration_frames=30, render_profile=hd, captions=[cue],
        provenance=Provenance(author=Author.AGENT),
    )  # fmt: skip

    def style(preset: CaptionPreset) -> list[str]:
        line = next(
            x
            for x in to_ass(base.model_copy(update={"caption_preset": preset})).splitlines()
            if x.startswith("Style:")
        )
        return line.split(",")

    # Style fields: Fontsize(2), Bold(7), Outline(16), Alignment(18), MarginV(21).
    assert style(CaptionPreset.CLEAN_WHITE)[18] == "2"
    top = style(CaptionPreset.TOP_CLEAN)
    assert top[18] == "8" and int(top[21]) == round(1920 * 0.12)
    center = style(CaptionPreset.BIG_CENTER)
    assert (
        center[18] == "5"
        and center[7] == "-1"
        and int(center[16]) > int(style(CaptionPreset.CLEAN_WHITE)[16])
    )
    lower = style(CaptionPreset.LOWER_MINIMAL)
    assert lower[18] == "2" and int(lower[2]) < int(style(CaptionPreset.CLEAN_WHITE)[2])
    assert {p.value for p in CaptionPreset} >= {"top_clean", "big_center", "lower_minimal"}


@pytest.mark.media
def test_speech_repair_lowers_background_noise_and_keeps_the_voice(tmp_path: Path) -> None:
    source = tmp_path / "noisy.mp4"
    # 1 s of tone over hiss, then 1 s of hiss alone.
    _ffmpeg(
        "-f", "lavfi", "-i", "color=gray:size=180x320:rate=30:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1",
        "-f", "lavfi", "-i", "anoisesrc=color=white:amplitude=0.03:sample_rate=48000:duration=2",
        "-filter_complex",
        "[1]apad=whole_dur=2,volume=4[t];[t][2]amix=inputs=2:normalize=0,aformat=channel_layouts=stereo[a]",
        "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", str(source),
    )  # fmt: skip
    store = ProjectStore.create(tmp_path, "repair")
    primary = store.import_asset(source, AssetRole.PRIMARY)
    seed = store.create_timeline(PROFILE, 60)
    clip = Clip(clip_id="v", asset_id=primary.id, track=TrackId.V1, source_in_frames=0, source_out_frames=60,
                timeline_in_frames=0)  # fmt: skip
    outputs = {}
    revision = 0
    for repair in ("none", "strong"):
        mix = AudioMix(normalize=False, duck_music=False, edge_fade_ms=0, speech_repair=repair)
        committed = store.commit_timeline(seed.model_copy(update={"clips": [clip], "audio": mix}), revision)
        revision = committed.revision
        outputs[repair] = store.safe_path(f"renders/{repair}.mp4")
        render_timeline(store, committed, outputs[repair])
    # The hiss-only second gets much quieter; the tone stays within a few dB.
    assert _mean_volume(outputs["strong"], 1.3, 0.5) < _mean_volume(outputs["none"], 1.3, 0.5) - 6
    assert abs(_mean_volume(outputs["strong"], 0.3, 0.5) - _mean_volume(outputs["none"], 0.3, 0.5)) < 4


@pytest.mark.media
def test_broll_colour_match_moves_a_bright_shot_toward_dark_footage(tmp_path: Path) -> None:
    dark = tmp_path / "dark.mp4"
    bright = tmp_path / "bright.mp4"
    _ffmpeg("-f", "lavfi", "-i", "color=0x303030:size=180x320:rate=30:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=48000:duration=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(dark))  # fmt: skip
    _ffmpeg("-f", "lavfi", "-i", "color=0xD0D0D0:size=180x320:rate=30:duration=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(bright))  # fmt: skip
    store = ProjectStore.create(tmp_path, "colour")
    primary = store.import_asset(dark, AssetRole.PRIMARY)
    broll = store.import_asset(bright, AssetRole.BROLL)
    seed = store.create_timeline(PROFILE, 60)
    clips = [
        Clip(clip_id="v", asset_id=primary.id, track=TrackId.V1, source_in_frames=0, source_out_frames=60,
             timeline_in_frames=0),
        Clip(clip_id="b", asset_id=broll.id, track=TrackId.V2, source_in_frames=0, source_out_frames=30,
             timeline_in_frames=15),
    ]  # fmt: skip
    plain = store.commit_timeline(
        seed.model_copy(update={"clips": clips, "audio": AudioMix(normalize=False)}), 0
    )
    matched = store.commit_timeline(plain.model_copy(update={"broll_color_match": True}), plain.revision)
    out_plain, out_matched = store.safe_path("renders/plain.mp4"), store.safe_path("renders/matched.mp4")
    render_timeline(store, plain, out_plain)
    render_timeline(store, matched, out_matched)

    # Under the overlay: the bright shot is pulled down, but by a bounded amount (gain >= 0.8).
    plain_luma, matched_luma = _luma(out_plain, 1.0), _luma(out_matched, 1.0)
    assert matched_luma < plain_luma - 20
    assert matched_luma > plain_luma * 0.75
    # Outside the overlay nothing changes.
    assert abs(_luma(out_matched, 0.2) - _luma(out_plain, 0.2)) < 2
