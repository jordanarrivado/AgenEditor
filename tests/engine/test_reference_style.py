"""Reference intelligence (spec §10): only measured, source-cited style notes come from the engine."""

from __future__ import annotations

from pathlib import Path

import pytest

from ageneditor.contracts import AssetRole, EngineException, ErrorCode
from ageneditor.production.reference_style import (
    _frame_text,
    _rate_text,
    measure_reference,
    parse_cut_times,
    reference_assets,
    shot_lengths,
)
from ageneditor.storage import ProjectStore


def test_cut_parsing_and_shot_lengths_use_integer_microseconds() -> None:
    stderr = (
        b"[Parsed_showinfo_2 @ 0x1] n:   0 pts:  3600 pts_time:2.5 duration: 1\n"
        b"[Parsed_showinfo_2 @ 0x1] n:   1 pts:  9000 pts_time:6.25 duration: 1\n"
        b"[Parsed_showinfo_2 @ 0x1] n:   2 pts:  9000 pts_time:6.25 duration: 1\n"
    )
    assert parse_cut_times(stderr) == [2_500_000, 6_250_000]
    assert shot_lengths([2_500_000, 6_250_000], 10_000_000) == [2_500_000, 3_750_000, 3_750_000]
    assert shot_lengths([0, 12_000_000], 10_000_000) == [10_000_000]  # cuts at or past the end are ignored
    assert shot_lengths([], 10_000_000) == [10_000_000]


def test_frame_and_rate_text() -> None:
    assert _frame_text(1080, 1920) == "1080x1920 (vertical, 9:16)"
    assert _frame_text(1920, 1080) == "1920x1080 (horizontal, 16:9)"
    assert _frame_text(1080, 1080) == "1080x1080 (square, 1:1)"
    assert _rate_text(30, 1) == "30 fps"
    assert _rate_text(30000, 1001) == "29.97 fps"


def test_reference_measurement_is_measured_and_cited(tmp_path: Path, media_dir: Path) -> None:
    store = ProjectStore.create(tmp_path, "references")
    reference = store.import_asset(media_dir / "talking_head_9x16_2997fps_20s.mp4", AssetRole.REFERENCE)
    primary = store.import_asset(media_dir / "talking_head_16x9_30fps_60s.mp4", AssetRole.PRIMARY)

    assert [item["id"] for item in reference_assets(store)] == [reference.id]
    notes = measure_reference(store, reference.id)
    by_label = {note.label: note for note in notes}
    assert all(note.basis == "measured" and note.source_asset_id == reference.id for note in notes)
    assert by_label["Frame"].value == "1080x1920 (vertical, 9:16)"
    assert by_label["Frame rate"].value == "29.97 fps"
    assert by_label["Reference length"].value.startswith("20.")
    assert by_label["Cuts"].value.endswith("detected")
    assert by_label["Average shot length"].value.endswith(" s")
    assert by_label["Integrated loudness"].value.endswith("LUFS")
    assert len({note.id for note in notes}) == len(notes)

    with pytest.raises(EngineException) as not_reference:
        measure_reference(store, primary.id)
    assert not_reference.value.error.code is ErrorCode.INVALID_ARGUMENT
    with pytest.raises(EngineException) as missing:
        measure_reference(store, "asset_missing")
    assert missing.value.error.code is ErrorCode.ASSET_NOT_FOUND
