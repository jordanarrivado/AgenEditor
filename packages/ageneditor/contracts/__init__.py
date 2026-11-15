"""Versioned contracts shared by the engine, MCP adapter and desktop IPC adapter."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from .asset import (
    Asset,
    AssetKind,
    AssetOrigin,
    AssetProvenance,
    AssetRole,
    AudioStream,
    ShotLabel,
    VideoStream,
)
from .asset_checklist import AcceptedIssue, AssetChecklist, AssetRequest
from .brief import BriefItem, ProjectBrief
from .common import SCHEMA_VERSION, EngineError, EngineException, ErrorCode
from .edit_plan import EditPlan, PlanDisposition, PlanResult, PlanSegment
from .job import Job, JobKind, JobStatus
from .qa import CheckKind, CheckStatus, GeneratedAsset, QACheck, QAReport
from .render_profile import BUILTIN_PROFILES, REEL_FINAL, REEL_PREVIEW, EncoderPreset, RenderProfile
from .revision import (
    AddOverlay,
    BuildCaptions,
    CorrectWord,
    MoveClip,
    RemoveClip,
    RevisionOp,
    RevisionPatch,
    RevisionResult,
    SetAudioMix,
    SetCaptionPreset,
    SetClipAudio,
    SetColorPreset,
    SetTransform,
    TimelineDiff,
    TrimClip,
)
from .sound_library import LibrarySource, Sound, SoundCatalogEntry, SoundLibrary, SoundLicense, SoundPack
from .timeline import (
    AudioMix,
    Author,
    CaptionCue,
    CaptionPreset,
    Clip,
    ColorPreset,
    FitMode,
    Provenance,
    Timeline,
    TrackId,
    Transform,
    ValidationIssue,
    WordCorrection,
    validate_against_assets,
)
from .transcript import (
    HookCandidate,
    ModelPreset,
    SilenceCandidate,
    Transcript,
    TranscriptSegment,
    TranscriptWord,
)
from .transition import (
    TRANSITION_PRESETS,
    Transition,
    TransitionName,
    TransitionSettings,
    TransitionStyle,
)

#: Top-level contracts exported as JSON Schema (consumed by the UI and documented for MCP).
SCHEMA_MODELS: dict[str, type[BaseModel]] = {
    "asset": Asset,
    "asset_checklist": AssetChecklist,
    "project_brief": ProjectBrief,
    "render_profile": RenderProfile,
    "timeline": Timeline,
    "job": Job,
    "qa_report": QAReport,
    "engine_error": EngineError,
    "transcript": Transcript,
    "edit_plan": EditPlan,
    "plan_result": PlanResult,
    "revision_patch": RevisionPatch,
    "revision_result": RevisionResult,
    "sound_library": SoundLibrary,
    "sound_catalog_entry": SoundCatalogEntry,
    "transition_settings": TransitionSettings,
}


def export_json_schemas(out_dir: Path) -> list[Path]:
    """Write the current version of each contract's JSON Schema."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, model in SCHEMA_MODELS.items():
        schema = model.model_json_schema(mode="serialization")
        version = (
            model.model_fields["schema_version"].default
            if "schema_version" in model.model_fields
            else SCHEMA_VERSION
        )
        schema["$id"] = f"https://ageneditor.local/schemas/{name}.v{version}.json"
        path = out_dir / f"{name}.v{version}.schema.json"
        path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(path)
    return written


__all__ = [
    "BUILTIN_PROFILES",
    "REEL_FINAL",
    "REEL_PREVIEW",
    "SCHEMA_MODELS",
    "SCHEMA_VERSION",
    "TRANSITION_PRESETS",
    "AcceptedIssue",
    "AddOverlay",
    "Asset",
    "AssetChecklist",
    "AssetKind",
    "AssetOrigin",
    "AssetProvenance",
    "AssetRequest",
    "AssetRole",
    "AudioMix",
    "AudioStream",
    "Author",
    "BriefItem",
    "BuildCaptions",
    "CaptionCue",
    "CaptionPreset",
    "CheckKind",
    "CheckStatus",
    "Clip",
    "ColorPreset",
    "CorrectWord",
    "EditPlan",
    "EncoderPreset",
    "EngineError",
    "EngineException",
    "ErrorCode",
    "FitMode",
    "GeneratedAsset",
    "HookCandidate",
    "Job",
    "JobKind",
    "JobStatus",
    "LibrarySource",
    "ModelPreset",
    "MoveClip",
    "PlanDisposition",
    "PlanResult",
    "PlanSegment",
    "ProjectBrief",
    "Provenance",
    "QACheck",
    "QAReport",
    "RemoveClip",
    "RenderProfile",
    "RevisionOp",
    "RevisionPatch",
    "RevisionResult",
    "SetAudioMix",
    "SetCaptionPreset",
    "SetClipAudio",
    "SetColorPreset",
    "SetTransform",
    "ShotLabel",
    "SilenceCandidate",
    "Sound",
    "SoundCatalogEntry",
    "SoundLibrary",
    "SoundLicense",
    "SoundPack",
    "Timeline",
    "TimelineDiff",
    "TrackId",
    "Transcript",
    "TranscriptSegment",
    "TranscriptWord",
    "Transform",
    "Transition",
    "TransitionName",
    "TransitionSettings",
    "TransitionStyle",
    "TrimClip",
    "ValidationIssue",
    "VideoStream",
    "WordCorrection",
    "export_json_schemas",
    "validate_against_assets",
]
