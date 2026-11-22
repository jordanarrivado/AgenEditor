"""Built-in chat orchestration (Phase 6, docs/BUILT_IN_CHAT_PLAN.md).

``send`` stores the person's message, builds bounded text evidence (brief, registered assets, transcript
words with IDs and times, the current revision), asks the chosen provider for a reply and, when the reply
contains a plan, validates it with the normal ``submit_edit_plan`` checks (one retry with the validation
errors as feedback). A valid plan is stored as a proposal; ``apply_proposal`` commits it as one new
revision only when a person asks and the project is still at the revision the proposal was made against.
The model never commits, renders or claims a result; every status comes from engine records.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from ageneditor.contracts import AssetRole, EditPlan, EngineException, ErrorCode, TrackId
from ageneditor.contracts.chat import ChatMessage, ChatProposal, ChatProvider, ChatTurn
from ageneditor.contracts.timeline import Author, Provenance
from ageneditor.storage import ProjectStore

MAX_EVIDENCE_WORDS = 1500
HISTORY_MESSAGES = 12
MAX_USER_TEXT = 4000
#: What leaves the machine, shown to the person before they allow chat for a project.
DATA_SENT = [
    "your message and the earlier chat messages in this project",
    "the project's instructions and script text",
    "file names, durations and frame rates of registered files (no media, no file paths)",
    "transcript words with their word IDs and times (up to 1,500 words)",
    "the current edit's revision number, length and cut list",
]

SYSTEM = """You are the edit planner inside AgenEditor, a local video editor. You only edit the person's own
registered footage; you never generate media, invent words, or claim that anything was rendered or
checked.

Reply with ONE JSON object and nothing else:
{"message": "<plain-language reply to the person>", "plan": null}
or, when they ask for a cut and the evidence supports one:
{"message": "<what the cut does and why>", "plan": {"target_duration_frames": <int>, "brief": "<10+ chars>",
 "segments": [{"asset_id": "...", "transcript_id": "...", "first_word_id": "...", "last_word_id": "...",
 "source_in_frames": <int>, "source_out_frames": <int>, "rationale": "<10+ chars>", "score": <0..1>}]}}

Plan rules: 2 to 5 segments in playback order; every segment cites the first and last transcript word it
contains; source frames are on the asset's own frame rate and must contain the cited words, with at most
1.5 s of uncited lead or tail; keep the total close to target_duration_frames on the timeline frame rate.
If the transcript is missing or too short, say so with "plan": null. AgenEditor validates every plan and
the person decides whether to apply it."""


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


# ---- storage --------------------------------------------------------------------------------------


def history(store: ProjectStore) -> list[ChatMessage]:
    with store._connect() as db:
        rows = db.execute("SELECT data FROM chat_messages ORDER BY created_at, rowid").fetchall()
    return [ChatMessage.model_validate_json(row[0]) for row in rows]


def proposals(store: ProjectStore) -> list[ChatProposal]:
    with store._connect() as db:
        rows = db.execute("SELECT data FROM chat_proposals ORDER BY rowid").fetchall()
    return [ChatProposal.model_validate_json(row[0]) for row in rows]


def proposal(store: ProjectStore, proposal_id: str) -> ChatProposal:
    with store._connect() as db:
        row = db.execute("SELECT data FROM chat_proposals WHERE id=?", (proposal_id,)).fetchone()
    if row is None:
        raise EngineException(ErrorCode.INVALID_ARGUMENT, "proposal does not exist")
    return ChatProposal.model_validate_json(row[0])


def _save_message(store: ProjectStore, message: ChatMessage) -> ChatMessage:
    with store._connect() as db:
        db.execute(
            "INSERT INTO chat_messages VALUES (?, ?, ?)",
            (message.id, message.created_at.isoformat(), message.model_dump_json()),
        )
    return message


def _save_proposal(store: ProjectStore, item: ChatProposal) -> None:
    with store._connect() as db:
        db.execute(
            "INSERT INTO chat_proposals VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
            (item.id, item.model_dump_json()),
        )


# ---- evidence -------------------------------------------------------------------------------------


def evidence(store: ProjectStore) -> str:
    """Bounded text the provider sees. No media, no absolute paths, no keys."""
    brief = store.brief()
    timeline = store.timeline()
    fps = timeline.render_profile.frame_rate
    lines = [
        f"Project timeline: revision {timeline.revision}, target {timeline.target_duration_frames} frames at "
        f"{fps.num}/{fps.den} fps.",
    ]
    if brief.brief_text.strip():
        lines += ["", "Instructions from the person:", brief.brief_text.strip()[:4000]]
    if brief.script_text.strip():
        lines += ["", "Script:", brief.script_text.strip()[:4000]]
    lines += ["", "Registered files:"]
    budget = MAX_EVIDENCE_WORDS
    transcripts: list[str] = []
    for asset in store.active_assets().values():
        if asset.video is not None:
            rate = asset.video.frame_rate
            seconds = asset.video.duration_frames * rate.den / rate.num
            detail = (
                f"video {asset.video.width}x{asset.video.height}, {rate.num}/{rate.den} fps, {seconds:.1f}s"
            )
        elif asset.audio is not None:
            detail = f"audio {asset.audio.duration_samples / asset.audio.sample_rate:.1f}s"
        else:
            detail = asset.kind.value
        lines.append(f"- {asset.id} ({asset.role.value}): {asset.original_filename}, {detail}")
        if asset.role is not AssetRole.PRIMARY or budget <= 0:
            continue
        transcript = store.latest_transcript(asset.id, asset.sha256)
        if transcript is None:
            transcripts.append(f"{asset.id}: no transcript yet (transcribe it first).")
            continue
        words = transcript.words[:budget]
        budget -= len(words)
        shown = " ".join(f"[{w.id} {w.start_us // 1000}-{w.end_us // 1000}ms] {w.text}" for w in words)
        note = (
            f" (truncated: first {len(words)} of {len(transcript.words)} words)"
            if len(words) < len(transcript.words)
            else ""
        )
        transcripts.append(
            f"{asset.id} transcript {transcript.id}, language {transcript.language}{note}:\n{shown}"
        )
    lines += ["", "Transcripts (word ID, start-end ms, word):", *(transcripts or ["none"])]
    v1 = sorted((c for c in timeline.clips if c.track is TrackId.V1), key=lambda c: c.timeline_in_frames)
    lines += ["", f"Current cut ({len(v1)} primary clips):"]
    lines += [
        f"- {c.clip_id}: {c.asset_id} source {c.source_in_frames}-{c.source_out_frames} at timeline "
        f"{c.timeline_in_frames}"
        for c in v1[:40]
    ] or ["empty"]
    return "\n".join(lines)


def _parse(reply: str) -> tuple[str, dict[str, Any] | None]:
    """(message, plan dict or None) from a reply that should be one JSON object."""
    text = reply.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return reply.strip()[:20_000], None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return reply.strip()[:20_000], None
    if not isinstance(data, dict):
        return reply.strip()[:20_000], None
    plan = data.get("plan")
    return str(data.get("message") or "").strip()[:20_000], plan if isinstance(plan, dict) else None


# ---- turn -----------------------------------------------------------------------------------------

Complete = Callable[[str, list[tuple[str, str]]], str]


def send(store: ProjectStore, provider: ChatProvider, model: str, text: str, complete: Complete) -> ChatTurn:
    """One chat turn. ``complete(system, messages)`` calls the provider (injected so tests stay offline)."""
    if store.setting("chat_consent") != "1":
        raise EngineException(
            ErrorCode.PERMISSION_DENIED,
            "allow the built-in chat for this project first (it sends text to the provider)",
        )
    text = text.strip()
    if not text or len(text) > MAX_USER_TEXT:
        raise EngineException(
            ErrorCode.INVALID_ARGUMENT, f"write a message of 1 to {MAX_USER_TEXT} characters"
        )
    earlier = [m for m in history(store) if m.role in {"user", "assistant"}][-HISTORY_MESSAGES:]
    user = _save_message(
        store,
        ChatMessage(
            id=_id("msg"), project_id=store.project_id, role="user", text=text, provider=provider, model=model
        ),
    )
    conversation = [(m.role, m.text) for m in earlier]
    conversation.append(("user", f"{text}\n\n--- Project evidence ---\n{evidence(store)}"))
    revision = store.current_revision()
    saved = [user]
    item: ChatProposal | None = None
    for attempt in range(2):
        reply = complete(SYSTEM, conversation)
        message, plan_data = _parse(reply)
        feedback = None
        if plan_data is not None:
            try:
                plan = EditPlan.model_validate({**plan_data, "project_id": store.project_id})
            except ValidationError as exc:
                feedback = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:8])
            else:
                from ageneditor.planning import submit_edit_plan

                try:
                    result = submit_edit_plan(store, plan, revision)
                except EngineException as exc:
                    message = (
                        f"{message}\n\n(AgenEditor could not check this plan: {exc.error.message})".strip()
                    )
                else:
                    item = ChatProposal(
                        id=_id("proposal"),
                        project_id=store.project_id,
                        base_revision=revision,
                        plan=plan,
                        result=result,
                        status="proposed" if result.valid else "rejected",
                    )
                    if not result.valid and attempt == 0:
                        feedback = "; ".join(issue.message for issue in result.issues[:8])
        if feedback and attempt == 0:
            conversation += [
                ("assistant", reply[:20_000]),
                (
                    "user",
                    f"AgenEditor could not accept that plan: {feedback}. "
                    "Fix it and reply with the JSON again.",
                ),
            ]
            continue
        if item is not None:
            _save_proposal(store, item)
        saved.append(
            _save_message(
                store,
                ChatMessage(
                    id=_id("msg"),
                    project_id=store.project_id,
                    role="assistant",
                    text=message or "(the model returned no message)",
                    provider=provider,
                    model=model,
                    proposal_id=item.id if item else None,
                ),
            )
        )
        break
    return ChatTurn(messages=saved, proposal=item)


def apply_proposal(store: ProjectStore, proposal_id: str, expected_revision: int) -> ChatProposal:
    """Commit a valid proposal as a new revision, authored by the person who applied it."""
    item = proposal(store, proposal_id)
    if item.status != "proposed" or not item.result.valid or item.result.candidate_timeline is None:
        raise EngineException(ErrorCode.INVALID_ARGUMENT, "only a valid, unapplied proposal can be applied")
    current = store.current_revision()
    if item.base_revision != expected_revision or current != expected_revision:
        stale = item.model_copy(update={"status": "stale"})
        _save_proposal(store, stale)
        raise EngineException(
            ErrorCode.REVISION_CONFLICT,
            "the edit changed since this proposal was made; ask again",
            current_revision=current,
        )
    candidate = item.result.candidate_timeline.model_copy(
        update={
            "provenance": Provenance(
                author=Author.USER, tool="chat_apply", summary=f"Applied chat proposal {item.id}"
            )
        }
    )
    committed = store.commit_timeline(candidate, expected_revision)
    applied = item.model_copy(update={"status": "applied", "applied_revision": committed.revision})
    _save_proposal(store, applied)
    return applied
