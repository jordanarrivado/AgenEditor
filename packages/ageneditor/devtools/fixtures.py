"""Synthetic reference media (roadmap Phase 0).

Every fixture is generated from FFmpeg's built-in sources, so it is free of
licensing and privacy concerns and fully reproducible from this file. Visuals
are coloured, slated sections with a burned-in frame counter so a reviewer (or a
future QA check) can tell exactly which source frames ended up in a render.
Audio is "speech-like": band-limited pink noise amplitude-modulated at a
syllable rate, with deliberate silences at known positions for VAD tests.

Expected properties are written to ``manifest.json`` and asserted by
``tests/engine/test_fixtures_probe.py``.

Usage: ``uv run ageneditor fixtures --out tests/fixtures/media``
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ageneditor.media.tools import find_tool

#: Section colours reuse the UI track palette so fixtures are recognisable in previews.
COLOURS = ["0x5E88D7", "0x5CABC8", "0x6BAE9B", "0xC5A56C", "0xF07C82", "0x84A9FF"]
FONT_CANDIDATES = [
    Path("C:/Windows/Fonts/consola.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
]
FONT_NAME = "fixture_font.ttf"
STILL_IMAGE_NAME = "still_storyboard_9x16.png"


@dataclass
class FixtureSpec:
    name: str
    description: str
    width: int | None
    height: int | None
    fps: str | None  # exact rational, e.g. "30000/1001"
    seconds: int
    has_video: bool
    has_audio: bool
    vfr: bool = False
    sections: int = 0
    silences: list[tuple[float, float]] = field(default_factory=list)


SPECS: list[FixtureSpec] = [
    FixtureSpec(
        name="talking_head_16x9_30fps_60s.mp4",
        description="Primary interview stand-in: six 10 s slated sections, speech-like audio, two silences.",
        width=1920,
        height=1080,
        fps="30/1",
        seconds=60,
        has_video=True,
        has_audio=True,
        sections=6,
        silences=[(20.0, 21.5), (44.0, 45.0)],
    ),
    FixtureSpec(
        name="talking_head_9x16_2997fps_20s.mp4",
        description="NTSC-rate vertical source (30000/1001) for rational timebase tests.",
        width=1080,
        height=1920,
        fps="30000/1001",
        seconds=20,
        has_video=True,
        has_audio=True,
        sections=2,
    ),
    FixtureSpec(
        name="vfr_phone_10s.mp4",
        description="Variable frame rate: every other frame dropped between 3 s and 5 s.",
        width=1280,
        height=720,
        fps="30/1",
        seconds=10,
        has_video=True,
        has_audio=True,
        vfr=True,
        sections=1,
    ),
    FixtureSpec(
        name="broll_9x16_10s.mp4",
        description="Supplied B-roll stand-in, video only.",
        width=1080,
        height=1920,
        fps="30/1",
        seconds=10,
        has_video=True,
        has_audio=False,
        sections=2,
    ),
    FixtureSpec(
        name="music_bed_30s.m4a",
        description="Supplied music stand-in: soft sustained chord, AAC 48 kHz stereo.",
        width=None,
        height=None,
        fps=None,
        seconds=30,
        has_video=False,
        has_audio=True,
    ),
]


def _font(out_dir: Path) -> str | None:
    for candidate in FONT_CANDIDATES:
        if candidate.is_file():
            shutil.copyfile(candidate, out_dir / FONT_NAME)
            return FONT_NAME
    return None


def _video_filter(spec: FixtureSpec, font: str | None) -> str:
    if not (spec.width and spec.height and spec.fps):
        raise ValueError(f"{spec.name}: video fixtures need width, height and fps")
    per = spec.seconds / max(spec.sections, 1)
    size = f"{spec.width}x{spec.height}"
    parts = []
    for i in range(max(spec.sections, 1)):
        parts.append(f"color=c={COLOURS[i % len(COLOURS)]}:s={size}:r={spec.fps}:d={per}[c{i}]")
    chain = "".join(f"[c{i}]" for i in range(max(spec.sections, 1)))
    graph = ";".join(parts) + f";{chain}concat=n={max(spec.sections, 1)}:v=1:a=0[bg]"
    last = "bg"
    if font:
        fs = spec.height // 14
        label = spec.name.split("_")[0].upper()
        graph += (
            f";[{last}]drawtext=fontfile={font}:fontsize={fs}:fontcolor=white:x=(w-tw)/2:y=h/3"
            f":text='{label} SEG %{{eif\\:floor(t/{per})+1\\:d}}'"
            f",drawtext=fontfile={font}:fontsize={fs // 2}:fontcolor=white:x=(w-tw)/2:y=h/3+{fs * 3 // 2}"
            f":text='frame %{{frame_num}}  t=%{{pts\\:hms}}'[txt]"
        )
        last = "txt"
    if spec.vfr:
        graph += f";[{last}]select='not(between(t\\,3\\,5)*mod(n\\,2))'[sel]"
        last = "sel"
    return graph + f";[{last}]format=yuv420p[v]"


def _speech_audio(spec: FixtureSpec) -> str:
    mute = "+".join(f"between(t,{a},{b})" for a, b in spec.silences) or "0"
    return (
        f"anoisesrc=color=pink:sample_rate=48000:amplitude=0.6:seed=7:d={spec.seconds},"
        "bandpass=f=900:width_type=h:w=1400,tremolo=f=4.5:d=0.85,"
        f"volume=volume=0:enable='{mute}',aformat=channel_layouts=stereo[a]"
    )


def _music_audio(spec: FixtureSpec) -> str:
    return (
        f"sine=f=220:sample_rate=48000:d={spec.seconds}[s1];"
        f"sine=f=277.18:sample_rate=48000:d={spec.seconds}[s2];"
        f"sine=f=329.63:sample_rate=48000:d={spec.seconds}[s3];"
        "[s1][s2][s3]amix=inputs=3,volume=0.3,afade=t=in:d=1,"
        f"afade=t=out:st={spec.seconds - 2}:d=2,aformat=channel_layouts=stereo[a]"
    )


def build_command(ffmpeg: str, spec: FixtureSpec, font: str | None) -> list[str]:
    graphs = []
    if spec.has_video:
        graphs.append(_video_filter(spec, font))
    if spec.has_audio:
        graphs.append(_speech_audio(spec) if spec.has_video else _music_audio(spec))
    args = [ffmpeg, "-v", "error", "-y", "-filter_complex", ";".join(graphs)]
    if spec.has_video:
        args += ["-map", "[v]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-g", "30"]
        args += ["-fps_mode", "vfr" if spec.vfr else "cfr"]
    if spec.has_audio:
        args += ["-map", "[a]", "-c:a", "aac", "-b:a", "96k", "-ar", "48000"]
    args += ["-t", str(spec.seconds), "-movflags", "+faststart", spec.name]
    return args


def generate(out_dir: Path, force: bool = False) -> Path:
    """Generate all fixtures into ``out_dir``; returns the manifest path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = str(find_tool("ffmpeg"))
    font = _font(out_dir)
    try:
        for spec in SPECS:
            if force or not (out_dir / spec.name).is_file():
                # cwd=out_dir keeps every filter path relative (no Windows drive-letter escaping).
                subprocess.run(build_command(ffmpeg, spec, font), cwd=out_dir, check=True, timeout=600)
        still = out_dir / STILL_IMAGE_NAME
        if force or not still.is_file():
            subprocess.run(
                [
                    ffmpeg,
                    "-v",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=0xF07C82:s=270x480",
                    "-vf",
                    "format=rgb24",
                    "-frames:v",
                    "1",
                    "-threads",
                    "1",
                    STILL_IMAGE_NAME,
                ],
                cwd=out_dir,
                check=True,
                timeout=60,
            )
    finally:
        (out_dir / FONT_NAME).unlink(missing_ok=True)
    manifest = out_dir / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "slated": font is not None,
                "fixtures": [asdict(s) for s in SPECS],
                "still_images": [{"name": STILL_IMAGE_NAME, "width": 270, "height": 480}],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return manifest
