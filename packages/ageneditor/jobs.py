"""Durable, single-render worker with persisted state and cooperative cancellation."""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from fractions import Fraction

from ageneditor.captions import to_srt
from ageneditor.contracts import (
    EncoderPreset,
    EngineError,
    EngineException,
    ErrorCode,
    Job,
    JobKind,
    JobStatus,
    RenderProfile,
)
from ageneditor.media.render import render_timeline
from ageneditor.storage import ProjectStore


def preview_profile(profile: RenderProfile) -> RenderProfile:
    """Same aspect and frame rate, at most 720x1280, fast preset: for quick review renders."""
    scale = min(Fraction(720, profile.width), Fraction(1280, profile.height), Fraction(1))
    width = max(16, (int(profile.width * scale) // 2) * 2)
    height = max(16, (int(profile.height * scale) // 2) * 2)
    return profile.model_copy(update={"width": width, "height": height, "preset": EncoderPreset.FAST})


class JobManager:
    def __init__(self, store: ProjectStore):
        self.store = store
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ageneditor-render")
        self._cancel: dict[str, threading.Event] = {}
        self.store.recover_interrupted_jobs()

    def start_render(self, revision: int, final: bool = True, variant_id: str | None = None) -> Job:
        """Render main revision ``revision``, or a variant computed against it."""
        variant_revision = None
        if variant_id is None:
            self.store.require_asset_requests_ready()
            timeline = self.store.timeline(revision)
        else:
            from ageneditor.variants import VariantService

            view = VariantService(self.store).view(variant_id, main_revision=revision)
            errors = [issue.message for issue in view.issues if issue.severity == "error"]
            if view.timeline is None or errors:
                raise EngineException(
                    ErrorCode.INVALID_TIMELINE,
                    f"variant does not fit main revision {revision}: " + "; ".join(errors[:5]),
                )
            self.store.require_asset_requests_ready(variant_id)
            timeline = view.timeline
            variant_revision = view.variant.revision
        if not final:
            timeline = timeline.model_copy(
                update={"render_profile": preview_profile(timeline.render_profile)}
            )
        kind = JobKind.FINAL_RENDER if final else JobKind.PREVIEW_RENDER
        job_id = f"job_{uuid.uuid4().hex[:20]}"
        output_path = f"renders/{job_id}/{'final' if final else 'preview'}.mp4"
        job = Job(
            id=job_id,
            project_id=self.store.project_id,
            kind=kind,
            timeline_revision=revision,
            output_path=output_path,
            variant_id=variant_id,
            variant_revision=variant_revision,
        )
        event = threading.Event()
        self._cancel[job.id] = event
        self.store.start_job(job)
        self._executor.submit(self._run, job, timeline, output_path, event)
        return job

    def _run(self, job: Job, timeline, output_path: str, cancel: threading.Event) -> None:
        if self.store.get_job(job.id).status is JobStatus.CANCELED:
            self._cancel.pop(job.id, None)
            return
        job = job.model_copy(update={"status": JobStatus.RUNNING, "started_at": datetime.now(UTC)})
        self.store.update_job(job)

        def update_progress(value: float) -> None:
            nonlocal job
            job = job.model_copy(update={"progress": value})
            self.store.update_job(job)

        try:
            output = self.store.safe_path(output_path)
            render_timeline(self.store, timeline, output, cancel=cancel, on_progress=update_progress)
            if cancel.is_set():
                raise EngineException(ErrorCode.JOB_CANCELED, "render was canceled")
            self.store.write_artifact(f"renders/{job.id}/timeline.json", timeline.model_dump_json(indent=2))
            if timeline.captions:
                self.store.write_artifact(f"renders/{job.id}/captions.srt", to_srt(timeline))
            job = job.model_copy(
                update={"status": JobStatus.SUCCEEDED, "progress": 1.0, "ended_at": datetime.now(UTC)}
            )
        except EngineException as exc:
            canceled = exc.error.code is ErrorCode.JOB_CANCELED
            job = job.model_copy(
                update={
                    "status": JobStatus.CANCELED if canceled else JobStatus.FAILED,
                    "ended_at": datetime.now(UTC),
                    "error": exc.error,
                }
            )
        except Exception as exc:  # Durable worker boundary: failures are data, not a false success.
            error = EngineError(
                code=ErrorCode.RENDER_FAILED, message=f"render worker failed: {type(exc).__name__}"
            )
            job = job.model_copy(
                update={"status": JobStatus.FAILED, "ended_at": datetime.now(UTC), "error": error}
            )
        finally:
            self.store.update_job(job)
            self._cancel.pop(job.id, None)

    def status(self, job_id: str) -> Job:
        return self.store.get_job(job_id)

    def cancel(self, job_id: str) -> Job:
        job = self.store.get_job(job_id)
        if job.status is JobStatus.QUEUED:
            event = self._cancel.get(job_id)
            if event:
                event.set()
            canceled = job.model_copy(update={"status": JobStatus.CANCELED, "ended_at": datetime.now(UTC)})
            self.store.update_job(canceled)
            return canceled
        if job.status is JobStatus.RUNNING:
            event = self._cancel.get(job_id)
            if event:
                event.set()
        return self.store.get_job(job_id)
