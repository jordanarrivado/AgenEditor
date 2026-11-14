# AgenEditor — Implementation Roadmap

**Date:** 2026-11-10  
**Direction:** Local-first Windows desktop AI video editor + external-agent MCP support  
**Scheduling:** Milestone gates rather than calendar promises. Each phase finishes only when tests and acceptance criteria pass.

## Critical path

**Correct deterministic editing → MCP control → transcript-aware selection → short-form polish → desktop integration → beta distribution.**

Do not build the complete desktop UI before the editing/rendering pipeline works reliably. AgenEditor never generates media itself; external generation is limited to the prompt hand-off and import gate in spec §3.1.

## Phase 0 — Repository, contracts, reference fixtures (P0)

**Goal:** Ensure architecture and media test data are stable.

Tasks:
- [x] Initialize Python package, `uv`, Ruff, pytest, GitHub Actions on Windows. *(workflows written; not yet run on GitHub)*
- [x] Add `AGENTS.md`, security boundaries, initial project layout and license inventory.
- [x] Write schema v1 for `Asset`, `Timeline`, `RenderProfile`, `Job` and `QAReport`.
- [ ] Create synthetic fixture videos with speech-like audio and identifiable colored/slated visual sections; add consented real-source fixtures privately. *(synthetic fixtures done; consented real recordings do not exist yet)*
- [x] Define frame-rate rational timing conventions and source timestamp normalization. *(docs/TIMEBASE.md)*

**Deliverable:** Buildable repo, schemas, fixtures and CI.  
**Exit gate:** Unit tests pass; sample assets can be probed; reproducible commands documented.

## Phase 1 — Deterministic media engine + MCP (P0)

**Goal:** Let Codex or Claude Code edit **known ranges** of a video using typed MCP tools.

Tasks:
- [x] Implement project directories, immutable imports, hashing and FFprobe metadata.
- [x] Implement timeline create, validate, commit/version, diff and revision conflict detection.
- [x] Implement FFmpeg video/audio trim, timestamp reset, scaling/cropping, concat, H.264/AAC encoding.
- [x] Implement durable job records, progress, cancel and timeout/failure handling.
- [x] Implement technical QA (decode, resolution, duration, codec, audio track, hash preservation).
- [x] Implement v2 MCP stdio server with project/import/inspect/timeline/render/status/QA tools.
- [ ] Test MCP Inspector, Codex and Claude Code with the same sample project. *(2026-11-12: MCP Inspector CLI ran the Phase 1 exit gate through the shared service — 3 ranges → 40.000 s, 1080×1920, 30 fps, H.264/AAC, no technical QA failures; Claude Code read the same project correctly. Codex not tested: the local Codex config selects a model the account cannot use.)*

**Deliverable:** `final.mp4`, `timeline.json`, `qa.json` from a typed cut plan.  
**Exit gate:** Three valid ranges totaling 40 seconds produce 1080×1920, 30fps, H.264/AAC, playable MP4; no sources changed; works through MCP.  
**Priority:** Highest; block all later phases until stable.

## Phase 2 — Transcription and autonomous clip planning (P0)

**Goal:** Transform raw talking-head footage into a coherent agent-proposed selection without predetermined cuts.

Tasks:
- [x] Implement faster-whisper transcription, word timestamp mapping and language metadata.
- [x] Add VAD/silence candidates; never auto-delete all pauses blindly.
- [x] Build transcript segments and candidate hook scoring.
- [x] Provide transcript source IDs, line/word ranges, exact timestamps and context to external MCP host.
- [x] Add `submit_edit_plan` tool that validates agent-authored candidates with rationale, score and target duration. The agent chooses cuts; the engine does not invoke an undeclared model.
- [x] Require source-evidence validation and flag potential out-of-context splices.
- [x] Add agent retry cap, failure fallback and source-insufficient report.

**Deliverable:** Agent generates valid source-linked 40-second first-cut plan from a 5–20-minute recording.  
**Exit gate:** All proposed cuts are valid assets/ranges; human reviewer finds coherent narrative in majority of a consented reference set; no hallucinated dialogue or media.

## Phase 2b — Asset preparation hand-off (P1, added 2026-11-12)

Spec §3.1. Planner emits `asset_requests`; the UI turns them into a one-click checklist (ChatGPT default, Gemini
fallback for images, Google Flow for video); Execute is gated until every request is ready or dropped.

- [x] UI: role workflow Plan → Prepare assets → Execute → Review with copyable prompts and the asset gate (Demo Mode).
- [x] Contracts: `AssetProvenance` (`original` | `generated-external`, tool name) on registered assets; asset/QA schema v2 generated, v1 records still readable.
- [x] Engine/MCP: `list_assets` reports provenance; QA report lists generated assets used by the revision; import of generated files reuses the normal probe/hash path. *(Media integration test awaits FFmpeg on the test host.)*
- [~] Native UI: import generated files against a request and mark it ready; persist checklist per project.
  *(2026-11-13: request links and ready/dropped state persist in the project database with version checks. The engine blocks commit/render while requests are pending. PNG/JPEG stills can be imported and placed on V2. Native file picking works. 2026-11-14: dropping one file on a pending request card imports and links it; the native shell holds the dropped path, never the page. Compiles and is covered by a mocked component test; a real OS drag-and-drop in the running app is not yet verified.)*
  *(2026-11-14: A manual project-open attempt exposed missing checklist operations in the Tauri bridge allowlist. The allowlist and a bridge contract test are updated; project opening and a real file drop still need verification in a running desktop window.)*
- [ ] Google Flow: use an MCP connector if one becomes available; until then, copy-paste only.

**Gate:** no generation call leaves AgenEditor; every generated asset in an export is labelled and source-linked.

## Phase 2c — Brief-first, multi-variant flow (P1, proposed 2026-11-14)

Source: the "Video edit flow, revised" diagram. Built in dependency order; each slice ships with tests and
leaves the unbuilt steps marked as such. Nothing below changes the product rules: AgenEditor still only edits.

- [x] **Known-issue acceptance.** A linked file can be accepted with a required note; the known-issues log is
  part of the project checklist (`accepted`), cleared when the link is reset or dropped, and does not weaken the
  execute gate. Desktop-only on purpose: no MCP tool, so an agent cannot accept an issue for the user.
  *(2026-11-14. Covered by engine and mocked component tests; not tried in the packaged app.)*
- [ ] **Voice-line requests.** `audio` request kind and generated-audio import. Needs a new asset role for voice
  (A1 accepts only the primary speaker today, audio is music-only), render mixing and ducking rules, and a schema bump.
- [x] **Brief import.** The client brief, script, reference note and a requirement checklist are stored in the
  project (versioned, optimistic lock) and written only by a person in the desktop app. MCP hosts read them with
  the read-only `get_brief` tool, and the handoff message tells agents not to mark requirements met. Final QA adds
  an editorial `brief_checklist` check: warn while any item is open, pass when a person has marked every item met
  or waived; it says plainly that nothing checked the video. *(2026-11-14. Not done: importing the reference
  *video* as media (it is a text note), and any machine check of a plan or render against the brief. Covered by
  engine tests including a real render, and mocked component tests; not tried in the packaged app.)*
- [ ] **Asset review against the brief.** Per-request review notes before a file counts as ready.
- [ ] **Variant matrix.** Variants (hook, language, localized shots) as first-class timelines sharing a body;
  each further variant returns to the missing-assets check.
- [ ] **Preview QA.** Frame-grid, subtitle-sync and crop checks on the fast preview, before the final render.
- [ ] **Delivery.** Client file names and a known-issues note generated from the log.
- [ ] **Syllable-timing fallback** for languages with no word aligner.

## Phase 3 — Captions, B-roll, audio, revisions (P0)

**Goal:** Produce a genuinely reviewable 40-second Reel rather than merely a stitched rough cut.

Tasks:
- [x] Add output-time remapping from transcript words to edited segments.
- [x] Add two-line subtitle styles, safe margins, SRT export and manual word corrections.
- [x] Add supplied B-roll overlays; use user-provided shot labels initially; optional model indexing requires explicit privacy consent.
- [x] Add audio normalization, fades and configurable music ducking under speech.
- [x] Add gentle punch-in, crop controls and optional color preset; preserve original speech.
- [x] Add bounded revision patches and versioned undo/revert.
- [x] Add technical QA and human-facing editorial flags.
- [ ] Create golden test set of 10–20 talking-head records with accepted source ranges and known defects.

**Deliverable:** Autonomous preview edit with real captions, optional B-roll/music and versioned change requests.  
**Exit gate:** No fabricated media; captions map to spoken source; output passes technical checks; user can request at least one focused revision without re-ingesting or retranscribing source.

## Phase 4 — Native Windows desktop alpha (P1)

**Goal:** Make AgenEditor usable without a command-line user interface, while preserving MCP compatibility.

Tasks:
- [x] Bootstrap Tauri 2 + React + TypeScript + Vite + Tailwind.
- [x] Implement Project Library, Asset Bin, Agent Handoff panel (copy brief to Codex/Claude Code), Preview Player, Job Progress and Export. *(2026-11-14: the Cut Inspector became a read-only "Edit made by your AI agent" view. The external agent is the master editor and commits every revision through MCP; the app has no manual cut, overlay or plan-paste controls and shows agent revisions as they arrive. The browser Demo Mode still has its sample inspector. Also 2026-11-14: the live screen is organised as the basic flow, Step 1 import assets and instructions, Step 2 AI edit, Step 3 preview; the instructions are saved in the project and read by the agent through `get_brief`.)*
- [x] Add typed desktop IPC to engine sidecar; do **not** embed editing services inside React components.
- [x] Ensure a single authoritative engine state shared by desktop UI and MCP bridge.
- [x] Implement project-level revision conflicts, render version pinning and safe cancellation.
- [ ] Package Python engine and FFmpeg for Windows; test clean machine without Python/Node/FFmpeg preinstalled.
- [x] Implement a fast proxy-based preview and explicit “preview/render pending” states.

**Deliverable:** Installable Windows desktop alpha supporting the complete 40-second workflow.  
**Exit gate:** User imports, previews, revises and exports in the UI; Codex/Claude Code can work on the same project without data loss; installer passes clean Windows test.

## Phase 5 — Beta hardening and first public open-source release (P1)

**Goal:** Make first public release safe, observable and repeatable.

Tasks:
- [ ] Add comprehensive Windows GitHub Actions build/signing/release flow (signing if available). *(workflow, packaged smoke, notices and publication gate implemented; first remote run and signing remain unverified)*
- [x] Audit project path sandbox, symlink escapes, local IPC authentication and secret storage. *(docs/SECURITY_AUDIT.md: targeted audit, regression tests, `cargo deny`, installed-app review; a full penetration test is not claimed)*
- [x] Add crash recovery, disk-space preflight, orphaned-job cleanup and render log diagnostics.
- [ ] Add hardware capability detection and CPU fallback; benchmark low/mid/high PCs before publishing speed claims. *(detection + fallback done; low/mid/high benchmark outstanding)*
- [x] Confirm license obligations of bundled FFmpeg build, codecs, fonts and model weights. *(all decisions recorded; pinned GPL FFmpeg with source archive; OFL font; models are downloaded by the user, not bundled)*
- [x] Document app installation, model requirements, supported codecs and user privacy choices.
- [ ] Run benchmark dataset; report QA false positives, editor adjustments and known limitations.

**Deliverable:** Windows beta release with installation guide, sample workflows, reproducible CI and clear limitations.  
**Exit gate:** QA/installer/security checklist passes and a new user can complete the end-to-end tutorial unaided.

## Phase 6 — After MVP (P2)

Candidate features after usage evidence:
- [ ] MLT or native preview engine for interactive multitrack timeline playback.
- [ ] OpenTimelineIO import/export, subject to adapter verification.
- [ ] Face-aware reframing using MediaPipe/other local vision models.
- [ ] More subtitle templates, audio repair and color matching.
- [ ] Packaged macOS and Linux builds and platform-specific hardware inference.
- [ ] Optional native AI chat and user-controlled model providers.
- [ ] Advanced project management, batch exports and editor presets.

## Workstream dependencies

| Workstream | Depends on | Blocks |
|---|---|---|
| Project/assets + data contracts | Phase 0 | All rendering and analysis |
| FFmpeg renderer | Asset/timebase contracts | All editable final outputs |
| MCP adapter | Stable service contracts | External agent integration |
| Transcription | Asset/audio normalization | Semantic planning and captions |
| Autonomous plan | Transcript provenance and MCP | AI rough cuts |
| Captions/B-roll/audio | Versioned timeline | Polished reel and revision loop |
| Desktop UI | Stable editing engine/IPC contracts | Distributable desktop MVP |
| Installer and licensing | Real bundled engine/media dependencies | Public beta |

## Immediate issue backlog (suggested GitHub issues)

1. **ARCH-001:** Define canonical frame/sample timebase and source timestamp normalization.
2. **DATA-001:** Implement Pydantic asset and timeline schemas with schema versioning.
3. **ASSET-001:** Build secure import and FFprobe media registry.
4. **TIMELINE-001:** Validate and commit versioned cut plans with optimistic locks.
5. **RENDER-001:** FFmpeg trim/scale/concat H.264/AAC render with tested fixtures.
6. **JOBS-001:** Durable rendering job state, progress, cancel and crash handling.
7. **QA-001:** Automated output checks and human-review warnings.
8. **MCP-001:** MCP SDK v2 server with typed tool outputs and safe errors.
9. **MCP-002:** Codex and Claude Code end-to-end tests.
10. **ASR-001:** faster-whisper transcript and word mapping.
11. **PLAN-001:** Candidate selection and source-evidence validation.
12. **CAPTION-001:** Word-to-output timestamp mapper and subtitle rendering.
13. **BROLL-001:** Registered B-roll overlay and basic semantic matching.
14. **AUDIO-001:** Music ducking/loudness preset.
15. **REV-001:** Natural-language revision patch and timeline diff.
16. **DESKTOP-001:** Tauri + React shell and typed engine IPC.
17. **DESKTOP-002:** Workspace: import, cut list, proxy preview, export.
18. **RELEASE-001:** Windows sidecar/FFmpeg packaging and clean-machine test.
19. **SEC-001:** Project sandbox/privacy/IPC/license threat review.
20. **BENCH-001:** Golden editing dataset and measurable acceptance metrics.

## Definition of first useful MVP

A Windows user can install AgenEditor, import a talking-head source, direct Codex or Claude Code to select a coherent ~40-second Reel, inspect a source-grounded edit plan, preview the actual render, adjust captions or B-roll, request one revision, and export an MP4/SRT/edit-plan/QA bundle without AgenEditor generating synthetic media or modifying originals.

## Project management recommendation

- Use GitHub Milestones for Phases 0–5.
- Implement dependency-critical issues first; do not start frontend visual polish before the editing engine is verified.
- Require tests and example output for every media-processing pull request.
- Keep a `KNOWN_LIMITATIONS.md` and a per-release benchmark report.
- Run a fixed end-to-end reference project on CI where practical and on a clean Windows test device before each release.
