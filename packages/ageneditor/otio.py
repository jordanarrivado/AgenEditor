"""OpenTimelineIO interchange (Phase 6): export a revision as ``.otio`` JSON and import one back.

Export writes the standard OTIO JSON schema directly (Timeline.1, Stack.1, Track.1, Clip.2, Gap.1,
Transition.1); there is no runtime dependency. Every track uses the timeline's frame rate so clip positions
are exact; media start times are converted from the source's own rate. Details OTIO has no standard field
for (asset ID and hash, gain, fades, crop, rationale, transition preset) go into ``metadata["ageneditor"]``.
Captions are not part of OTIO; export them as SRT.

Import accepts OTIO JSON whose media matches registered assets (by AgenEditor metadata, file hash or file
path) and builds a *candidate* timeline that goes through the normal validation. Nothing is committed unless
asked, and an import never registers new media or reads files other than the registered originals.

Interchange with Premiere Pro or DaVinci Resolve is not verified (spec §2.3). Tests also open exported
files with the reference ``opentimelineio`` library and import files authored by that library. It is an
interop test dependency, not a runtime dependency.
"""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from ageneditor.contracts import (
    Asset,
    AssetKind,
    AssetRole,
    Clip,
    EngineException,
    ErrorCode,
    Timeline,
    TrackId,
)
from ageneditor.contracts.timeline import Author, Provenance, ValidationIssue
from ageneditor.contracts.transition import Transition
from ageneditor.storage import ProjectStore

MAX_IMPORT_BYTES = 8_000_000
MAX_IMPORT_CLIPS = 500


def _rt(value: Fraction | int, rate: Fraction) -> dict[str, Any]:
    return {"OTIO_SCHEMA": "RationalTime.1", "rate": float(rate), "value": float(value)}


def _range(start: Fraction | int, duration: Fraction | int, rate: Fraction) -> dict[str, Any]:
    return {"OTIO_SCHEMA": "TimeRange.1", "start_time": _rt(start, rate), "duration": _rt(duration, rate)}


def _item(schema: str, name: str, **fields: Any) -> dict[str, Any]:
    return {
        "OTIO_SCHEMA": schema,
        "name": name,
        "metadata": fields.pop("metadata", {}),
        "source_range": fields.pop("source_range", None),
        "effects": [],
        "markers": [],
        "enabled": True,
        **fields,
    }


def _source_fps(asset: Asset, timeline_fps: Fraction) -> Fraction:
    # Audio-only assets and stills use the timeline grid (docs/TIMEBASE.md).
    if asset.kind is AssetKind.VIDEO and asset.video is not None:
        return asset.video.frame_rate.fraction
    return timeline_fps


def _asset_length(asset: Asset, timeline_fps: Fraction) -> Fraction:
    """Asset duration in timeline frames (for the media reference's available range)."""
    if asset.kind is AssetKind.IMAGE:
        return Fraction(1)
    if asset.video is not None:
        return Fraction(asset.video.duration_frames) / asset.video.frame_rate.fraction * timeline_fps
    if asset.audio is not None:
        return Fraction(asset.audio.duration_samples, asset.audio.sample_rate) * timeline_fps
    return Fraction(0)


def _clip(store: ProjectStore, asset: Asset, clip: Clip, fps: Fraction, track: str) -> dict[str, Any]:
    source_fps = _source_fps(asset, fps)
    start = Fraction(clip.source_in_frames) / source_fps * fps
    path = store.safe_path(asset.relative_path)
    reference = {
        "OTIO_SCHEMA": "ExternalReference.1",
        "name": asset.original_filename,
        "target_url": path.resolve().as_uri(),
        "available_range": _range(0, _asset_length(asset, fps), fps),
        "available_image_bounds": None,
        "metadata": {"ageneditor": {"asset_id": asset.id, "sha256": asset.sha256}},
    }
    return _item(
        "Clip.2",
        clip.clip_id,
        source_range=_range(start, clip.timeline_span_frames, fps),
        media_references={"DEFAULT_MEDIA": reference},
        active_media_reference_key="DEFAULT_MEDIA",
        metadata={
            "ageneditor": {
                "clip_id": clip.clip_id,
                "asset_id": asset.id,
                "sha256": asset.sha256,
                "track": track,
                "source_in_frames": clip.source_in_frames,
                "source_out_frames": clip.source_out_frames,
                "gain_db": clip.gain_db,
                "fade_in_frames": clip.fade_in_frames,
                "fade_out_frames": clip.fade_out_frames,
                "fit_mode": clip.fit_mode.value,
                "transform": clip.transform.model_dump(mode="json"),
                "rationale": clip.rationale,
            }
        },
    )


def _lanes(clips: list[Clip]) -> list[list[Clip]]:
    """Pack possibly overlapping clips into the fewest non-overlapping lanes (sound effects)."""
    lanes: list[list[Clip]] = []
    for clip in sorted(clips, key=lambda c: c.timeline_in_frames):
        for lane in lanes:
            if lane[-1].timeline_out_frames <= clip.timeline_in_frames:
                lane.append(clip)
                break
        else:
            lanes.append([clip])
    return lanes


def export_otio(store: ProjectStore, timeline: Timeline, name: str | None = None) -> str:
    """OTIO JSON for one timeline revision."""
    assets = store.assets()
    fps = timeline.render_profile.frame_rate.fraction
    transitions = {t.after_clip_id: t for t in timeline.transitions}

    def track(label: str, kind: str, clips: list[Clip], code: str, with_transitions: bool = False) -> dict:
        children: list[dict[str, Any]] = []
        cursor = 0
        ordered = sorted(clips, key=lambda c: c.timeline_in_frames)
        for index, clip in enumerate(ordered):
            if clip.timeline_in_frames > cursor:
                children.append(
                    _item("Gap.1", "", source_range=_range(0, clip.timeline_in_frames - cursor, fps))
                )
            children.append(_clip(store, assets[clip.asset_id], clip, fps, code))
            cursor = clip.timeline_out_frames
            transition = transitions.get(clip.clip_id) if with_transitions else None
            if transition and index + 1 < len(ordered):
                children.append(
                    {
                        "OTIO_SCHEMA": "Transition.1",
                        "name": transition.preset.value,
                        "transition_type": "SMPTE_Dissolve"
                        if "dissolve" in transition.preset.value
                        else "Custom",
                        "in_offset": _rt(transition.incoming_frames, fps),
                        "out_offset": _rt(transition.outgoing_frames, fps),
                        "metadata": {
                            "ageneditor": {
                                "preset": transition.preset.value,
                                "rationale": transition.rationale,
                            }
                        },
                    }
                )
        return _item("Track.1", label, kind=kind, children=children, metadata={"ageneditor": {"track": code}})

    by_track = {t: [c for c in timeline.clips if c.track is t] for t in TrackId if t is not TrackId.T1}
    v1 = by_track[TrackId.V1]
    tracks = [track("V1 primary", "Video", v1, "V1", with_transitions=True)]
    if by_track[TrackId.V2]:
        tracks.append(track("V2 overlays", "Video", by_track[TrackId.V2], "V2"))
    speaker = [c for c in v1 if assets[c.asset_id].audio is not None]
    if speaker:
        tracks.append(track("A1 speaker (V1 audio)", "Audio", speaker, "V1-audio"))
    for code, label in ((TrackId.A1, "A1 dialogue"), (TrackId.A2, "A2 music"), (TrackId.A4, "A4 voice")):
        if by_track[code]:
            tracks.append(track(label, "Audio", by_track[code], code.value))
    for number, lane in enumerate(_lanes(by_track[TrackId.A3]), start=1):
        tracks.append(track(f"A3 sound effects {number}", "Audio", lane, "A3"))

    document = {
        "OTIO_SCHEMA": "Timeline.1",
        "name": name or f"AgenEditor r{timeline.revision}",
        "global_start_time": None,
        "tracks": _item("Stack.1", "tracks", children=tracks),
        "metadata": {
            "ageneditor": {
                "project_id": timeline.project_id,
                "revision": timeline.revision,
                "render_profile": timeline.render_profile.model_dump(mode="json"),
                "caption_count": len(timeline.captions),
                "note": "Captions are exported separately as SRT. "
                "Interchange with other editors is not verified.",
            }
        },
    }
    return json.dumps(document, indent=2)


# ---- import ---------------------------------------------------------------------------------------


def _seconds(rt: dict[str, Any]) -> Fraction:
    rate = Fraction(str(rt.get("rate") or 0))
    if rate <= 0:
        raise ValueError("a time has no rate")
    return Fraction(str(rt.get("value") or 0)) / rate


def _duration(item: dict[str, Any]) -> Fraction:
    source = item.get("source_range")
    if not source:
        raise ValueError(f"{item.get('OTIO_SCHEMA')} {item.get('name')!r} has no source range")
    return _seconds(source["duration"])


def _path_of(reference: dict[str, Any]) -> Path | None:
    url = reference.get("target_url") or ""
    parsed = urlparse(url)
    if parsed.scheme == "file":
        text = unquote(parsed.path)
        if len(text) > 2 and text[0] == "/" and text[2] == ":":
            text = text[1:]  # file:///C:/... on Windows
        return Path(text)
    return Path(url) if url and "://" not in url else None


def _match_asset(clip: dict[str, Any], store: ProjectStore, assets: dict[str, Asset]) -> Asset | None:
    candidates = [clip.get("metadata", {}).get("ageneditor", {})]
    references = clip.get("media_references") or {}
    key = clip.get("active_media_reference_key") or "DEFAULT_MEDIA"
    reference = references.get(key) or clip.get("media_reference") or {}
    candidates.append(reference.get("metadata", {}).get("ageneditor", {}))
    for meta in candidates:
        if meta.get("asset_id") in assets:
            return assets[meta["asset_id"]]
        for asset in assets.values():
            if meta.get("sha256") and asset.sha256 == meta["sha256"]:
                return asset
    path = _path_of(reference)
    if path is None:
        return None
    for asset in assets.values():
        try:
            if store.safe_path(asset.relative_path).resolve() == path.resolve():
                return asset
        except (OSError, EngineException):
            continue
    named = [a for a in assets.values() if a.original_filename == path.name]
    return named[0] if len(named) == 1 else None


def import_otio(
    store: ProjectStore, text: str, base: Timeline
) -> tuple[Timeline | None, list[ValidationIssue]]:
    """A candidate timeline (not committed) from OTIO JSON, with every issue found."""
    if len(text.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise EngineException(ErrorCode.INVALID_ARGUMENT, "the OTIO file is too large")
    issues: list[ValidationIssue] = []

    def problem(message: str, severity: str = "error") -> None:
        issues.append(ValidationIssue(code=ErrorCode.INVALID_TIMELINE, message=message, severity=severity))

    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EngineException(ErrorCode.INVALID_ARGUMENT, "the file is not OTIO JSON") from exc
    if not isinstance(document, dict) or not str(document.get("OTIO_SCHEMA", "")).startswith("Timeline."):
        raise EngineException(ErrorCode.INVALID_ARGUMENT, "the file is not an OTIO timeline")
    fps = base.render_profile.frame_rate.fraction
    assets = store.active_assets()
    clips: list[Clip] = []
    transitions: list[Transition] = []
    used_ids: set[str] = set()
    video_tracks = 0
    count = 0

    def frames(seconds: Fraction) -> int:
        return round(seconds * fps)

    for track in (document.get("tracks") or {}).get("children") or []:
        if track.get("OTIO_SCHEMA", "").split(".")[0] != "Track" or not track.get("enabled", True):
            continue
        kind = track.get("kind")
        code = (track.get("metadata") or {}).get("ageneditor", {}).get("track")
        if kind == "Video":
            video_tracks += 1
            target = TrackId.V1 if video_tracks == 1 else TrackId.V2
        elif code == "V1-audio":
            continue  # the speaker's audio travels with its V1 video
        else:
            target = None  # decided per clip from the asset's role
        cursor = Fraction(0)
        previous_clip: Clip | None = None
        for item in track.get("children") or []:
            schema = str(item.get("OTIO_SCHEMA", "")).split(".")[0]
            try:
                if schema == "Transition":
                    preset = (item.get("metadata") or {}).get("ageneditor", {}).get("preset")
                    if target is TrackId.V1 and previous_clip is not None and preset:
                        incoming = frames(_seconds(item["in_offset"]))
                        outgoing = frames(_seconds(item["out_offset"]))
                        try:
                            transitions.append(
                                Transition(
                                    after_clip_id=previous_clip.clip_id,
                                    preset=preset,
                                    duration_frames=incoming + outgoing,
                                    rationale=(item.get("metadata") or {})
                                    .get("ageneditor", {})
                                    .get("rationale"),
                                )
                            )
                        except ValueError:
                            problem(
                                f"transition after {previous_clip.clip_id!r} was dropped (unknown preset)",
                                "warning",
                            )
                    elif target is TrackId.V1:
                        problem("a transition without an AgenEditor preset was dropped", "warning")
                    continue
                duration = _duration(item)
                if schema == "Gap" or not item.get("enabled", True):
                    cursor += duration
                    continue
                if schema != "Clip":
                    problem(f"{schema} items are not supported and were skipped", "warning")
                    cursor += duration
                    continue
                count += 1
                if count > MAX_IMPORT_CLIPS:
                    raise EngineException(ErrorCode.INVALID_ARGUMENT, "the OTIO file has too many clips")
                asset = _match_asset(item, store, assets)
                if asset is None:
                    problem(f"clip {item.get('name')!r} uses media that is not registered in this project")
                    cursor += duration
                    continue
                meta = (item.get("metadata") or {}).get("ageneditor", {})
                track_id = target or {
                    AssetRole.MUSIC: TrackId.A2,
                    AssetRole.SFX: TrackId.A3,
                    AssetRole.VOICE: TrackId.A4,
                    AssetRole.PRIMARY: TrackId.A1,
                }.get(asset.role)
                if track_id is None:
                    problem(f"clip {item.get('name')!r}: {asset.role} audio has no track", "warning")
                    cursor += duration
                    continue
                source_fps = _source_fps(asset, fps)
                start_seconds = _seconds(item["source_range"]["start_time"])
                if "source_in_frames" in meta and "source_out_frames" in meta:
                    source_in, source_out = int(meta["source_in_frames"]), int(meta["source_out_frames"])
                elif asset.kind is AssetKind.IMAGE:
                    source_in, source_out = 0, 1
                else:
                    source_in = round(start_seconds * source_fps)
                    source_out = max(source_in + 1, round((start_seconds + duration) * source_fps))
                clip_id = str(meta.get("clip_id") or item.get("name") or f"clip_{count}")
                clip_id = (
                    "".join(c if c.isalnum() or c in "_-" else "_" for c in clip_id.lower())[:60] or "clip"
                )
                if not clip_id[0].isalnum():
                    clip_id = f"c{clip_id}"[:60]
                while clip_id in used_ids:
                    clip_id = f"{clip_id[:56]}_{count}"
                used_ids.add(clip_id)
                extra = {
                    key: meta[key]
                    for key in (
                        "gain_db",
                        "fade_in_frames",
                        "fade_out_frames",
                        "fit_mode",
                        "transform",
                        "rationale",
                    )
                    if meta.get(key) is not None
                }
                clip = Clip.model_validate(
                    {
                        "clip_id": clip_id,
                        "asset_id": asset.id,
                        "track": track_id.value,
                        "source_in_frames": source_in,
                        "source_out_frames": source_out,
                        "timeline_in_frames": frames(cursor),
                        "timeline_duration_frames": max(1, frames(duration)),
                        **extra,
                    }
                )
                clips.append(clip)
                if track_id is TrackId.V1:
                    previous_clip = clip
                cursor += duration
            except (KeyError, TypeError, ValueError) as exc:
                problem(f"could not read {schema or 'an item'} {item.get('name')!r}: {exc}")
    if not any(c.track is TrackId.V1 for c in clips):
        problem("the OTIO file has no usable primary video clips")
    if any(issue.severity == "error" for issue in issues):
        return None, issues
    try:
        candidate = Timeline.model_validate(
            {
                **base.model_dump(mode="json"),
                "clips": [c.model_dump(mode="json") for c in clips],
                "captions": [],
                "transitions": [t.model_dump(mode="json") for t in transitions],
                "provenance": Provenance(
                    author=Author.USER, tool="import_otio", summary="Imported from an OpenTimelineIO file"
                ).model_dump(mode="json"),
            }
        )
    except ValueError as exc:
        problem(f"the imported timeline is not valid: {exc}")
        return None, issues
    if base.captions:
        problem("captions were not imported; build them again for the new cut", "warning")
    issues.extend(store.validation_issues(candidate))
    return candidate, issues
