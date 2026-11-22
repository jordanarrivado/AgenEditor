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
- [x] **Voice-line requests.** `audio` request kind with the exact words in `line` (the prompt is the voice
  direction); a new `voice` asset role for audio-only voice-over files; track A4 for voice lines (no overlaps).
  The renderer mixes A4 into the speech bus, so music ducks under voice lines as it does under dialogue. The
  B-roll card copies the words plus direction and opens Google AI Studio (Gemini speech generation); a dropped
  file is imported as `voice`, labelled `generated-external`, and links only to an audio request. QA lists it
  as AI-generated and adds an editorial `voice_over_speaker` warning when a line plays over speaker audio that
  was not turned down. Schema change is additive (no version bump). *(2026-11-21. Covered by engine tests with
  real FFmpeg renders and by mocked component tests; not tried in the packaged app. Nothing checks that the
  generated speech says the requested words: the person is asked to listen before approving. Voice lines are
  not transcribed, so they get no captions yet.)*
- [x] **Brief import.** The client brief, script, reference note and a requirement checklist are stored in the
  project (versioned, optimistic lock) and written only by a person in the desktop app. MCP hosts read them with
  the read-only `get_brief` tool, and the handoff message tells agents not to mark requirements met. Final QA adds
  an editorial `brief_checklist` check: warn while any item is open, pass when a person has marked every item met
  or waived; it says plainly that nothing checked the video. *(2026-11-14. Not done: importing the reference
  *video* as media (it is a text note), and any machine check of a plan or render against the brief. Covered by
  engine tests including a real render, and mocked component tests; not tried in the packaged app.)*
- [x] **Asset review against the brief.** When approving a file the person can write a review note
  (`review_notes` on the checklist: request → asset ID and text). The note belongs to the exact approved file and
  is cleared when the file is reset, replaced or skipped; a re-plan keeps it only while the request keeps the same
  approved file. Agents read notes through `get_asset_checklist` and cannot write them; the Execute and Review
  stage prompts tell Claude to follow and check them. Skipping one card no longer clears other cards' accepted
  issues. *(2026-11-21. Engine tests (store, desktop bridge) and mocked component tests; not tried in the packaged
  app. The note is optional and free text: nothing checks the file, or the edit, against it.)*
- [x] **Variant matrix.** Variants (hook, language, localized shots) are stored as overrides on the shared main
  edit and computed from its current revision (spec §3.5): a replacement hook, overlay asset swaps, hook overlays,
  captions kept or off. Versioned, refused when they do not fit the main edit. Asset requests with `variant_id`
  gate only their variant. Variant renders pin both revisions and pass the same technical QA. MCP: `list_variants`,
  `create_variant`, `update_variant`, `get_variant`, `start_render(variant_id)`; desktop: read-only Variants panel.
  *(2026-11-21. Engine tests include a real render and QA of a variant whose hook changes the length; MCP round
  trip and mocked component tests. Not tried with a live agent or in the packaged app. No translated captions and
  variants cannot be deleted. Variant finals are delivered with the main edit by Save delivery… (see Delivery).)*
- [x] **Preview QA.** `run_preview_qa` measures a finished render (normally the preview) before the final render:
  format against its timeline, a frame grid (`frame_grid.png`, the middle frame of each clip, 6-12 frames) with flat
  black or single-colour frames flagged (`signalstats`), black bars where a clip should fill the frame
  (`cropdetect`; skipped where a clip uses `contain`), and captions shown while the render is silent
  (`silencedetect`). Warnings only, never a gate. Stored per job (`PreviewQAReport`); works for variant renders.
  The desktop app shows a Preview check under the preview with the frame grid; Execute and Review are told to run it.
  *(2026-11-21. Engine tests on real renders, including a crafted letterboxed/black/silent source; mocked component
  tests. Thresholds are first guesses not tuned on real footage. Caption sync measures sound, not words: music
  under a caption counts as sound, and early or late captions over continuous speech are not detected.)*
- [x] **Delivery.** Client file names and a known-issues note generated from the log. Save delivery… checks every
  final (main and variants) and saves those that pass into a new folder: `<Client>_<Project>_<Main|Variant>_<ratio>_
  <seconds>_r<rev>[v<variant rev>].mp4` (+ `.srt`) and `known_issues.md`, built by the engine (`delivery_plan`) from
  accepted known issues, QA checks that did not pass, the matching preview check and the AI-generated (external)
  labels. Renders without a QA report or with a failed one are left out and reported in the app, not in the
  client's note; files that fail to copy are listed under "Not saved". The client name is remembered per project.
  *(2026-11-22. Engine tests on real renders (main + variant, accepted issue on a generated shot); Rust tests for the
  folder naming and note; mocked component tests. The native folder picker and copy have not been tried in the
  packaged app. The note is in English only.)*
- [x] **Syllable-timing fallback** for languages with no word aligner. Before, a segment whose words came back
  without usable times was dropped from the transcript, and so were single untimed words. Now a run of untimed
  words is spread over the gap between its timed neighbours by estimated syllables, and a segment with no usable
  times has its words (or its text, split into characters or grapheme clusters for scripts without spaces) spread
  over the segment (`word_timing.py`). Such words are marked `timing: "estimated"` (aligned words keep the old wire
  format). Plan validation warns when a cut boundary is on an estimated word; QA adds an editorial
  `estimated_caption_timing` warning. *(2026-11-21. Tested with a fake recognizer, the real store and planner; not
  with a real non-Latin recording. Syllable counts are rough: vowel groups for Latin, Cyrillic and Greek, one per
  character for CJK, Hangul, Thai and similar.)*

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
- [~] MLT or native preview engine for interactive multitrack timeline playback. *(2026-11-21: a read-only
  multitrack timeline view (V1, V2, A1-A4, captions, drawn to scale from the current revision) under the preview;
  its playhead follows the rendered preview and clicking seeks it, only when the preview is of the shown revision.
  This is navigation over the render, not live multitrack playback: no MLT or native compositor was added, and
  changes still need a render to be seen.)*
- [~] OpenTimelineIO import/export, subject to adapter verification. *(2026-11-21: `otio.py` writes OTIO JSON
  (Timeline.1/Stack.1/Track.1/Clip.2/Gap.1/Transition.1) at the timeline frame rate with AgenEditor details in
  metadata, and imports OTIO whose media matches registered assets (by metadata, hash, path or unique file name) as a
  validated candidate. Desktop: Export .otio… and Import .otio… (a new revision; refused with the reasons when media
  is not registered). MCP: `export_otio`. Tests check structure, timing and the round trip; on 2026-11-22,
  the reference `opentimelineio` 0.18.1 library parsed an export and AgenEditor imported a timeline authored by
  that library. Premiere Pro/DaVinci Resolve adapter verification is still open.)*
- [~] Face-aware reframing using MediaPipe/other local vision models. *(2026-11-21: `suggest_reframe` (MCP) gives
  crop-centre suggestions for primary clips whose crop cuts the picture, from FFmpeg edge and motion energy on a
  64x36 thumbnail with a confidence score; the agent applies confident ones as `set_transform`. Tested on synthetic
  moving shapes and a noise frame. This is a detail/motion heuristic, not face detection: a local face model
  (MediaPipe or OpenCV) needs a new dependency that has not been added.)*
- [x] More subtitle templates, audio repair and color matching. *(2026-11-21: caption templates `top_clean`,
  `big_center` (bold, centred, heavier outline) and `lower_minimal`; `AudioMix.speech_repair` none/light/strong with
  fixed high-pass, FFT denoise and de-esser chains on the speech bus; `Timeline.broll_color_match` measures each
  B-roll clip and the primary footage under it with `signalstats` and moves the B-roll halfway toward it with a bounded
  `lutyuv` (luma gain 0.8-1.25, chroma shift ±12). Tested on real renders of synthetic sources; not tuned on real
  footage or checked by ear/eye by a person.)*
- [~] Packaged macOS and Linux builds and platform-specific hardware inference. *(2026-11-21: the desktop shell no
  longer hard-codes Windows paths (`.venv/bin`, executable suffixes, `open`/`xdg-open` for generator sites, Claude Code
  in `~/.local/bin`), the bundled-FFmpeg lookup uses the platform suffix, and an informational CI job runs the engine
  tests and compiles the shell on Ubuntu and macOS. Not done: no macOS or Linux package, installer or pinned FFmpeg
  build; saved chat keys are Windows-only; the CI job has not run yet; no platform-specific hardware inference.)*
- [~] Optional native AI chat and user-controlled model providers. *(2026-11-21, per docs/BUILT_IN_CHAT_PLAN.md:
  a "Your API key" mode beside Claude Code. Keys are stored in Windows Credential Manager and never returned to the
  page; the person allows chat per project after seeing what text is sent. Providers: Claude through the official
  `anthropic` SDK (default `claude-opus-5-5`, effort set, server-side refusal fallback), OpenAI Responses API and
  Gemini `generateContent` over HTTPS. The model gets bounded text evidence (instructions, file metadata without
  paths, up to 1,500 transcript words with IDs and times, the current cut) and may return an EditPlan; the engine
  validates it with `submit_edit_plan` (one retry with the reasons), stores a proposal pinned to the revision, and
  only "Apply" commits it as one user-authored revision; a proposal goes stale if the edit changed. Tested with
  recorded provider responses, a fake SDK client and an in-memory credential store. Not done: no live call to any
  provider has been made. On 2026-11-22 the desktop sidecar build was changed to include the `ai` extra, and its
  local executable was built and inspected for the `anthropic` SDK. A request and reply passed through the real SDK
  using a local mock HTTP transport; the full installer and UI were not exercised.
  The Windows credential code has not run against the real Credential Manager in tests, no streaming, and revision
  patches (as opposed to whole cut plans) are not offered in chat.)*
- [x] Advanced project management, batch exports and editor presets. *(2026-11-21: projects can be renamed and
  archived/unarchived (display only; nothing deleted). Workspace editor presets hold caption template, colour, B-roll
  colour match and audio mix; a person saves one from a project's current edit and picks one per project, and the
  agent applies the pick with `apply_editor_preset` (one revision patch). Saving finals together is now Delivery (Phase 2c):
  technical QA on the main final and each variant's latest final, then a new folder with client names and a note. Engine, Rust and mocked UI tests; the native folder picker and copy are not tried in the
  packaged app.)*

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
