from fractions import Fraction

import pytest

from ageneditor.timebase import (
    FrameRate,
    cfr_frame_map,
    format_timecode,
    frames_to_samples,
    frames_to_seconds,
    normalize_pts,
    rescale_frames,
    seconds_to_frame_floor,
    seconds_to_frames_round,
)

NTSC = FrameRate(num=30000, den=1001)


def test_parse_rates():
    assert FrameRate.parse("30000/1001") == NTSC
    assert FrameRate.parse("30") == FrameRate(num=30)
    with pytest.raises(ValueError):
        FrameRate.parse("0/1")


def test_exact_frame_seconds_roundtrip():
    for rate in (FrameRate(num=30), NTSC, FrameRate(num=24000, den=1001), FrameRate(num=60)):
        for f in (0, 1, 299, 1200, 107_892):
            assert seconds_to_frame_floor(frames_to_seconds(f, rate), rate) == f


def test_spec_example_is_forty_seconds():
    # Spec §5.2 example: 150 + 450 + 600 frames at 30 fps.
    total = (510 - 360) + (1800 - 1350) + (3360 - 2760)
    assert total == 1200
    assert frames_to_seconds(total, FrameRate(num=30)) == 40


def test_floor_for_positions_round_for_durations():
    rate = FrameRate(num=30)
    almost_next = Fraction(1, 30) - Fraction(1, 10**9)
    assert seconds_to_frame_floor(almost_next, rate) == 0
    assert seconds_to_frames_round(almost_next, rate) == 1


def test_normalize_pts_ignores_container_offset():
    # MP4 with an edit-list offset: first pts 1001 in a 1/30000 time base.
    tb = Fraction(1, 30000)
    assert normalize_pts(1001, tb, stream_start_pts=1001) == 0
    assert normalize_pts(1001 * 31, tb, stream_start_pts=1001) == Fraction(1001, 1000)
    assert seconds_to_frame_floor(normalize_pts(1001 * 31, tb, 1001), NTSC) == 30


def test_rescale_between_rates():
    # Frame 30 at 29.97 starts at 1.001 s → frame 30 at 30 fps (floor).
    assert rescale_frames(30, NTSC, FrameRate(num=30)) == 30
    assert rescale_frames(1000, NTSC, FrameRate(num=30)) == 1001


def test_audio_samples_do_not_drift_at_ntsc():
    # 1601.6 samples per frame: boundaries round per-boundary, never accumulate.
    samples = [frames_to_samples(f, NTSC, 48000) for f in range(0, 30001)]
    exact = [Fraction(f * 1001 * 48000, 30000) for f in range(0, 30001)]
    assert max(abs(s - e) for s, e in zip(samples, exact, strict=True)) <= Fraction(1, 2)
    assert frames_to_samples(30000, NTSC, 48000) == 48_048_000


def test_cfr_map_sample_and_hold():
    # Source: frames at 0, 1/30, 3/30 (one dropped), 4/30.
    src = [Fraction(0), Fraction(1, 30), Fraction(3, 30), Fraction(4, 30)]
    assert cfr_frame_map(src, FrameRate(num=30), 5) == [0, 1, 1, 2, 3]
    with pytest.raises(ValueError):
        cfr_frame_map([Fraction(1), Fraction(0)], FrameRate(num=30), 2)


def test_timecode_display():
    assert format_timecode(376, FrameRate(num=30)) == "00:00:12:16"
    assert format_timecode(1200, FrameRate(num=30)) == "00:00:40:00"
    assert format_timecode(30, NTSC) == "00:00:01:00"
