# Built-in AI chat implementation plan

**Status:** Plan for review; no implementation or provider connection has been made.

## Goal and scope

Add a real conversational editing panel to the native AgenEditor workspace. A user can describe an edit in plain language, inspect the assistant's source-cited proposal, save an immutable timeline revision, render a preview, request revisions, review measured QA, and export. The first release supports OpenAI, Google Gemini, and Anthropic Claude through user-supplied API credentials. The existing MCP handoff remains available.

The product specification defers native AI chat until Phase 6. The user's request moves this feature forward; all editing, source-integrity, privacy, and honest-status rules in the specification still apply. This feature edits user-supplied media only. It does not generate footage, images, voices, or music, or fetch stock media. External generation follows the Plan → Prepare assets → Execute → Review hand-off in spec §3.1: the planner's `asset_requests` become a checklist, generation runs in ChatGPT/Gemini/Google Flow, and the executor is gated until the assets are imported.

## User flow

1. Open a project, import media, and run local transcription/analysis where needed.
2. Choose a provider and model in Settings, enter a provider API key through a native credential dialog, and see a clear description of what text will be sent to the provider. Consumer ChatGPT, Gemini, and Claude subscriptions are not treated as API credentials.
3. In the workspace chat, enter a brief such as “Make a 40-second Reel with a strong hook.” The chat shows real conversation history and the current stage: gathering evidence, planning, validating, waiting for approval, rendering, or reviewing.
4. The planner reads bounded project metadata, transcript excerpts with word IDs and timestamps, and the brief. It returns a typed, source-cited `EditPlan` or explains why the available material is insufficient.
5. AgenEditor's existing `submit_edit_plan` service validates every asset, transcript word, range, duration, and revision. The UI presents the candidate cut list, citations, rationale, warnings, and a revision diff. Validation failures become actionable chat feedback; retry is capped.
6. The user can revise the proposal in chat or explicitly save it. Saving uses `expected_revision` and creates a new immutable timeline revision. A concurrent edit requires refresh and review before retry.
7. The user can request a real preview render. The chat displays the engine job's actual progress and a playable result only after the job succeeds. A reviewer model may assess the brief against the source-cited plan and available preview/QA evidence; its notes are labeled editorial advice, never technical measurements.
8. Further instructions produce a new proposal or typed revision patch. Final export remains gated by a successful final render and measured technical QA.

## Architecture

| Layer | Planned change |
|---|---|
| React workspace | Replace the JSON-paste-first experience with message composer, thread history, provider/model selector, evidence cards, proposal review, approval, job status, and revision controls. Keep the MCP handoff as a separate path. |
| Tauri/native bridge | Add narrow commands for credential setup/status and chat requests. Credentials never cross into the webview as values. |
| Shared Python service | Add an agent-session orchestrator that reads the same project state used by MCP and desktop, builds bounded evidence, calls a provider adapter, validates typed output, persists session events, and invokes existing edit/render/QA services. |
| Provider adapters | Implement OpenAI Responses, Gemini, and Claude Messages behind one internal interface. Keep provider-specific request and response handling outside editing contracts. Use structured outputs or tool calls where supported, followed by local Pydantic validation in all cases. |
| Contracts and storage | Add versioned chat request, message, proposal, and provider-setting contracts. Store messages/proposals per project with provider/model IDs and the revision they describe. Never persist keys, raw provider headers, or unredacted secrets in project data. Regenerate JSON Schemas after contract changes. |

**Model roles:** Any supported provider/model can be selected for planning and optional editorial review. The suggested Opus → Sonnet → Opus split can be offered as a configurable profile when those models are available. “Execution” means coordinating typed editor operations; the deterministic engine performs media changes. The default should use one selected model for planning and review so no second provider account is required.

**Provider inputs:** Start with text evidence: brief, asset IDs and media metadata, transcript words/segments with source IDs and integer timestamps, relevant scene/silence signals, current revision, and technical QA results. Raw video/audio stays local in this first iteration. Show the exact categories of data that leave the machine before the first request. Bound evidence size and paginate transcripts; never truncate citations without telling the model. Do not send absolute local paths.

**Tool boundary:** Allow only named, typed editor actions. Model text is untrusted data, never a shell command, URL fetch, FFmpeg filter, filesystem path, or direct SQLite write. The engine checks source hashes and path containment at I/O time. Every proposed edit is tied to a project ID and `expected_revision`. The model cannot claim that an operation succeeded; job and QA states come from persisted engine records.

**Credentials and reliability:** Store API keys in Windows OS credential storage, scoped per provider, and expose only set/remove/presence operations to the UI. Use provider request timeouts, bounded retries for transient transport failures, cancellation, rate-limit feedback, and redacted logs. Preserve the unsent user message and validated proposal on interruption. A provider outage must not damage project state or block manual editing/MCP use.

## Delivery sequence and acceptance gates

1. **Contracts and session persistence.** Define chat and provider abstractions, project-scoped session storage, redacted event logging, and revision-linked proposal lifecycle. Gate: restart preserves messages/proposals; secrets are absent from DB/logs; stale proposals cannot be committed.
2. **Provider connections.** Implement and independently test OpenAI, Gemini, and Claude adapters with recorded or fake HTTP responses, malformed outputs, timeouts, cancellation, and rate limits. Gate: each provider can return a locally validated typed plan from the same evidence fixture; no provider-specific data reaches the timeline engine.
3. **Planning and execution orchestration.** Reuse transcript/asset inspection, `submit_edit_plan`, revision validation/commit, render jobs, QA, and export. Add a bounded revise/retry loop and source-insufficient path. Gate: the same brief yields a reviewable source-cited proposal; no timeline revision changes before approval; successful saves create exactly one new revision.
4. **Native chat UI and settings.** Build conversation, progress, citations, warnings, diff, approval and preview controls; wire provider credentials through native commands. Keep browser Demo Mode visibly simulated. Gate: a native user can complete the flow without pasting JSON; keyboard and screen-reader paths work; status reflects persisted engine state.
5. **End-to-end verification.** Run engine/unit/component tests, a live native workflow with synthetic fixture media, and provider smoke tests only with explicitly configured credentials. Verify no source hash changes, revision conflicts, render output, QA measurement, and restart recovery. Gate: all three providers pass the same plan-validation scenario; document any unavailable live-provider checks honestly.

## Release boundary

This is a local feature plan, not approval for a public release. The existing public-release block remains in force until the separate licensing and release decisions are resolved.
