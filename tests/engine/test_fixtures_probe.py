"""Phase 0 exit gate: sample assets can be probed, and the probe agrees with the fixture manifest."""

from fractions import Fraction

import pytest

from ageneditor.contracts import EngineException, ErrorCode
from ageneditor.media.hashing import sha256_file
from ageneditor.media.probe import probe, video_frame_times
from ageneditor.timebase import FrameRate, cfr_frame_map

pytestmark = pytest.mark.media


def test_every_fixture_matches_manifest(media_dir, manifest):
    for spec in manifest["fixtures"]:
        r = probe(media_dir / spec["name"])
        assert r.duration_seconds is not None
        assert abs(float(r.duration_seconds) - spec["seconds"]) < 0.1, spec["name"]
        assert (r.video is not None) == spec["has_video"], spec["name"]
        assert (r.audio is not None) == spec["has_audio"], spec["name"]
        if r.video:
            assert (r.video.width, r.video.height) == (spec["width"], spec["height"])
            assert r.video.frame_rate == FrameRate.parse(spec["fps"])
            assert r.video.is_vfr == spec["vfr"], spec["name"]
            assert r.video.codec == "h264" and r.video.pixel_format == "yuv420p"
        if r.audio:
            assert r.audio.sample_rate == 48000 and r.audio.channels == 2
    for spec in manifest["still_images"]:
        still = probe(media_dir / spec["name"])
        assert still.video and still.video.codec == "png"
        assert (still.video.width, still.video.height) == (spec["width"], spec["height"])
        assert still.audio is None


def test_cfr_frame_counts(media_dir):
    r = probe(media_dir / "talking_head_16x9_30fps_60s.mp4")
    assert r.video and r.video.duration_frames == 1800
    ntsc = probe(media_dir / "talking_head_9x16_2997fps_20s.mp4")
    assert ntsc.video and ntsc.video.frame_rate == FrameRate(num=30000, den=1001)
    assert ntsc.video.duration_frames == 600


def test_vfr_maps_onto_cfr_grid(media_dir):
    path = media_dir / "vfr_phone_10s.mp4"
    times = video_frame_times(path)
    assert times[0] == 0
    assert len(times) == 270  # 300 frames minus 30 dropped between 3 s and 5 s
    mapping = cfr_frame_map(times, FrameRate(num=30), 300)
    # Outside the drop window the map is 1:1 …
    assert mapping[:90] == list(range(90))
    # … inside it, each kept source frame is held for two output frames.
    assert mapping[90] == mapping[91] and mapping[92] == mapping[93]
    assert mapping[-1] == 269
    assert all(Fraction(0) <= t < 10 for t in times)


def test_hashing_is_stable_and_probe_is_read_only(media_dir):
    path = media_dir / "broll_9x16_10s.mp4"
    before = sha256_file(path)
    probe(path)
    assert sha256_file(path) == before


def test_probe_errors_are_structured(tmp_path):
    with pytest.raises(EngineException) as e:
        probe(tmp_path / "missing.mp4")
    assert e.value.error.code is ErrorCode.ASSET_NOT_FOUND
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not a video")
    with pytest.raises(EngineException) as e:
        probe(junk)
    assert e.value.error.code is ErrorCode.PROBE_FAILED
