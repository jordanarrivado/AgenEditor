"""Phase 6: workspace editor presets and project rename/archive."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from mcp import Client

from ageneditor.contracts import (
    AssetRole,
    AudioMix,
    CaptionPreset,
    Clip,
    ColorPreset,
    EngineException,
    ErrorCode,
    RenderProfile,
    TrackId,
)
from ageneditor.desktop_service import DesktopService
from ageneditor.editor_presets import EditorPresetStore, apply_preset
from ageneditor.mcp_server import build_server
from ageneditor.storage import ProjectStore
from ageneditor.timebase import FrameRate

PROFILE = RenderProfile(id="presets", width=180, height=320, frame_rate=FrameRate(num=30))


def _project(workspace: Path, media_dir: Path, name: str) -> ProjectStore:
    store = ProjectStore.create(workspace, name)
    primary = store.import_asset(media_dir / "talking_head_16x9_30fps_60s.mp4", AssetRole.PRIMARY)
    seed = store.create_timeline(PROFILE, 30)
    clip = Clip(clip_id="v", asset_id=primary.id, track=TrackId.V1, source_in_frames=0, source_out_frames=30,
                timeline_in_frames=0)  # fmt: skip
    store.commit_timeline(seed.model_copy(update={"clips": [clip]}), 0)
    return store


@pytest.mark.media
def test_a_preset_saved_from_one_project_applies_to_another(tmp_path: Path, media_dir: Path) -> None:
    styled = _project(tmp_path, media_dir, "styled")
    current = styled.timeline()
    styled.commit_timeline(
        current.model_copy(
            update={
                "caption_preset": CaptionPreset.TOP_CLEAN,
                "color_preset": ColorPreset.WARM,
                "broll_color_match": True,
                "audio": AudioMix(speech_repair="light", duck_ratio=6),
            }
        ),
        current.revision,
    )
    presets = EditorPresetStore(tmp_path)
    library = presets.save_from_project(styled, "House style", 0)
    [preset] = library.presets
    assert (preset.caption_preset, preset.color_preset, preset.broll_color_match) == (
        CaptionPreset.TOP_CLEAN,
        ColorPreset.WARM,
        True,
    )
    assert preset.audio.speech_repair == "light" and preset.audio.duck_ratio == 6

    with pytest.raises(EngineException) as stale:
        presets.save_from_project(styled, "Other", 0)
    assert stale.value.error.code is ErrorCode.REVISION_CONFLICT
    with pytest.raises(EngineException, match="name exists"):
        presets.save_from_project(styled, "house STYLE", library.version)

    plain = _project(tmp_path, media_dir, "plain")
    before = plain.timeline()
    result = apply_preset(plain, presets.get(preset.id), before.revision)
    after = plain.timeline()
    assert after.revision == before.revision + 1 and result.timeline.revision == after.revision
    assert after.caption_preset is CaptionPreset.TOP_CLEAN and after.color_preset is ColorPreset.WARM
    assert after.broll_color_match is True and after.audio.speech_repair == "light"

    # Finishing settings only: every clip plays the same range at the same place.
    def placed(timeline):
        return [
            (c.clip_id, c.source_in_frames, c.source_out_frames, c.timeline_in_frames, c.timeline_span_frames)
            for c in timeline.clips
        ]

    assert placed(after) == placed(before)

    emptied = presets.delete(preset.id, library.version)
    assert emptied.presets == [] and emptied.version == library.version + 1


@pytest.mark.media
def test_projects_can_be_renamed_archived_and_given_a_preset(tmp_path: Path, media_dir: Path) -> None:
    store = _project(tmp_path, media_dir, "first name")
    library = EditorPresetStore(tmp_path).save_from_project(store, "Clean", 0)
    preset_id = library.presets[0].id
    service = DesktopService(tmp_path)
    try:
        call = service._desktop_call
        args = {"project_id": store.project_id}
        assert call("rename_project", {**args, "name": "  Better name "})["name"] == "Better name"
        with pytest.raises(EngineException):
            call("rename_project", {**args, "name": "   "})
        call("set_project_archived", {**args, "archived": True})
        [listed] = call("list_projects", {})
        assert listed["name"] == "Better name" and listed["archived"] is True
        call("set_project_archived", {**args, "archived": False})
        assert call("list_projects", {})[0]["archived"] is False

        with pytest.raises(EngineException, match="does not exist"):
            call("choose_editor_preset", {**args, "preset_id": "preset_missing"})
        call("choose_editor_preset", {**args, "preset_id": preset_id})
        assert call("project_settings", args) == {
            "archived": False,
            "editor_preset_id": preset_id,
            "client_name": None,
        }
        assert [p["name"] for p in call("list_editor_presets", {})["presets"]] == ["Clean"]
    finally:
        service.http.server_close()
    # History and folder are untouched by a rename.
    assert ProjectStore(store.root).current_revision() == store.current_revision()

    async def through_mcp() -> None:
        async with Client(build_server(tmp_path), raise_exceptions=True) as client:
            listed = await client.call_tool("list_editor_presets", {"project_id": store.project_id})
            assert listed.structured_content["chosen"] == preset_id
            applied = await client.call_tool(
                "apply_editor_preset",
                {
                    "project_id": store.project_id,
                    "preset_id": preset_id,
                    "expected_revision": store.current_revision(),
                },
            )
            assert applied.structured_content["timeline"]["revision"] == 2

    asyncio.run(through_mcp())
