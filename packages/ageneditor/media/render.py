"""Deterministic FFmpeg compiler for validated timelines: cuts, B-roll, captions and audio mix.

Every filter string is assembled here from typed, bounded contract values; nothing caller-supplied
is interpolated as filter syntax (caption text travels in a generated ASS file, escaped)."""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path
from queue import Empty, Queue

from ageneditor.captions import to_ass
from ageneditor.contracts import (
    Asset,
    AssetKind,
    ColorPreset,
    EngineException,
    ErrorCode,
    FitMode,
    Timeline,
    TrackId,
)
from ageneditor.maintenance import ensure_render_space
from ageneditor.media.hashing import sha256_file
from ageneditor.media.probe import probe
from ageneditor.media.ranges import source_interval
from ageneditor.media.tools import find_fonts_dir, find_tool
from ageneditor.storage import ProjectStore
from ageneditor.timebase import frames_to_samples

RENDER_TIMEOUT_S = 60 * 60


def _verified_source(store: ProjectStore, asset: Asset) -> Path:
    source = store.safe_path(asset.relative_path)
    try:
        unchanged = source.is_file() and sha256_file(source) == asset.sha256
    except OSError:
        unchanged = False
    if not unchanged:
        raise EngineException(
            ErrorCode.SOURCE_CHANGED, "registered source is missing or changed", asset_id=asset.id
        )
    return source


def _time(frames: int, fps: Fraction) -> str:
    return _seconds(Fraction(frames, 1) / fps)


def _seconds(value: Fraction) -> str:
    with localcontext() as context:
        context.prec = 30
        return f"{Decimal(value.numerator) / Decimal(value.denominator):.9f}"


#: Fixed, gentle grades. ``none`` applies no colour filter at all.
_COLOR_FILTERS: dict[ColorPreset, str] = {
    ColorPreset.NONE: "",
    ColorPreset.NATURAL: "eq=contrast=1.04:saturation=1.06",
    ColorPreset.WARM: "colorbalance=rm=0.04:bm=-0.04,eq=saturation=1.04",
    ColorPreset.COOL: "colorbalance=rm=-0.03:bm=0.04",
}


def _transition_graph(clips, transitions, pre: list[int], post: list[int], fps: Fraction) -> list[str]:
    """Join V1 clips into ``[basev]``: hard-cut runs are concatenated, runs are joined by ``xfade``.

    Each clip was extended by its transition handles, so every xfade overlaps exactly its own
    duration and the joined stream keeps the timeline's total length.
    """
    runs: list[list[int]] = [[0]]
    for index in range(1, len(clips)):
        if clips[index - 1].clip_id in transitions:
            runs.append([index])
        else:
            runs[-1].append(index)
    graph: list[str] = []
    labels: list[str] = []
    lengths: list[int] = []
    for number, members in enumerate(runs):
        lengths.append(sum(pre[i] + clips[i].timeline_span_frames + post[i] for i in members))
        if len(members) == 1:
            labels.append(f"v{members[0]}")
        else:
            inputs = "".join(f"[v{i}]" for i in members)
            graph.append(f"{inputs}concat=n={len(members)}:v=1:a=0[run{number}]")
            labels.append(f"run{number}")
    current, length = labels[0], lengths[0]
    for number in range(1, len(runs)):
        transition = transitions[clips[runs[number][0] - 1].clip_id]
        offset = length - transition.duration_frames
        output = "basev" if number == len(runs) - 1 else f"xf{number}"
        # The filter name comes from the fixed preset catalog, never from agent text.
        graph.append(
            f"[{current}][{labels[number]}]xfade=transition={transition.info.xfade}:"
            f"duration={_seconds(Fraction(transition.duration_frames) / fps)}:"
            f"offset={_seconds(Fraction(offset) / fps)}[{output}]"
        )
        current, length = output, offset + lengths[number]
    return graph


def _fit(clip, profile) -> str:
    """Scale/crop to the output frame. ``transform.scale`` > 1 is a punch-in around the crop centre."""
    scaled_width = round(profile.width * clip.transform.scale / 2) * 2
    scaled_height = round(profile.height * clip.transform.scale / 2) * 2
    cx, cy = clip.transform.center_x, clip.transform.center_y
    if clip.fit_mode is FitMode.COVER:
        fitted = (
            f"scale={scaled_width}:{scaled_height}:force_original_aspect_ratio=increase,"
            f"crop={profile.width}:{profile.height}:(iw-ow)*{cx}:(ih-oh)*{cy}"
        )
    else:
        fitted = (
            f"scale={scaled_width}:{scaled_height}:force_original_aspect_ratio=decrease,"
            f"pad={scaled_width}:{scaled_height}:(ow-iw)*{cx}:(oh-ih)*{cy},"
            f"crop={profile.width}:{profile.height}:(iw-ow)*{cx}:(ih-oh)*{cy}"
        )
    return fitted + ",setsar=1"


_BASE_ARGS = ("-hide_banner", "-v", "error", "-nostdin", "-y", "-stats_period", "0.5", "-progress", "pipe:1")
_LOUDNORM_JSON = re.compile(rb"\{[^{}]*\"input_i\"[^{}]*\}", re.S)


def _measure_loudness(
    input_args: list[str], audio: list[str], mixed: str, target: str, timeline: Timeline, timeout: int
) -> str | None:
    """First loudnorm pass over the exact audio mix; returns linear-mode parameters for the second pass.

    Returns ``None`` when the mix is digital silence (for example footage with no audio track and no music).
    Silence has no loudness to normalize, and loudnorm turns it into NaN that the AAC encoder rejects, so the
    caller must skip normalization. Returns ``""`` (single-pass dynamic mode) when the result cannot be
    parsed.
    """
    fps = timeline.render_profile.frame_rate.fraction
    args = [
        str(find_tool("ffmpeg")),
        "-hide_banner",
        "-nostdin",
        "-nostats",
        *input_args,
        "-filter_complex",
        ";".join([*audio, f"[{mixed}]loudnorm={target}:print_format=json[measure]"]),
        "-map",
        "[measure]",
        "-t",
        _time(timeline.duration_frames, fps),
        "-f",
        "null",
        "-",
    ]
    try:
        proc = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise EngineException(ErrorCode.RENDER_FAILED, "loudness measurement timed out") from exc
    if proc.returncode != 0:
        raise EngineException(ErrorCode.RENDER_FAILED, proc.stderr.decode("utf-8", "replace")[-1500:])
    match = _LOUDNORM_JSON.search(proc.stderr)
    if match is None:
        raise EngineException(ErrorCode.RENDER_FAILED, "loudness measurement produced no result")
    values = json.loads(match.group(0))
    keys = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    try:
        numbers = [float(values[key]) for key in keys]
    except (KeyError, ValueError):
        return ""
    if not all(math.isfinite(number) for number in numbers):
        return None
    measured_i, measured_tp, measured_lra, measured_thresh, offset = numbers
    return (
        f":measured_I={measured_i:.2f}:measured_TP={measured_tp:.2f}:measured_LRA={measured_lra:.2f}"
        f":measured_thresh={measured_thresh:.2f}:offset={offset:.2f}:linear=true"
    )


def render_timeline(
    store: ProjectStore,
    timeline: Timeline,
    output: Path,
    timeout: int = RENDER_TIMEOUT_S,
    cancel: threading.Event | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> dict[str, str | int]:
    diagnostics: dict = {"started": time.monotonic()}
    outcome = "succeeded"
    try:
        return _render_timeline(store, timeline, output, timeout, cancel, on_progress, diagnostics)
    except EngineException as exc:
        outcome = f"{exc.error.code.value}: {exc.error.message[:300]}"
        raise
    except Exception as exc:
        outcome = f"internal error: {type(exc).__name__}"
        raise
    finally:
        (output.parent / f".captions.r{timeline.revision}.ass").unlink(missing_ok=True)
        shutil.rmtree(output.parent / f".captionfonts.r{timeline.revision}", ignore_errors=True)
        _write_render_log(store, timeline, output, diagnostics, outcome)


def _write_render_log(
    store: ProjectStore, timeline: Timeline, output: Path, diagnostics: dict, outcome: str
) -> None:
    """Best-effort ``render.log`` next to the output: exact command, exit status and FFmpeg stderr.

    Project paths are shown as ``<project>`` so logs can be shared in bug reports.
    """
    root = str(store.root)

    def redact(text: str) -> str:
        return text.replace(root, "<project>")

    process = diagnostics.get("process")
    chunks = diagnostics.get("stderr") or []
    lines = [
        f"revision: {timeline.revision}",
        f"profile: {timeline.render_profile.width}x{timeline.render_profile.height} "
        f"@ {timeline.render_profile.frame_rate.num}/{timeline.render_profile.frame_rate.den}",
        f"outcome: {redact(outcome)}",
        f"exit_code: {process.returncode if process is not None else 'not started'}",
        f"elapsed_s: {time.monotonic() - diagnostics['started']:.2f}",
        "command:",
        *(f"  {redact(arg)}" for arg in diagnostics.get("args", [])),
        "ffmpeg_stderr_tail:",
        redact(b"".join(chunks).decode("utf-8", "replace")) or "  (empty)",
    ]
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        (output.parent / "render.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass  # Diagnostics must never mask the render result.


def _render_timeline(
    store: ProjectStore,
    timeline: Timeline,
    output: Path,
    timeout: int,
    cancel: threading.Event | None,
    on_progress: Callable[[float], None] | None,
    diagnostics: dict,
) -> dict[str, str | int]:
    assets = store.assets()
    issues = store.validation_issues(timeline)
    if any(issue.severity == "error" for issue in issues):
        raise EngineException(ErrorCode.INVALID_TIMELINE, "timeline is invalid for rendering")
    clips = sorted(
        (clip for clip in timeline.clips if clip.track is TrackId.V1),
        key=lambda clip: clip.timeline_in_frames,
    )
    if not clips:
        raise EngineException(ErrorCode.INVALID_TIMELINE, "timeline has no V1 video clips")
    ensure_render_space(output.parent, timeline)
    profile = timeline.render_profile
    fps = profile.frame_rate.fraction
    fps_text = f"{fps.numerator}/{fps.denominator}"
    sample_rate = profile.audio_sample_rate
    broll = sorted(
        (clip for clip in timeline.clips if clip.track is TrackId.V2), key=lambda c: c.timeline_in_frames
    )
    dialogue_overlays = [clip for clip in timeline.clips if clip.track is TrackId.A1]
    music = [clip for clip in timeline.clips if clip.track is TrackId.A2]
    effects = sorted(
        (clip for clip in timeline.clips if clip.track is TrackId.A3), key=lambda c: c.timeline_in_frames
    )
    mix = timeline.audio
    args = [str(find_tool("ffmpeg")), *_BASE_ARGS]
    inputs = [*clips, *broll, *dialogue_overlays, *music, *effects]
    for clip in inputs:
        asset = assets[clip.asset_id]
        source = _verified_source(store, asset)
        if asset.kind is AssetKind.IMAGE:
            args.extend(["-loop", "1", "-framerate", fps_text])
        args.extend(["-i", str(source)])
    graph: list[str] = []  # video
    audio: list[str] = []

    def video_chain(index: int, clip, asset, pre: int = 0, post: int = 0) -> str:
        """``pre``/``post``: extra output frames before/after the clip for a transition. Real source
        handles are used when the asset has them; otherwise the first/last frame is held."""
        start, end = source_interval(asset, clip, store.safe_path(asset.relative_path), fps)
        held = 0
        if pre:
            available = min(pre, math.floor(start * fps))
            start -= Fraction(available) / fps
            held = pre - available
        if post:
            end += Fraction(post) / fps  # past the source's end, tpad below holds the last frame
        lead = f"tpad=start={held}:start_mode=clone," if held else ""
        return (
            f"[{index}:{asset.video.index}]setpts=PTS-STARTPTS,"
            f"trim=start={_seconds(start)}:end={_seconds(end)},"
            f"setpts=PTS-STARTPTS,fps={fps_text}:round=down,{lead}tpad=stop={post + 1}:stop_mode=clone,"
            f"trim=end_frame={pre + clip.timeline_span_frames + post},"
            f"setpts=PTS-STARTPTS,{_fit(clip, profile)}"
        )

    def audio_fades(clip, duration: Fraction, edge: Fraction) -> str:
        fade_in = max(edge, Fraction(clip.fade_in_frames, 1) / fps)
        fade_out = max(edge, Fraction(clip.fade_out_frames, 1) / fps)
        if fade_in + fade_out >= duration:
            return ""
        parts = []
        if fade_in > 0:
            parts.append(f"afade=t=in:st=0:d={_seconds(fade_in)}")
        if fade_out > 0:
            parts.append(f"afade=t=out:st={_seconds(duration - fade_out)}:d={_seconds(fade_out)}")
        return "".join(f",{part}" for part in parts)

    edge = Fraction(mix.edge_fade_ms, 1000)
    transitions = {transition.after_clip_id: transition for transition in timeline.transitions}
    pre = [0] * len(clips)
    post = [0] * len(clips)
    for index, clip in enumerate(clips[:-1]):
        if transition := transitions.get(clip.clip_id):
            post[index] = transition.outgoing_frames
            pre[index + 1] = transition.incoming_frames
    for index, clip in enumerate(clips):
        asset = assets[clip.asset_id]
        chain = video_chain(index, clip, asset, pre[index], post[index])
        if clip.fade_in_frames:
            chain += f",fade=t=in:s={pre[index]}:n={clip.fade_in_frames}"
        if clip.fade_out_frames:
            fade_start = pre[index] + clip.timeline_span_frames - clip.fade_out_frames
            chain += f",fade=t=out:s={fade_start}:n={clip.fade_out_frames}"
        graph.append(f"{chain}[v{index}]")
        span = Fraction(clip.timeline_span_frames, 1) / fps
        if asset.audio:
            start, end = source_interval(asset, clip, store.safe_path(asset.relative_path), fps)
            audio.append(
                f"[{index}:{asset.audio.index}]atrim=start={_seconds(start)}:end={_seconds(end)},"
                f"asetpts=PTS-STARTPTS,aresample={sample_rate},aformat=channel_layouts=stereo,"
                f"apad=whole_dur={_seconds(span)},atrim=duration={_seconds(span)},"
                f"volume={clip.gain_db}dB{audio_fades(clip, span, edge)}[a{index}]"
            )
        else:
            audio.append(f"anullsrc=r={sample_rate}:cl=stereo,atrim=duration={_seconds(span)}[a{index}]")
    # Each audio segment is padded/trimmed to its clip's exact span, so separate concats stay in sync.
    # Audio keeps hard cuts (with edge fades) under transitions, so speech timing never moves.
    if transitions:
        graph.extend(_transition_graph(clips, transitions, pre, post, fps))
    else:
        graph.append(f"{''.join(f'[v{i}]' for i in range(len(clips)))}concat=n={len(clips)}:v=1:a=0[basev]")
    audio.append(f"{''.join(f'[a{i}]' for i in range(len(clips)))}concat=n={len(clips)}:v=0:a=1[dialogue0]")

    # V2: supplied B-roll replaces the picture only; the speaker's audio is never replaced.
    video_label = "basev"
    offset = len(clips)
    for n, clip in enumerate(broll):
        asset = assets[clip.asset_id]
        chain = f"{video_chain(offset + n, clip, asset)},format=yuva420p"
        if clip.fade_in_frames:
            chain += f",fade=t=in:s=0:n={clip.fade_in_frames}:alpha=1"
        if clip.fade_out_frames:
            chain += (
                f",fade=t=out:s={clip.timeline_span_frames - clip.fade_out_frames}"
                f":n={clip.fade_out_frames}:alpha=1"
            )
        # Exact rational offset rounded to whole ticks: a float seconds/TB offset truncates to the
        # previous tick and makes the overlay show B-roll one frame early.
        offset_num = clip.timeline_in_frames * fps.denominator
        chain += f",setpts=PTS+round({offset_num}/({fps.numerator}*TB))[b{n}]"
        graph.append(chain)
        graph.append(
            f"[{video_label}][b{n}]overlay=eof_action=pass:format=auto:"
            f"enable='between(n,{clip.timeline_in_frames},{clip.timeline_out_frames - 1})'[ov{n}]"
        )
        video_label = f"ov{n}"
    offset += len(broll)

    color = _COLOR_FILTERS[timeline.color_preset]
    caption_file: Path | None = None
    if timeline.captions:
        output.parent.mkdir(parents=True, exist_ok=True)
        caption_file = output.parent / f".captions.r{timeline.revision}.ass"
        caption_file.write_text(to_ass(timeline), encoding="utf-8")
        # Render with the bundled font only; never fall back to whatever the system has.
        fonts = output.parent / f".captionfonts.r{timeline.revision}"
        fonts.mkdir(exist_ok=True)
        for font in find_fonts_dir().glob("*.ttf"):
            shutil.copy2(font, fonts / font.name)
    finishing = [
        f
        for f in (color, f"ass=filename={caption_file.name}:fontsdir={fonts.name}" if caption_file else "")
        if f
    ]
    graph.append(f"[{video_label}]{','.join(finishing) or 'null'},format={profile.pixel_format}[outv]")

    def placed_audio(input_index: int, clip, asset, label: str) -> None:
        start, end = source_interval(asset, clip, store.safe_path(asset.relative_path), fps)
        delay = frames_to_samples(clip.timeline_in_frames, fps, sample_rate)
        audio.append(
            f"[{input_index}:{asset.audio.index}]atrim=start={_seconds(start)}:end={_seconds(end)},"
            f"asetpts=PTS-STARTPTS,aresample={sample_rate},aformat=channel_layouts=stereo,"
            f"volume={clip.gain_db}dB{audio_fades(clip, end - start, Fraction(0))},"
            f"adelay=delays={delay}S:all=1[{label}]"
        )

    dialogue = "dialogue0"
    if dialogue_overlays:
        labels = ["[dialogue0]"]
        for n, clip in enumerate(dialogue_overlays):
            placed_audio(offset + n, clip, assets[clip.asset_id], f"d{n}")
            labels.append(f"[d{n}]")
        audio.append(
            f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=first:dropout_transition=0[dialogue]"
        )
        dialogue = "dialogue"
    offset += len(dialogue_overlays)

    mixed = dialogue
    if music:
        for n, clip in enumerate(music):
            placed_audio(offset + n, clip, assets[clip.asset_id], f"m{n}")
        if len(music) > 1:
            audio.append(
                f"{''.join(f'[m{n}]' for n in range(len(music)))}"
                f"amix=inputs={len(music)}:normalize=0:duration=longest:dropout_transition=0[music]"
            )
        else:
            audio.append("[m0]anull[music]")
        bed = "music"
        if mix.duck_music:
            threshold = 10 ** (mix.duck_threshold_db / 20)
            audio.append(f"[{dialogue}]asplit=2[dmain][dkey]")
            audio.append(
                f"[music][dkey]sidechaincompress=threshold={threshold:.6f}:ratio={mix.duck_ratio}:"
                f"attack={mix.duck_attack_ms}:release={mix.duck_release_ms}[ducked]"
            )
            dialogue, bed = "dmain", "ducked"
        audio.append(
            f"[{dialogue}][{bed}]amix=inputs=2:normalize=0:duration=first:dropout_transition=0[mixed]"
        )
        mixed = "mixed"
    offset += len(music)
    if effects:
        # Sound effects sit on top of the mix and are never ducked; each keeps its own gain.
        for n, clip in enumerate(effects):
            placed_audio(offset + n, clip, assets[clip.asset_id], f"s{n}")
        labels = f"[{mixed}]" + "".join(f"[s{n}]" for n in range(len(effects)))
        audio.append(
            f"{labels}amix=inputs={len(effects) + 1}:normalize=0:duration=first:dropout_transition=0[withsfx]"
        )
        mixed = "withsfx"
    if mix.normalize:
        target = f"I={profile.loudness_lufs}:TP={profile.true_peak_dbtp}:LRA=11"
        measured = _measure_loudness(args[1 + len(_BASE_ARGS) :], audio, mixed, target, timeline, timeout)
        if cancel and cancel.is_set():
            raise EngineException(ErrorCode.JOB_CANCELED, "render was canceled")
        if measured is None:
            audio.append(f"[{mixed}]aresample={sample_rate}[outa]")
        else:
            audio.append(f"[{mixed}]loudnorm={target}{measured},aresample={sample_rate}[outa]")
    else:
        audio.append(f"[{mixed}]anull[outa]")
    graph.extend(audio)
    preset = {"fast": "veryfast", "balanced": "medium", "quality": "slow"}[profile.preset.value]
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(f".{output.stem}.{timeline.revision}.tmp.mp4")
    args.extend(
        [
            "-filter_complex",
            ";".join(graph),
            "-map",
            "[outv]",
            "-map",
            "[outa]",
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-pix_fmt",
            profile.pixel_format,
            "-r",
            f"{fps.numerator}/{fps.denominator}",
            "-c:a",
            "aac",
            "-ar",
            str(profile.audio_sample_rate),
            "-ac",
            str(profile.audio_channels),
            "-movflags",
            "+faststart",
            "-t",
            _time(timeline.duration_frames, fps),
            str(temp),
        ]
    )
    try:
        # The caption file is referenced by bare name, so FFmpeg runs in its directory; every
        # other path argument is absolute.
        diagnostics["args"] = args
        result = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=caption_file.parent if caption_file else None,
        )
        diagnostics["process"] = result
        started = time.monotonic()
        last_progress = 0.0
        expected_us = Fraction(timeline.duration_frames * fps.denominator * 1_000_000, fps.numerator)
        progress_lines: Queue[bytes] = Queue()
        stderr_chunks: deque[bytes] = deque(maxlen=100)
        diagnostics["stderr"] = stderr_chunks

        def read_progress() -> None:
            if result.stdout is None:
                return
            for line in result.stdout:
                if line.startswith(b"out_time_us="):
                    progress_lines.put(line)

        def read_errors() -> None:
            if result.stderr is None:
                return
            for line in result.stderr:
                stderr_chunks.append(line)

        progress_reader = threading.Thread(target=read_progress, daemon=True)
        error_reader = threading.Thread(target=read_errors, daemon=True)
        progress_reader.start()
        error_reader.start()

        def publish_progress(line: bytes) -> None:
            nonlocal last_progress
            if on_progress is None:
                return
            try:
                measured = min(0.99, float(Fraction(int(line.split(b"=", 1)[1].strip()), 1) / expected_us))
            except ValueError:
                return
            if measured > last_progress:
                on_progress(measured)
                last_progress = measured

        def stop_process() -> None:
            result.terminate()
            try:
                result.wait(timeout=5)
            except subprocess.TimeoutExpired:
                result.kill()
                result.wait()

        while result.poll() is None:
            try:
                publish_progress(progress_lines.get(timeout=0.2))
            except Empty:
                pass
            if cancel and cancel.is_set():
                stop_process()
                temp.unlink(missing_ok=True)
                raise EngineException(ErrorCode.JOB_CANCELED, "render was canceled")
            if time.monotonic() - started > timeout:
                stop_process()
                temp.unlink(missing_ok=True)
                raise EngineException(ErrorCode.RENDER_FAILED, "FFmpeg render timed out")
        progress_reader.join(timeout=2)
        error_reader.join(timeout=2)
        while not progress_lines.empty():
            publish_progress(progress_lines.get_nowait())
        stderr = b"".join(stderr_chunks)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        raise EngineException(ErrorCode.RENDER_FAILED, "could not start FFmpeg") from exc
    if result.returncode:
        temp.unlink(missing_ok=True)
        raise EngineException(ErrorCode.RENDER_FAILED, stderr.decode("utf-8", "replace")[-1500:])
    # Verify source hashes again before publishing the output atomically.
    for clip in inputs:
        asset = assets[clip.asset_id]
        try:
            _verified_source(store, asset)
        except EngineException:
            temp.unlink(missing_ok=True)
            raise
    try:
        metadata = probe(temp)
    except EngineException:
        temp.unlink(missing_ok=True)
        raise
    if metadata.video is None or metadata.audio is None or metadata.video.duration_frames == 0:
        temp.unlink(missing_ok=True)
        raise EngineException(ErrorCode.RENDER_FAILED, "FFmpeg output is missing required streams or frames")
    digest = sha256_file(temp)
    temp.replace(output)
    return {
        "sha256": digest,
        "size_bytes": metadata.size_bytes,
        "duration": str(metadata.duration_seconds or Fraction(0)),
    }
