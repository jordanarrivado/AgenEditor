"""Local faster-whisper worker and durable one-at-a-time transcription jobs."""

from __future__ import annotations

import argparse
import json
import logging
import queue
import subprocess
import sys
import threading
import time
import types
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from fractions import Fraction
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from ageneditor.contracts import (
    AssetRole,
    EngineError,
    EngineException,
    ErrorCode,
    Job,
    JobKind,
    JobStatus,
    ModelPreset,
    SilenceCandidate,
    Transcript,
    TranscriptSegment,
    TranscriptWord,
)
from ageneditor.media.hashing import sha256_file
from ageneditor.media.probe import probe
from ageneditor.media.tools import find_tool
from ageneditor.storage import ProjectStore

TRANSCRIBE_TIMEOUT_S = 1800
#: Hidden engine CLI command that runs :func:`worker_main` (the packaged exe has no ``-m``).
WORKER_COMMAND = "transcribe-worker"
#: faster-whisper takes in-memory audio as 16 kHz mono float32.
WHISPER_SAMPLE_RATE = 16_000
#: Decoding even a multi-hour source is far quicker than transcribing it.
DECODE_TIMEOUT_S = 600


def decode_whisper_audio(path: Path, stream_index: int, channels: int):
    """Decode one audio stream with the bundled FFmpeg to 16 kHz mono float32 samples.

    This replaces faster-whisper's PyAV decoder, so the engine never loads PyAV's GPL
    libx264/libx265 DLLs in-process; FFmpeg stays a separate program. Channels are averaged
    (not FFmpeg's ``-ac 1`` downmix, which is 3 dB louder) to match PyAV's mono conversion.
    """
    import numpy as np

    args = [
        str(find_tool("ffmpeg")),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-map",
        f"0:{stream_index}",
        "-ac",
        str(channels),
        "-ar",
        str(WHISPER_SAMPLE_RATE),
        "-f",
        "f32le",
        "-",
    ]
    try:
        result = subprocess.run(args, capture_output=True, timeout=DECODE_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired as exc:
        raise EngineException(ErrorCode.TRANSCRIPTION_FAILED, "audio decode timed out") from exc
    if result.returncode != 0:
        logging.error("audio decode failed: %s", result.stderr.decode("utf-8", "replace")[-2000:])
        raise EngineException(ErrorCode.TRANSCRIPTION_FAILED, "could not decode the source audio")
    samples = np.frombuffer(result.stdout, dtype=np.float32)
    usable = len(samples) - len(samples) % channels
    return samples[:usable].reshape(-1, channels).mean(axis=1, dtype=np.float32)


def _import_whisper_model():
    """Import faster-whisper without PyAV.

    ``faster_whisper.audio`` imports ``av`` at module load but only uses it inside
    ``decode_audio``, which the engine never calls (audio arrives as samples). The packaged
    engine excludes PyAV, so an empty placeholder satisfies that import.
    """
    try:
        import av  # noqa: F401
    except ImportError:
        sys.modules["av"] = types.ModuleType("av")
    from faster_whisper import WhisperModel

    return WhisperModel


def _microseconds(value: float | Fraction) -> int:
    if isinstance(value, Fraction):
        return round(value * 1_000_000)
    return int((Decimal(str(value)) * 1_000_000).to_integral_value(rounding=ROUND_HALF_EVEN))


def _silence(words: list[TranscriptWord], duration_us: int) -> list[SilenceCandidate]:
    candidates: list[SilenceCandidate] = []
    previous_end, previous_id = 0, None
    for word in words:
        if word.start_us - previous_end >= 400_000:
            candidates.append(
                SilenceCandidate(
                    start_us=previous_end,
                    end_us=word.start_us,
                    before_word_id=previous_id,
                    after_word_id=word.id,
                )
            )
        previous_end, previous_id = max(previous_end, word.end_us), word.id
    if duration_us - previous_end >= 400_000:
        candidates.append(
            SilenceCandidate(start_us=previous_end, end_us=duration_us, before_word_id=previous_id)
        )
    return candidates


def transcribe_local(
    store: ProjectStore, asset_id: str, preset: ModelPreset, transcript_id: str
) -> Transcript:
    """Run an already cached local model. This function runs only in the child process."""
    asset = store.assets().get(asset_id)
    if asset is None:
        raise EngineException(ErrorCode.ASSET_NOT_FOUND, "asset does not exist")
    if asset.role is not AssetRole.PRIMARY or asset.video is None or asset.audio is None:
        raise EngineException(ErrorCode.INVALID_ARGUMENT, "transcription needs a primary video with audio")
    path = store.safe_path(asset.relative_path)
    if sha256_file(path) != asset.sha256:
        raise EngineException(ErrorCode.SOURCE_CHANGED, "registered source hash changed")
    try:
        WhisperModel = _import_whisper_model()
    except ImportError as exc:
        raise EngineException(
            ErrorCode.TRANSCRIPTION_FAILED, "install the transcribe extra: uv sync --extra transcribe"
        ) from exc
    from ageneditor.system import cuda_device_count, transcription_plan

    plan = transcription_plan(cuda_device_count())
    try:
        model_version = version("faster-whisper")
    except PackageNotFoundError:
        model_version = "unknown"
    media = probe(path)
    duration = (
        media.duration_seconds or Fraction(asset.video.duration_frames, 1) / asset.video.frame_rate.fraction
    )
    duration_us = _microseconds(duration)
    video_start = Fraction(asset.video.start_pts * asset.video.time_base_num, asset.video.time_base_den)
    audio_start = Fraction(asset.audio.start_pts * asset.audio.time_base_num, asset.audio.time_base_den)
    offset_us = _microseconds(audio_start - video_start)
    samples = decode_whisper_audio(path, asset.audio.index, asset.audio.channels)

    def decode(model) -> tuple[list[TranscriptWord], list[TranscriptSegment], object]:
        words: list[TranscriptWord] = []
        segments: list[TranscriptSegment] = []
        detected, info = model.transcribe(samples, word_timestamps=True, vad_filter=True, beam_size=5)
        for segment in detected:
            segment_words: list[TranscriptWord] = []
            segment_id = f"segment_{len(segments):06d}"
            for item in segment.words or []:
                text = item.word.strip()
                if not text or item.start is None or item.end is None:
                    continue
                start = max(0, _microseconds(item.start) + offset_us)
                end = min(duration_us, _microseconds(item.end) + offset_us)
                if end <= start:
                    continue
                segment_words.append(
                    TranscriptWord(
                        id=f"word_{len(words) + len(segment_words):07d}",
                        segment_id=segment_id,
                        text=text,
                        start_us=start,
                        end_us=end,
                        probability=item.probability,
                    )
                )
            if segment_words:
                words.extend(segment_words)
                segments.append(
                    TranscriptSegment(
                        id=segment_id,
                        start_us=segment_words[0].start_us,
                        end_us=max(w.end_us for w in segment_words),
                        text=segment.text.strip() or " ".join(w.text for w in segment_words),
                        word_ids=[w.id for w in segment_words],
                    )
                )
            progress = min(0.95, max(0.0, segment.end / max(info.duration, 0.001)))
            print(json.dumps({"progress": progress}), flush=True)
        return words, segments, info

    # Accelerated devices are opt-in and fail lazily (missing DLLs surface while decoding), so each
    # attempt covers model load *and* decode; CPU int8 is always the last attempt.
    compute = ""
    for attempt, (device, compute_type) in enumerate(plan):
        last = attempt == len(plan) - 1
        try:
            model = WhisperModel(
                preset.value, device=device, compute_type=compute_type, local_files_only=True
            )
        except Exception as exc:
            if not last:
                logging.warning("transcription device %s unavailable; falling back", device)
                continue
            raise EngineException(
                ErrorCode.TRANSCRIPTION_FAILED,
                "local faster-whisper model is unavailable; cache the selected model before transcribing",
            ) from exc
        try:
            words, segments, info = decode(model)
        except EngineException:
            raise
        except Exception as exc:
            if not last:
                logging.warning("transcription on %s failed; retrying on CPU", device, exc_info=True)
                continue
            logging.exception("local transcription failed")
            raise EngineException(ErrorCode.TRANSCRIPTION_FAILED, "local transcription failed") from exc
        compute = f"{device}/{compute_type}"
        break
    if sha256_file(path) != asset.sha256:
        raise EngineException(ErrorCode.SOURCE_CHANGED, "registered source changed during transcription")
    return Transcript(
        id=transcript_id,
        project_id=store.project_id,
        asset_id=asset.id,
        source_sha256=asset.sha256,
        model_preset=preset,
        model_version=model_version,
        compute=compute or None,
        language=(info.language or "unknown") if words else "unknown",
        language_probability=info.language_probability if words else None,
        duration_us=duration_us,
        words=words,
        segments=segments,
        silence_candidates=_silence(words, duration_us),
    )


def worker_command() -> list[str]:
    """How to start the worker process: the frozen engine exe cannot run ``python -m``."""
    if getattr(sys, "frozen", False):
        return [sys.executable, WORKER_COMMAND]
    return [sys.executable, "-m", "ageneditor.transcription"]


def worker_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--asset", required=True)
    parser.add_argument("--preset", choices=[x.value for x in ModelPreset], required=True)
    parser.add_argument("--transcript", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        store = ProjectStore(Path(args.project))
        transcript = transcribe_local(store, args.asset, ModelPreset(args.preset), args.transcript)
        store.write_artifact(args.output, transcript.model_dump_json())
        return 0
    except EngineException as exc:
        print(json.dumps({"error": exc.error.model_dump(mode="json")}), flush=True)
        return 2


class TranscriptionManager:
    def __init__(self, store: ProjectStore):
        self.store = store
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ageneditor-transcribe")
        self._cancel: dict[str, threading.Event] = {}

    def start(self, asset_id: str, preset: ModelPreset) -> Job:
        asset = self.store.assets().get(asset_id)
        if asset is None:
            raise EngineException(ErrorCode.ASSET_NOT_FOUND, "asset does not exist")
        if asset.role is not AssetRole.PRIMARY or asset.video is None or asset.audio is None:
            raise EngineException(ErrorCode.INVALID_ARGUMENT, "transcription needs primary video with audio")
        if sha256_file(self.store.safe_path(asset.relative_path)) != asset.sha256:
            raise EngineException(ErrorCode.SOURCE_CHANGED, "registered source hash changed")
        cached = self.store.cached_transcript(asset.id, asset.sha256, preset.value)
        try:
            installed_version = version("faster-whisper")
        except PackageNotFoundError:
            installed_version = None
        if cached and installed_version and cached.model_version != installed_version:
            cached = None
        job_id = f"job_{uuid.uuid4().hex[:20]}"
        transcript_id = cached.id if cached else f"transcript_{job_id[4:]}"
        output_path = f"transcripts/{transcript_id}.json"
        job = Job(
            id=job_id, project_id=self.store.project_id, kind=JobKind.TRANSCRIBE, output_path=output_path
        )
        if cached:
            job = job.model_copy(
                update={
                    "status": JobStatus.SUCCEEDED,
                    "progress": 1.0,
                    "started_at": datetime.now(UTC),
                    "ended_at": datetime.now(UTC),
                }
            )
            self.store.start_job(job)
            return job
        event = threading.Event()
        self._cancel[job_id] = event
        self.store.start_job(job)
        self._executor.submit(self._run, job, asset_id, preset, transcript_id, event)
        return job

    def _run(
        self, job: Job, asset_id: str, preset: ModelPreset, transcript_id: str, cancel: threading.Event
    ) -> None:
        if self.store.get_job(job.id).status is JobStatus.CANCELED:
            self._cancel.pop(job.id, None)
            return
        job = job.model_copy(update={"status": JobStatus.RUNNING, "started_at": datetime.now(UTC)})
        self.store.update_job(job)
        pending = f"transcripts/{transcript_id}.pending.json"
        lines: queue.Queue[str] = queue.Queue()
        process: subprocess.Popen[str] | None = None
        try:
            log_path = self.store.safe_path(f"reports/{job.id}.transcription.log")
            with log_path.open("w", encoding="utf-8") as log:
                process = subprocess.Popen(
                    [
                        *worker_command(),
                        "--project",
                        str(self.store.root),
                        "--asset",
                        asset_id,
                        "--preset",
                        preset.value,
                        "--transcript",
                        transcript_id,
                        "--output",
                        pending,
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=log,
                    text=True,
                    encoding="utf-8",
                    bufsize=1,
                )
                output_pipe = process.stdout
                if output_pipe is None:
                    raise RuntimeError("worker stdout pipe was not created")

                def read_stdout() -> None:
                    for line in output_pipe:
                        lines.put(line)

                reader = threading.Thread(target=read_stdout, daemon=True)
                reader.start()
                started = time.monotonic()
                error: EngineError | None = None
                while process.poll() is None or reader.is_alive() or not lines.empty():
                    if cancel.is_set():
                        raise EngineException(ErrorCode.JOB_CANCELED, "transcription was canceled")
                    if time.monotonic() - started > TRANSCRIBE_TIMEOUT_S:
                        raise EngineException(ErrorCode.TRANSCRIPTION_FAILED, "transcription timed out")
                    try:
                        line = lines.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    message = json.loads(line)
                    if "progress" in message:
                        job = job.model_copy(update={"progress": min(0.95, float(message["progress"]))})
                        self.store.update_job(job)
                    if "error" in message:
                        error = EngineError.model_validate(message["error"])
                reader.join(timeout=2)
                if cancel.is_set():
                    raise EngineException(ErrorCode.JOB_CANCELED, "transcription was canceled")
                if error:
                    raise EngineException(error.code, error.message, **error.details)
                if process.returncode != 0:
                    raise EngineException(
                        ErrorCode.TRANSCRIPTION_FAILED, "transcription worker exited with error"
                    )
            transcript = Transcript.model_validate_json(
                self.store.safe_path(pending).read_text(encoding="utf-8")
            )
            self.store.save_transcript(transcript)
            job = job.model_copy(
                update={"status": JobStatus.SUCCEEDED, "progress": 1.0, "ended_at": datetime.now(UTC)}
            )
        except EngineException as exc:
            job = job.model_copy(
                update={
                    "status": JobStatus.CANCELED
                    if exc.error.code is ErrorCode.JOB_CANCELED
                    else JobStatus.FAILED,
                    "ended_at": datetime.now(UTC),
                    "error": None if exc.error.code is ErrorCode.JOB_CANCELED else exc.error,
                }
            )
        except Exception as exc:
            job = job.model_copy(
                update={
                    "status": JobStatus.FAILED,
                    "ended_at": datetime.now(UTC),
                    "error": EngineError(
                        code=ErrorCode.TRANSCRIPTION_FAILED,
                        message=f"transcription worker failed: {type(exc).__name__}",
                    ),
                }
            )
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            self.store.safe_path(pending).unlink(missing_ok=True)
            self.store.update_job(job)
            self._cancel.pop(job.id, None)

    def cancel(self, job_id: str) -> Job:
        job = self.store.get_job(job_id)
        if job.kind is not JobKind.TRANSCRIBE:
            raise EngineException(ErrorCode.INVALID_ARGUMENT, "job is not a transcription")
        event = self._cancel.get(job_id)
        if job.status is JobStatus.QUEUED:
            if event:
                event.set()
            job = job.model_copy(update={"status": JobStatus.CANCELED, "ended_at": datetime.now(UTC)})
            self.store.update_job(job)
        elif job.status is JobStatus.RUNNING and event:
            event.set()
        return self.store.get_job(job_id)


def speech_models_cached() -> dict[str, bool]:
    """Which faster-whisper presets are already downloaded (checked offline; nothing is fetched).

    Raises ``TRANSCRIPTION_FAILED`` when the check itself is impossible, so a missing library is never
    reported as "not downloaded".
    """
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError as exc:
        raise EngineException(
            ErrorCode.TRANSCRIPTION_FAILED, "the speech model cache could not be checked"
        ) from exc
    status = {}
    for preset in ("tiny", "base", "small"):
        try:
            found = try_to_load_from_cache(f"Systran/faster-whisper-{preset}", "model.bin")
        except Exception:  # a broken cache entry reads as "not downloaded"
            found = None
        status[preset] = isinstance(found, str)
    return status


if __name__ == "__main__":
    raise SystemExit(worker_main())
