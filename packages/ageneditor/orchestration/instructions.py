"""Task instructions for AI workers. Built from engine records only; nothing from other clients."""

from __future__ import annotations

from ageneditor.contracts import FindingState, Task, TaskType
from ageneditor.production.context_resolver import ENGINE_INVARIANTS
from ageneditor.production.store import ProductionStore

_ROLE_TEXT: dict[TaskType, str] = {
    TaskType.DISCOVER_CLIPS: (
        "You are the clip discovery specialist. Read the source transcripts with "
        "production_get_transcript and propose self-contained short clips with "
        "discovery_submit_candidates. Cite word IDs only; the engine derives times and quotes. "
        "Never pad to a requested count: fewer strong clips beat filler."
    ),
    TaskType.PLAN_CLIP: (
        "You are the clip director. Read the assignment and transcript, then submit one plan with "
        "plan_submit: ordered cuts as word citations, the narrative goal, the hook and any "
        "conditional passes."
    ),
    TaskType.STRUCTURAL_EDIT: "You are the structural editor. Build the plan's cuts on this clip's timeline.",
    TaskType.CAPTIONS: "You are the caption specialist. Build and correct captions from transcript words.",
    TaskType.VISUAL_POLISH: "You are the visual specialist. Adjust framing and colour within the settings.",
    TaskType.AUDIO_POLISH: "You are the audio specialist. Keep dialogue intact; mix within the settings.",
    TaskType.ENHANCEMENT: (
        "You are the enhancement specialist. Add only approved, registered B-roll, music, SFX or "
        "transitions the plan asks for. Never invent media; if something is missing, say so and finish "
        "as a no-op."
    ),
    TaskType.REVISION: "You are the revision editor. Fix exactly the QA findings listed below, nothing else.",
    TaskType.EDITORIAL_QA: (
        "You are the independent editorial QA reviewer. Call qa_get_review_packet, judge hook, narrative, "
        "framing, pacing, captions and brief alignment from the evidence provided, then call "
        "qa_submit_findings once. Claim only evidence you actually examined. You cannot edit the clip."
    ),
}


def task_instructions(production: ProductionStore, task: Task) -> str:
    parts = [
        "You work inside AgenEditor on one scoped production task.",
        "Non-negotiable rules:",
        *[f"- {rule}" for rule in ENGINE_INVARIANTS],
        "",
        _ROLE_TEXT.get(task.type, "Complete the task with the scoped tools."),
        "Start with production_get_assignment. Every change goes through the tools; your messages are "
        "not evidence of anything. When done, call production_task_complete with a short summary (set "
        "no_op and a reason if nothing needed changing).",
    ]
    if task.type is TaskType.REVISION:
        findings = [production.finding(str(fid)) for fid in task.params.get("finding_ids", [])]
        parts.append("Findings to fix:")
        for finding in findings:
            if finding.state in {FindingState.REVISION_QUEUED, FindingState.OPEN}:
                parts.append(
                    f"- [{finding.severity.value}/{finding.category.value}] at {finding.reference.kind} "
                    f"{finding.reference.id or ''}: {finding.evidence} -> {finding.suggested_correction}"
                )
    return "\n".join(parts)[:40000]
