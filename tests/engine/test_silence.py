"""Audio-level VAD candidates against the fixture's deliberate silences."""

from __future__ import annotations

from pathlib import Path

import pytest

from ageneditor.contracts import EngineException, ErrorCode
from ageneditor.media.silence import AudioSilence, detect_silence, parse_silencedetect


def test_parse_pairs_lines_and_closes_trailing_run() -> None:
    log = (
        b"[silencedetect @ 0x1] silence_start: 1.5\n"
        b"[silencedetect @ 0x1] silence_end: 2.25 | silence_duration: 0.75\n"
        b"[silencedetect @ 0x1] silence_end: 3 | silence_duration: 1\n"  # orphan end ignored
        b"[silencedetect @ 0x1] silence_start: 9.000001\n"
    )
    assert parse_silencedetect(log, 10_000_000) == [
        AudioSilence(1_500_000, 2_250_000),
        AudioSilence(9_000_001, 10_000_000),
    ]


def test_parse_clamps_negative_start_and_overrun() -> None:
    log = b"silence_start: -0.01\nsilence_end: 12.5 | silence_duration: 12.51\n"
    assert parse_silencedetect(log, 10_000_000) == [AudioSilence(0, 10_000_000)]


@pytest.mark.parametrize(
    "kwargs",
    [{"noise_floor_db": 0}, {"min_silence_us": 10}, {"audio_stream": -1}],
)
def test_rejects_out_of_range_parameters(tmp_path: Path, kwargs: dict) -> None:
    media = tmp_path / "x.wav"
    media.write_bytes(b"")
    with pytest.raises(EngineException) as info:
        detect_silence(media, 1_000_000, **kwargs)
    assert info.value.error.code is ErrorCode.INVALID_ARGUMENT


@pytest.mark.media
def test_detects_fixture_silences(media_dir: Path, manifest: dict) -> None:
    detected = detect_silence(media_dir / "talking_head_16x9_30fps_60s.mp4", 60_000_000)
    for start_s, end_s in [(20.0, 21.5), (44.0, 45.0)]:
        start_us, end_us = int(start_s * 1_000_000), int(end_s * 1_000_000)
        match = [s for s in detected if s.start_us < end_us and s.end_us > start_us]
        assert match, f"expected silence near {start_s}-{end_s}s, got {detected}"
        # Within ~1.5 audio frames of the authored mute boundaries.
        assert abs(match[0].start_us - start_us) <= 50_000
        assert abs(match[-1].end_us - end_us) <= 50_000
    assert all(s.end_us > s.start_us for s in detected)
