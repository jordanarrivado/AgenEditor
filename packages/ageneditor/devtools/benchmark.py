"""Benchmark harness for the consented evaluation set (roadmap Phase 3 golden set / Phase 5 benchmark).

Runs each recording through import → (optional) local transcription → the human reference edit →
preview render → QA, and reports only what was measured: wall-clock timings, word error rate against a
human reference transcript, source-range validity, and how QA flags compare with annotated defects.
It never claims editorial quality; "flagged but not annotated" is a *candidate* false positive for a
human to confirm.

The dataset lives outside the repository (consented media stays private). Manifest format:
``docs/BENCHMARK.md``.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ageneditor.contracts import (
    AssetRole,
    Author,
    CheckStatus,
    Clip,
    EngineException,
    ErrorCode,
    JobStatus,
    ModelPreset,
    Provenance,
    RenderProfile,
    TrackId,
)
from ageneditor.contracts.common import Identifier
from ageneditor.timebase import FrameRate, seconds_to_frame_floor, seconds_to_frames_round

BENCH_PROFILE = RenderProfile(id="bench_preview_720", width=720, height=1280, frame_rate=FrameRate(num=30))


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RangeSeconds(_Model):
    start_s: Fraction
    end_s: Fraction

    @model_validator(mode="after")
    def _order(self) -> RangeSeconds:
        if self.end_s <= self.start_s or self.start_s < 0:
            raise ValueError("range end must follow a non-negative start")
        return self


class KnownDefect(_Model):
    #: QA ``check_id`` expected to flag this recording, e.g. ``jump_cuts`` or ``uncertain_caption_words``.
    check_id: Identifier
    note: str = Field(default="", max_length=500)


class BenchmarkRecord(_Model):
    id: Identifier
    media: str = Field(min_length=1)
    consent: bool
    reference_transcript: str | None = None
    accepted_ranges: list[RangeSeconds] = Field(min_length=1, max_length=20)
    known_defects: list[KnownDefect] = Field(default_factory=list)


class BenchmarkManifest(_Model):
    name: str = Field(min_length=1, max_length=200)
    records: list[BenchmarkRecord] = Field(min_length=1, max_length=200)


_WORD = re.compile(r"[\w']+")


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Levenshtein word distance / reference length, case- and punctuation-insensitive."""
    ref = _WORD.findall(reference.lower())
    hyp = _WORD.findall(hypothesis.lower())
    if not ref:
        return 0.0 if not hyp else 1.0
    previous = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, start=1):
        current = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, start=1):
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (r != h))
        previous = current
    return previous[-1] / len(ref)


def _resolve(manifest_path: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (manifest_path.parent / path)


def _transcribe(store, asset_id: str, preset: ModelPreset, timeout_s: int) -> tuple[str, float]:
    from ageneditor.transcription import TranscriptionManager

    started = time.perf_counter()
    job = TranscriptionManager(store).start(asset_id, preset)
    deadline = time.monotonic() + timeout_s
    while True:
        job = store.get_job(job.id)
        if job.is_terminal:
            break
        if time.monotonic() > deadline:
            raise EngineException(
                job.error.code if job.error else ErrorCode.TRANSCRIPTION_FAILED, "benchmark timeout"
            )
        time.sleep(0.5)
    if job.status is not JobStatus.SUCCEEDED or not job.output_path:
        message = job.error.message if job.error else job.status.value
        raise EngineException(job.error.code if job.error else ErrorCode.TRANSCRIPTION_FAILED, message)
    return Path(job.output_path).stem, time.perf_counter() - started


def run_record(
    record: BenchmarkRecord,
    manifest_path: Path,
    work: Path,
    transcribe: ModelPreset | None,
    timeout_s: int,
) -> dict:
    from ageneditor.captions import build_captions
    from ageneditor.media.qa import run_qa
    from ageneditor.media.render import render_timeline
    from ageneditor.storage import ProjectStore

    result: dict = {"id": record.id, "status": "ok", "timings_s": {}}
    if not record.consent:
        return {"id": record.id, "status": "skipped", "reason": "no recorded consent"}
    source = _resolve(manifest_path, record.media)
    store = ProjectStore.create(work, record.id)
    started = time.perf_counter()
    asset = store.import_asset(source, AssetRole.PRIMARY)
    result["timings_s"]["import"] = round(time.perf_counter() - started, 3)
    rate = asset.video.frame_rate.fraction
    source_seconds = Fraction(asset.video.duration_frames, 1) / rate
    result["source_seconds"] = float(source_seconds)

    transcript_id = None
    if transcribe is not None:
        transcript_id, elapsed = _transcribe(store, asset.id, transcribe, timeout_s)
        result["timings_s"]["transcribe"] = round(elapsed, 3)
        result["transcribe_realtime_factor"] = round(float(source_seconds) / elapsed, 2)
        transcript = store.transcript(transcript_id)
        result["transcript"] = {
            "words": len(transcript.words),
            "language": transcript.language,
            "compute": transcript.compute,
        }
        if record.reference_transcript:
            reference = _resolve(manifest_path, record.reference_transcript).read_text(encoding="utf-8")
            hypothesis = " ".join(word.text for word in transcript.words)
            result["word_error_rate"] = round(word_error_rate(reference, hypothesis), 4)

    timeline = store.create_timeline(BENCH_PROFILE, target_duration_frames=1)
    clips, position, invalid = [], 0, []
    for index, span in enumerate(record.accepted_ranges):
        source_in = seconds_to_frame_floor(span.start_s, rate)
        source_out = seconds_to_frames_round(span.end_s, rate)
        if source_out > asset.video.duration_frames or source_out <= source_in:
            invalid.append(index)
            continue
        out_frames = seconds_to_frames_round(
            Fraction(source_out - source_in, 1) / rate, BENCH_PROFILE.frame_rate
        )
        clips.append(
            Clip(
                clip_id=f"ref_{index + 1}",
                asset_id=asset.id,
                track=TrackId.V1,
                source_in_frames=source_in,
                source_out_frames=source_out,
                timeline_in_frames=position,
                timeline_duration_frames=out_frames,
                rationale="human reference edit",
            )
        )
        position += out_frames
    result["reference_ranges"] = {"total": len(record.accepted_ranges), "invalid_indexes": invalid}
    if not clips:
        result["status"] = "error"
        result["reason"] = "no accepted range lies inside the source"
        return result
    candidate = timeline.model_copy(
        update={
            "clips": clips,
            "target_duration_frames": position,
            "provenance": Provenance(author=Author.USER, tool="benchmark", summary="reference edit"),
        }
    )
    if transcript_id:
        candidate = candidate.model_copy(
            update={"captions": build_captions(store, candidate, [transcript_id])}
        )
    committed = store.commit_timeline(candidate, 0)
    output = store.safe_path("renders/benchmark/preview.mp4")
    started = time.perf_counter()
    render_timeline(store, committed, output)
    elapsed = time.perf_counter() - started
    output_seconds = Fraction(position, 1) / BENCH_PROFILE.frame_rate.fraction
    result["timings_s"]["render_preview"] = round(elapsed, 3)
    result["render_realtime_factor"] = round(float(output_seconds) / elapsed, 2)
    started = time.perf_counter()
    report = run_qa(store, "job_benchmark", committed.revision, output)
    result["timings_s"]["qa"] = round(time.perf_counter() - started, 3)
    flagged = sorted({c.check_id for c in report.checks if c.status in {CheckStatus.WARN, CheckStatus.FAIL}})
    annotated = sorted({d.check_id for d in record.known_defects})
    result["qa"] = {
        "overall": report.overall.value,
        "flagged": flagged,
        "annotated": annotated,
        "flagged_not_annotated": sorted(set(flagged) - set(annotated)),
        "annotated_not_flagged": sorted(set(annotated) - set(flagged)),
        "technical_failures": [c.check_id for c in report.checks if c.status is CheckStatus.FAIL],
    }
    return result


def run_benchmark(
    manifest_path: Path,
    out_dir: Path,
    transcribe: ModelPreset | None = None,
    timeout_s: int = 3600,
    keep_projects: bool = False,
) -> dict:
    from ageneditor.system import system_report

    manifest = BenchmarkManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "projects"
    work.mkdir(parents=True, exist_ok=True)
    results = []
    for record in manifest.records:
        try:
            results.append(run_record(record, manifest_path, work, transcribe, timeout_s))
        except EngineException as exc:
            results.append({"id": record.id, "status": "error", "error": exc.error.model_dump(mode="json")})
    if not keep_projects:
        shutil.rmtree(work, onexc=_clear_readonly_and_retry)
    done = [r for r in results if r["status"] == "ok"]
    summary = {
        "records": len(results),
        "completed": len(done),
        "mean_render_realtime_factor": _mean([r.get("render_realtime_factor") for r in done]),
        "mean_transcribe_realtime_factor": _mean([r.get("transcribe_realtime_factor") for r in done]),
        "mean_word_error_rate": _mean([r.get("word_error_rate") for r in done]),
        "flagged_not_annotated": sum(len(r["qa"]["flagged_not_annotated"]) for r in done),
        "annotated_not_flagged": sum(len(r["qa"]["annotated_not_flagged"]) for r in done),
    }
    report = {
        "dataset": manifest.name,
        "created_at": datetime.now(UTC).isoformat(),
        "transcription_preset": transcribe.value if transcribe else None,
        "system": system_report(out_dir).to_dict(),
        "summary": summary,
        "records": results,
    }
    (out_dir / "benchmark.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (out_dir / "benchmark.md").write_text(_markdown(report), encoding="utf-8")
    return report


def _clear_readonly_and_retry(function, path, _error) -> None:
    """Scratch projects hold read-only originals by design; make them deletable, then retry."""
    Path(path).chmod(0o600)
    function(path)


def _mean(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return round(sum(present) / len(present), 4) if present else None


def _markdown(report: dict) -> str:
    system = report["system"]
    ffmpeg = system.get("ffmpeg") or {}
    s = report["summary"]
    lines = [
        f"# Benchmark: {report['dataset']}",
        "",
        f"Run {report['created_at']} on {system['os']}, {system['cpu_logical']} logical CPUs, "
        f"{(system['memory_total_bytes'] or 0) / 2**30:.1f} GiB RAM, FFmpeg {ffmpeg.get('version', '?')}.",
        f"Transcription: {report['transcription_preset'] or 'not run'}.",
        "",
        "Measured on this machine only; not a speed claim for other hardware.",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Records completed | {s['completed']} / {s['records']} |",
        f"| Mean preview render speed (x real time) | {s['mean_render_realtime_factor']} |",
        f"| Mean transcription speed (x real time) | {s['mean_transcribe_realtime_factor']} |",
        f"| Mean word error rate | {s['mean_word_error_rate']} |",
        f"| QA flags not in annotations (candidate false positives) | {s['flagged_not_annotated']} |",
        f"| Annotated defects QA did not flag | {s['annotated_not_flagged']} |",
        "",
        "| Record | Status | Render x RT | WER | Flagged, not annotated | Annotated, not flagged |",
        "|---|---|---|---|---|---|",
    ]
    for r in report["records"]:
        qa = r.get("qa", {})
        lines.append(
            f"| {r['id']} | {r['status']} | {r.get('render_realtime_factor', '')} | "
            f"{r.get('word_error_rate', '')} | {', '.join(qa.get('flagged_not_annotated', []))} | "
            f"{', '.join(qa.get('annotated_not_flagged', []))} |"
        )
    return "\n".join(lines) + "\n"
