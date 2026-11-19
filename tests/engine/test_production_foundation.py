"""AgenEditor 2.0 gate B: multi-clip data foundation.

Exit criterion: one source produces three independently planned clips with valid source evidence and
separate revision histories, sharing one cached analysis.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from production_helpers import build, create_batch, discover, make_project, policy

from ageneditor.contracts import (
    Batch,
    CandidateProposal,
    ClipStatus,
    CreativeDefaults,
    EngineException,
    ErrorCode,
    EvidenceCitation,
    InstructionProfile,
    ProfileScope,
    TaskType,
)
from ageneditor.contracts.production import ColorPreset
from ageneditor.orchestration.task_graph import production_graph, validate_graph
from ageneditor.production.clip_discovery import validate_proposals
from ageneditor.production.context_resolver import resolve_context
from ageneditor.production.store import ProductionStore, clip_transition_allowed
from ageneditor.production.workspace import WorkspaceRegistry
from ageneditor.storage import ProjectStore


def test_migration_is_additive_and_legacy_projects_still_open(tmp_path: Path) -> None:
    store = ProjectStore.create(tmp_path, "legacy")
    with sqlite3.connect(store.db_path) as db:
        db.execute("DROP TABLE clip_timelines")  # simulate a project created before 2.0
    reopened = ProjectStore(store.root)
    with reopened._connect() as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"timelines", "jobs", "batches", "clips", "clip_timelines", "tasks", "approvals"} <= tables
    assert reopened.current_revision() == 0


def test_context_layers_resolve_in_order_and_locked_fields_escalate(tmp_path: Path, media_dir: Path) -> None:
    store, _ = make_project(tmp_path, media_dir)
    registry = WorkspaceRegistry(tmp_path)
    registry.save_profile(
        InstructionProfile(
            scope=ProfileScope.GLOBAL,
            instructions="House style",
            creative=CreativeDefaults(color_preset=ColorPreset.NATURAL),
        ),
        0,
    )
    client = registry.create_client("Acme")
    registry.bind_project(store.project_id, client.id, has_batches=False)
    registry.save_profile(
        InstructionProfile(
            scope=ProfileScope.CLIENT,
            client_id=client.id,
            instructions="Acme voice",
            creative=CreativeDefaults(color_preset=ColorPreset.WARM),
            locked_creative=["color_preset"],
            required_constraints=["Never show competitor logos"],
        ),
        0,
    )
    ok = resolve_context(registry, store, client.id, policy(), "batch notes")
    assert ok.creative.color_preset is ColorPreset.WARM  # client overrides global
    assert [scope for scope, _ in ok.instructions] == ["global", "client", "batch"]
    assert "Never show competitor logos" in ok.required_constraints
    assert ok.engine_invariants and not ok.conflicts
    # Same inputs → same content-addressed snapshot.
    assert resolve_context(registry, store, client.id, policy(), "batch notes").hash == ok.hash
    clash = resolve_context(
        registry,
        store,
        client.id,
        policy(creative=CreativeDefaults(color_preset=ColorPreset.COOL)),
        "batch notes",
    )
    assert clash.conflicts and clash.conflicts[0].field == "color_preset"
    assert clash.creative.color_preset is ColorPreset.WARM  # never silently overridden


def test_client_binding_is_fixed_once_batches_exist(tmp_path: Path) -> None:
    registry = WorkspaceRegistry(tmp_path)
    acme, other = registry.create_client("Acme"), registry.create_client("Other")
    registry.bind_project("project_x", acme.id, has_batches=False)
    with pytest.raises(EngineException) as denied:
        registry.bind_project("project_x", other.id, has_batches=True)
    assert denied.value.error.code is ErrorCode.PERMISSION_DENIED
    with pytest.raises(EngineException):
        registry.create_client("acme")


def test_desktop_profile_and_binding_operations(tmp_path: Path) -> None:
    from ageneditor.production.desktop_ops import production_call
    from ageneditor.storage import ProjectStore

    store = ProjectStore.create(tmp_path, "profiles")  # no media needed
    orchestrator, _ = build(tmp_path)
    acme = production_call(orchestrator, "production_create_client", {"name": "Acme"})
    assert production_call(orchestrator, "production_project_clients", {}) == {}
    production_call(
        orchestrator, "production_bind_project", {"project_id": store.project_id, "client_id": acme["id"]}
    )
    assert production_call(orchestrator, "production_project_clients", {}) == {store.project_id: acme["id"]}

    empty = production_call(
        orchestrator, "production_get_profile", {"scope": "client", "client_id": acme["id"]}
    )
    assert empty["revision"] == 0
    profile = {**empty, "instructions": "Warm and calm.", "locked_creative": ["color_preset"]}
    profile["creative"] = {**empty["creative"], "color_preset": "warm"}
    saved = production_call(
        orchestrator, "production_save_profile", {"profile": profile, "expected_revision": 0}
    )
    assert saved["revision"] == 1 and saved["creative"]["color_preset"] == "warm"
    with pytest.raises(EngineException) as stale:
        production_call(orchestrator, "production_save_profile", {"profile": profile, "expected_revision": 0})
    assert stale.value.error.code is ErrorCode.REVISION_CONFLICT


def test_deterministic_discovery_is_source_linked_and_reports_shortfall(
    tmp_path: Path, media_dir: Path
) -> None:
    store, primary = make_project(tmp_path, media_dir)
    orchestrator, service = build(tmp_path)
    batch_id = create_batch(service, store.project_id, target_count=10)
    discover(orchestrator, service, batch_id)
    status = service.status(batch_id)
    discovery = next(t for t in status["tasks"] if t["type"] == "discover_clips")["result"]
    assert discovery["viable_count"] == 6 and discovery["shortfall"] == 4
    assert any("No filler" in note for note in discovery["notes"])
    transcript = store.transcript("transcript_production")
    words = {word.id: word for word in transcript.words}
    for candidate in status["candidates"]:
        evidence = candidate["evidence"][0]
        assert evidence["asset_id"] == primary
        first, last = words[evidence["first_word_id"]], words[evidence["last_word_id"]]
        assert (evidence["start_us"], evidence["end_us"]) == (first.start_us, last.end_us)
        ids = [w.id for w in transcript.words]
        cited = transcript.words[ids.index(first.id) : ids.index(last.id) + 1]
        assert candidate["quote"] == " ".join(w.text for w in cited)  # exact, never paraphrased
        assert candidate["overlaps"] == []


def test_agent_proposals_cannot_invent_dialogue(tmp_path: Path, media_dir: Path) -> None:
    store, _ = make_project(tmp_path, media_dir)
    orchestrator, service = build(tmp_path)
    batch_id = create_batch(service, store.project_id, exclusions=["morning"])
    production = orchestrator.production(store.project_id)
    batch: Batch = production.batch(batch_id)
    snapshot = production.snapshot(batch.context_hash)

    def cite(a: str, b: str) -> EvidenceCitation:
        return EvidenceCitation(transcript_id="transcript_production", first_word_id=a, last_word_id=b)

    report, rejected = validate_proposals(
        store,
        batch.model_copy(update={"policy": policy()}),
        snapshot,
        [
            CandidateProposal(
                topic="ok", rationale="two clean sentences", evidence=[cite("word_0000000", "word_0000019")]
            ),
            CandidateProposal(
                topic="fake", rationale="invented word id", evidence=[cite("word_0000000", "word_9999999")]
            ),
            CandidateProposal(
                topic="short", rationale="too short to use", evidence=[cite("word_0000000", "word_0000003")]
            ),
        ],
    )
    assert len(report.candidates) == 1 and len(rejected) == 2
    assert report.candidates[0].quote.startswith("we0 brewed coffee")
    _, excluded = validate_proposals(
        store,
        batch,
        snapshot,
        [
            CandidateProposal(
                topic="x",
                rationale="mentions the excluded word",
                evidence=[cite("word_0000000", "word_0000019")],
            )
        ],
    )
    assert "excluded" in excluded[0]


def test_one_source_three_independent_clips(tmp_path: Path, media_dir: Path) -> None:
    store, _ = make_project(tmp_path, media_dir)
    orchestrator, service = build(tmp_path, team_slots=3)
    batch_id = create_batch(service, store.project_id)
    candidates = discover(orchestrator, service, batch_id)
    clips = service.confirm_selection(batch_id, candidates[:3], "tester")
    orchestrator.run_until_idle(timeout=300)
    production: ProductionStore = orchestrator.production(store.project_id)
    plans = [production.plan(str(production.clip(c.id).plan_id)) for c in clips]
    assert len({plan.id for plan in plans}) == 3
    selected = [c for c in production.candidates(batch_id) if c.status.value == "selected"]
    assert sorted(plan.cuts[0].first_word_id for plan in plans) == sorted(
        c.evidence[0].first_word_id for c in selected
    )
    for clip in clips:
        assert production.clip(clip.id).status is ClipStatus.REVIEW_READY
        # Every revision of a clip was committed by that clip's own tasks.
        own_attempts = {a.id for t in production.tasks(clip_id=clip.id) for a in production.attempts(t.id)}
        assert {r["attempt_id"] for r in production.clip_revisions(clip.id)} - {None} <= own_attempts
    # Committing to one clip leaves the others untouched.
    a, b = clips[0].id, clips[1].id
    before_b = production.clip(b).current_revision
    head = production.clip(a).current_revision
    assert head is not None
    timeline = production.clip_timeline(a)
    production.commit_clip_timeline(a, timeline.model_copy(update={"clips": timeline.clips[:1]}), head)
    assert production.clip(a).current_revision == head + 1
    assert production.clip(b).current_revision == before_b
    assert store.current_revision() == 0  # the legacy project timeline is a separate history
    with pytest.raises(EngineException) as conflict:
        production.commit_clip_timeline(a, timeline, head)
    assert conflict.value.error.code is ErrorCode.REVISION_CONFLICT


def test_second_batch_reuses_cached_source_analysis(tmp_path: Path, media_dir: Path) -> None:
    store, _ = make_project(tmp_path, media_dir)
    orchestrator, service = build(tmp_path)
    first = create_batch(service, store.project_id)
    discover(orchestrator, service, first)
    second = create_batch(service, store.project_id)
    discover(orchestrator, service, second)
    production = orchestrator.production(store.project_id)
    analyses = [t for t in production.tasks() if t.type is TaskType.ANALYZE_SOURCE]
    assert len(analyses) == 2
    assert analyses[0].result and analyses[0].result["transcript_id"] == "transcript_production"
    assert analyses[1].result and "reused_checkpoint" in analyses[1].result
    assert not production.attempts(analyses[1].id)  # nothing re-ran


def test_task_graph_is_validated_and_composed_from_the_plan(tmp_path: Path, media_dir: Path) -> None:
    from ageneditor.contracts import ClipTaskGraph, GraphNode

    with pytest.raises(EngineException):
        validate_graph(
            ClipTaskGraph(
                clip_id="c",
                cycle=0,
                nodes=[
                    GraphNode(key="a", type=TaskType.CAPTIONS, depends_on=["b"]),
                    GraphNode(key="b", type=TaskType.CAPTIONS, depends_on=["a"]),
                ],
            )
        )
    with pytest.raises(EngineException):
        validate_graph(
            ClipTaskGraph(
                clip_id="c",
                cycle=0,
                nodes=[GraphNode(key="a", type=TaskType.CAPTIONS, depends_on=["missing"])],
            )
        )
    store, _ = make_project(tmp_path, media_dir)
    orchestrator, service = build(tmp_path)
    batch_id = create_batch(service, store.project_id)
    candidates = discover(orchestrator, service, batch_id)
    clip = service.confirm_selection(batch_id, candidates[:1], "tester")[0]
    orchestrator.run_until_idle(timeout=300)
    production = orchestrator.production(store.project_id)
    plan = production.plan(production.clip(clip.id).plan_id)
    keys = [node.key for node in production_graph(clip.id, plan, editorial_qa=True).nodes]
    assert "enhancement" not in keys  # no conditional pass requested → no dangling node
    assert keys[-1] == "editorial_qa"


def test_clip_state_machine_rejects_illegal_jumps() -> None:
    assert clip_transition_allowed(ClipStatus.READY, ClipStatus.EDITING)
    assert not clip_transition_allowed(ClipStatus.READY, ClipStatus.HUMAN_APPROVED)
    assert not clip_transition_allowed(ClipStatus.EDITING, ClipStatus.DELIVERED)
    assert clip_transition_allowed(ClipStatus.EDITING, ClipStatus.RETRY_WAIT)
    assert clip_transition_allowed(ClipStatus.RETRY_WAIT, ClipStatus.EDITING)
    assert not clip_transition_allowed(ClipStatus.CANCELLED, ClipStatus.READY)
