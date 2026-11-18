"""Shared setup for AgenEditor 2.0 production tests.

Transcripts are synthetic evidence (no ASR model in CI); media, renders and QA are real. AI workers are
the real ``AnthropicMessagesAdapter`` driven by a scripted client, so no network is ever used.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ageneditor.contracts import (
    AssetRole,
    BatchCreateRequest,
    BatchPolicy,
    BatchTargetMode,
    ModelPreset,
    RenderProfile,
    Transcript,
    TranscriptSegment,
    TranscriptWord,
)
from ageneditor.orchestration.host_adapters import AnthropicMessagesAdapter
from ageneditor.orchestration.orchestrator import ProductionOrchestrator
from ageneditor.production.batch_service import ProductionService
from ageneditor.production.workspace import WorkspaceRegistry
from ageneditor.storage import ProjectStore
from ageneditor.timebase import FrameRate

SENTENCE = ["we", "brewed", "coffee", "every", "morning", "before", "the", "studio", "opened", "today."]
PROFILE = RenderProfile(id="prod_test", width=180, height=320, frame_rate=FrameRate(num=30))


def synthetic_transcript(
    store: ProjectStore, asset_id: str, transcript_id: str = "transcript_prod"
) -> Transcript:
    """Twelve ten-word sentences, one word every 0.5 s across the 60 s source."""
    asset = store.assets()[asset_id]
    words, segments = [], []
    for s in range(12):
        seg_words = []
        for i in range(10):
            n = s * 10 + i
            word = TranscriptWord(
                id=f"word_{n:07d}",
                segment_id=f"segment_{s:04d}",
                text=SENTENCE[i] if i else f"{SENTENCE[i]}{s}",
                start_us=n * 500_000,
                end_us=n * 500_000 + 400_000,
                probability=0.95,
            )
            words.append(word)
            seg_words.append(word)
        segments.append(
            TranscriptSegment(
                id=f"segment_{s:04d}",
                start_us=seg_words[0].start_us,
                end_us=seg_words[-1].end_us,
                text=" ".join(w.text for w in seg_words),
                word_ids=[w.id for w in seg_words],
            )
        )
    transcript = Transcript(
        id=transcript_id,
        project_id=store.project_id,
        asset_id=asset_id,
        source_sha256=asset.sha256,
        model_preset=ModelPreset.BASE,
        model_version="synthetic",
        language="en",
        duration_us=60_000_000,
        words=words,
        segments=segments,
    )
    store.save_transcript(transcript)
    return transcript


def make_project(workspace: Path, media_dir: Path, name: str = "production") -> tuple[ProjectStore, str]:
    store = ProjectStore.create(workspace, name)
    primary = store.import_asset(media_dir / "talking_head_16x9_30fps_60s.mp4", AssetRole.PRIMARY)
    synthetic_transcript(store, primary.id, f"transcript_{name.replace(' ', '_')}")
    return store, primary.id


def policy(**changes: Any) -> BatchPolicy:
    base: dict[str, Any] = {
        "target_mode": BatchTargetMode.COUNT,
        "target_count": 3,
        "min_duration_us": 8_000_000,
        "max_duration_us": 12_000_000,
        "output_profile": PROFILE,
        "editorial_qa": "skip",
    }
    base.update(changes)
    return BatchPolicy.model_validate(base)


@dataclass
class Clock:
    now: datetime = field(default_factory=lambda: datetime(2026, 11, 17, 9, 0, tzinfo=UTC))

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@dataclass
class ScriptedClient:
    """Stands in for ``anthropic.Anthropic``: returns scripted JSON answers and records each call."""

    answers: list[Callable[[dict[str, Any]], Any]]
    calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def beta(self) -> Any:
        return SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        script = self.answers[min(len(self.calls), len(self.answers)) - 1]
        body = script(kwargs)
        if isinstance(body, Exception):
            raise body
        return SimpleNamespace(
            model=kwargs["model"],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
            content=[SimpleNamespace(type="text", text=json.dumps(body))],
        )


def packet_from(kwargs: dict[str, Any]) -> dict[str, Any]:
    text = kwargs["messages"][0]["content"]
    return json.loads(text[text.index("\n") + 1 :])


def build(
    workspace: Path,
    clock: Callable[[], datetime] | None = None,
    client: ScriptedClient | None = None,
    team_slots: int = 2,
) -> tuple[ProductionOrchestrator, ProductionService]:
    registry = WorkspaceRegistry(workspace)
    config = registry.resource_config().model_copy(update={"team_slots": team_slots})
    registry.save_resource_config(config)
    adapters = []
    if client is not None:
        registry.set_provider_enabled("anthropic_api", True)
        adapters.append(
            AnthropicMessagesAdapter(
                client_factory=lambda: client, prices={"claude-opus-5-5": (4_000_000, 20_000_000)}
            )
        )
    kwargs: dict[str, Any] = {"adapters": adapters}
    if clock is not None:
        kwargs["clock"] = clock
    orchestrator = ProductionOrchestrator(workspace, registry, **kwargs)
    return orchestrator, ProductionService(orchestrator)


def create_batch(service: ProductionService, project_id: str, **policy_changes: Any) -> str:
    created = service.create_batch(
        BatchCreateRequest(project_id=project_id, name="Launch shorts", policy=policy(**policy_changes))
    )
    return created["batch"]["id"]


def discover(orchestrator: ProductionOrchestrator, service: ProductionService, batch_id: str) -> list[str]:
    service.start_discovery(batch_id)
    orchestrator.run_until_idle()
    status = service.status(batch_id)
    assert status["batch"]["status"] == "awaiting_selection", status["tasks"]
    return [candidate["id"] for candidate in status["candidates"]]
