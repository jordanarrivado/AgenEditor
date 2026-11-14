# Timebase and timestamp conventions (ARCH-001)

Implemented in [`packages/ageneditor/timebase.py`](../packages/ageneditor/timebase.py); tested in
[`tests/engine/test_timebase.py`](../tests/engine/test_timebase.py) and against real files in
[`tests/engine/test_fixtures_probe.py`](../tests/engine/test_fixtures_probe.py).

| Rule | Why |
|---|---|
| Frame rates are exact rationals (`FrameRate(num, den)`), never floats. | `29.97` is really `30000/1001`; floats drift over long sources. |
| Timeline and clip positions are **integer frame indices**. Frame `i` covers `[i/fps, (i+1)/fps)`. | One unambiguous grid for UI, MCP and renderer. |
| Ranges are half-open `[in, out)`; duration = `out − in`. | Back-to-back cuts share a boundary with no overlap or gap. |
| Source times are normalised as `(pts − stream_start_pts) × time_base`, as an exact `Fraction`. | MP4 edit lists and container `start_time` offsets must not shift frame math. |
| Position → frame uses **floor** (the frame on screen at that instant). Duration → frames uses **round-half-even**. | Positions must be stable; durations must not be systematically short. |
| A clip's `source_in/out_frames` are on the **asset's own** frame grid (audio-only assets: the timeline grid). | Agents cite source frames exactly as the probe reports them. |
| **VFR** sources are mapped to the CFR output grid by sample-and-hold: output frame `k` shows the last source frame with normalised PTS `≤ k/fps`. | Phones record VFR; the output must be CFR. `cfr_frame_map` implements this. |
| Audio is sample-accurate. Frame boundaries convert to samples per boundary with round-half-even. | At 29.97 fps / 48 kHz a frame is 1601.6 samples; per-boundary rounding keeps error ≤ ½ sample with no accumulation. |
| Timecode strings (`HH:MM:SS:FF`) are **display only**, non-drop-frame, with the nominal rate rounded up. | Never parse timecode back into frame math. |

## Detecting VFR

`ffprobe` reports `r_frame_rate` (the lowest rate that can represent every timestamp) and `avg_frame_rate`.
When they differ the stream is treated as VFR (`VideoStream.is_vfr`). The fixture `vfr_phone_10s.mp4`
drops every other frame between 3 s and 5 s: nominal 30/1, average 27/1, 270 frames.

## Phase One render mapping

For CFR sources, a clip's source frame range converts to exact rational timestamps on that asset's
frame grid. For VFR sources, source frame indices address presented frames; packet PTS identifies the
start and the next frame PTS (or stream end) identifies the half-open end. FFmpeg trims the normalized
source interval and then applies the output `fps` filter with down rounding. A final held frame fills a
one-frame end shortfall before trimming to the declared output span.

`Clip.timeline_duration_frames` records the output-grid span when it differs from the source-frame
count. Validation calculates the required span from source timestamps and rejects mismatches. This
keeps timeline positions and the output frame count exact across 29.97 fps and VFR fixtures.

Rotation metadata is captured by FFprobe; a dedicated rotated-source fixture and acceptance check
remain to be added before treating rotated phone footage as verified.

## Phase Two transcript time

Faster-whisper's floating-point boundary values are converted once to integer microseconds with
decimal round-half-even. The transcript stores times on the registered video source clock, including
the probed audio-to-video start offset. Plan citations refer to word IDs plus half-open source frame
ranges; validation maps word boundaries to the asset's rational CFR grid or its observed VFR timestamps.

## Phase Three output time

* **Captions.** A transcript word belongs to the V1 clip whose exact source interval contains the word's
  midpoint. Its output position is `clip.timeline_in + floor((max(word_start, clip_start) − clip_start) × fps)`,
  and its end is rounded the same way, clamped to the clip. Cues never cross a cut. Cue text comes from the
  cited words plus versioned `word_corrections`; timing is never edited by a correction. SRT uses
  milliseconds rounded from exact rationals. ASS uses centiseconds, which is enough at 30 fps (33 ms frames).
* **B-roll (V2).** An overlay's stream is offset by `round(timeline_in × den / (num × TB))` ticks, an exact
  integer, and is shown only for output frames `[timeline_in, timeline_out)` (`enable=between(n,…)`).
  A float seconds/TB offset truncates to the previous tick and shows the overlay one frame early;
  `test_broll_overlay_is_frame_exact` guards this.
* **Still images (V2).** A PNG or JPEG has one source frame, always cited as `[0, 1)`.
  `timeline_duration_frames` is required and counts the output frames for which that frame is held.
  The image input loops at the exact output frame rate, then the filter trims it to that span.
* **Audio.** Each V1 audio segment is padded/trimmed to its clip's exact output span before concat, so the
  separate audio and video concats stay sample-aligned. A2 music is placed with
  `adelay=<samples>S`, using `frames_to_samples` (per-boundary round-half-even).
