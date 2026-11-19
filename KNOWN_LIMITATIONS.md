# Known limitations

Kept current per release (roadmap project management recommendation).

## Engine - Phase One

* The desktop explicitly reviews and saves exports. The external MCP host controls its own behavior.
* Preview jobs use a maximum 720?1280 fast proxy; interactive frame-accurate playback is not implemented.
* Desktop MCP hosts must use `--shared-service`. Do not run a separate standalone MCP worker against
  the same active desktop workspace.
* VFR range lookup reads all packet timestamps into memory. Very long sources will need a streaming index.
* Rotation metadata is recorded by the probe, but rotated source acceptance has not been established with a
  fixture. Use review before publishing such footage.
* Only synthetic fixtures are included; consented real-source evaluation remains outstanding.
* Project license is not chosen (see `docs/LICENSE_INVENTORY.md`).
* MCP Inspector strict schema discovery and the MCP SDK stdio workflow passed. Codex accepted an isolated
  server configuration; Claude Code showed its project configuration as pending approval. A live editing
  session in those two hosts remains unverified.

## Engine - Phase Two

* The local speech model is an optional install and explicit download. The real worker completed on
  the synthetic fixture, which contains no dialogue, and returned zero words. Transcription accuracy
  and editorial coherence have not been evaluated on consented 5–20-minute spoken recordings.
* Hook scores are transparent deterministic hints, not semantic understanding. Word-gap silence
  candidates and mid-thought warnings require human or agent review.
* Plan submission validates citations and produces a candidate timeline but does not author or
  approve a plan. An external MCP agent must choose cuts; no undeclared model runs in the engine.
* The installer is CPU-only (no NVIDIA cuDNN is shipped). From source, CUDA transcription is opt-in
  with CPU fallback; real GPU performance and model-specific accuracy remain unverified.

## Engine - Phase Three

* Caption, B-roll, ducking, revision and QA behaviour is tested on synthetic fixtures with synthetic
  transcripts. Caption sync and readability have not been judged by people on consented real
  recordings, and the golden set of 10-20 talking-head records is still outstanding (no real media in
  the repository).
* B-roll suggestions are lexical matches between user shot labels and spoken words. Optional model
  indexing (which would need explicit privacy consent) is not implemented.
* Captions use the bundled Poppins font (SIL OFL 1.1) only; the packaged engine renders with it locally, but not yet on a clean Windows machine.
* B-roll and logo audio is ignored; only V1 dialogue, A1 dialogue overlays and A2 music are mixed.
* Loudness uses two-pass `loudnorm` (measure, then linear mode). QA reports the measured integrated
  loudness and true peak after AAC encoding; it does not re-encode to hit the target.
* After a V1 trim, move or removal, overlays move with the primary clip they started over; overlays on
  a removed clip are dropped (reported as warnings). Overlays that span a cut are not re-split.
* Editorial flags (jump cuts, framing, B-roll balance, uncertain words, hook timing) are heuristics for
  a human reviewer. They never claim a polished edit.

## Asset preparation

* Generated video B-roll can be imported with an external tool name and linked to a request. The checklist persists in the project database and blocks commit/render while configured requests are pending. Native import uses the file picker; dropping one file onto a pending request card also imports it, labelled with the tool name typed in the External tool field (the drop cannot tell which tool made the file). The drop path is covered by a mocked component test and `cargo check`; it has not been tried with a real OS drag in the running app, and dropped files expire after 60 seconds. The packaged app has not been exercised on a clean Windows machine.
* PNG and JPEG stills can be imported and placed on V2 for a chosen output duration. Other image formats and animated images are not supported. The FFmpeg-backed engine tests, including image render, passed on this machine on 2026-11-14 (full engine suite: 115 passed, 1 skipped for symlink rights); only synthetic fixtures were used.

* Main footage does not need speech or even an audio track. Video with no audio renders with a silent AAC track
  and QA reports loudness and true peak as unmeasurable (a warning, not a failure). Transcription, captions, hook
  scoring and word-based cut evidence need speech; for silent footage the agent picks cuts without transcript
  evidence, and AgenEditor cannot check those choices against any words. Tested only with one 10-second synthetic
  video-only fixture.
* The desktop app does not edit cuts, overlays or captions by hand: the external agent does, through MCP, and
  the app shows each new revision within about three seconds by polling. Without a connected agent there is no way
  to create a cut from the desktop. Revision changes made by the agent are not individually approved by the user in
  the app; the user's gates are the asset requests, the preview and final-render review, QA, and export. The
  polling and read-only view are tested with a mocked engine only, not against a live agent session.
* The client brief is text a person writes and confirms. The reference video is a note, not imported media.
  Agents read it through `get_brief`, but nothing verifies a plan or render against it; the QA `brief_checklist`
  check only reports what a person marked. It is tested with the real store and a real render, and with a mocked
  engine in the UI, not in the packaged app.
* Known-issue acceptance is recorded per request with a note and is tested with a mocked engine and the real store. Nothing yet reads the log: the delivery note, brief checklist, variants, voice-line requests and preview QA from the proposed flow (roadmap Phase 2c) are not built.

## Engine - Phase Five (beta hardening)

* The release workflow builds test artifacts; publication and draft-release creation are blocked by
  `docs/release-status.json`. GitHub Actions has not been run. Optional installer signing is configured
  but has not been tested with a certificate.
* The licence gate (`scripts/license_report.py --check`) passes; publication stays blocked until a person
  completes the redistribution review and the clean-machine, benchmark and tutorial evidence exists. The
  project licence is Apache-2.0. See `docs/RELEASE_CHECKLIST.md`.
* Hardware detection reports usable hardware encoders, but renders deliberately stay on libx264.
  CUDA transcription (source installs only) is opt-in (`AGENEDITOR_TRANSCRIBE_DEVICE`); the CPU
  fallback is tested with a simulated failure. A real CUDA run on Windows has not been verified.
* No speed or memory figures are published. The benchmark harness works on synthetic media; the
  consented real dataset and low/mid/high machine runs are outstanding.
* The disk preflight uses a generous size estimate plus a 256 MB reserve. It does not account for
  other processes filling the disk during a job.
* Desktop-service findings and tested resolutions are recorded in `docs/SECURITY_AUDIT.md`.
* Dependency notices cover Python, production npm dependencies and resolved Windows Rust crates.
  Missing license texts are listed in the generated inventory; redistribution review is still required.
* The native installer still needs clean-Windows and unaided-user acceptance tests.

## AgenEditor 2.0 - multi-clip production

* Gates B-E (batches, scoped teams, recovery, centralized QA, delivery) are covered by engine tests on
  synthetic fixtures only. The three-clip/two-team reference workflow has not been run on real footage or
  benchmarked. Per-batch measurement now exists (`production_metrics`: approved clips per hour, time to
  first reviewable clip, queue wait, attempt time, revision rounds, checkpoint reuse, cost per approved clip),
  but no real-hardware figure has been recorded. Machine pressure (CPU busy, memory, disk, full slots) is sampled
  every 30 s while the engine runs and kept 14 days; GPU load is not measured.
* Gate F: the Production workspace (Overview, Queue, Teams, Review, Resources, Costs, Delivery, Clients) is tested in
  browser Demo Mode with simulation data (Vitest + Playwright). In the native app it opens from the
  **Production** header button and calls the allowlisted `production_*` operations; a live end-to-end session
  in the packaged Windows app and a clean-machine install are still unverified.
* Live updates use a long-poll (`production_changes`, up to 25 s per request) through the existing desktop
  bridge, with a 15 s fallback poll. The Rust shell changes for 2.0 are allowlist entries only and have not
  been compiled on a machine with the Rust toolchain yet.
* The Clients view edits global and client instruction profiles (each save is a new revision; a stale save is
  refused) and binds projects to clients. Per-clip instructions can be added only while a clip is still
  unplanned, which is usually a short window unless the batch is paused. Style notes are either typed by a person
  or measured from a reference file (frame, frame rate, cuts and shot lengths, loudness; first 5 minutes only).
  Scene-cut detection uses a fixed FFmpeg scene threshold (0.30) that has not been tuned on real footage, and the
  measurement test needs FFmpeg, so it has not run on this machine.
* Resource defaults (2 teams, 1 render slot) are conservative guesses, not measured on any hardware.

## Static UI prototype

* Everything is Demo Mode: sample data, simulated playhead, simulated jobs, export configuration only.
* Not connected to the engine; the `ProjectRepository` seam is ready for an IPC implementation (Phase 4).
* Clip moves/trims via inspector fields, not drag. Transcript fixture has 12 lines.
* The UI uses frame-only clip ranges on the timeline grid; the engine contract distinguishes the asset's own grid
  (docs/TIMEBASE.md). Reconcile when wiring IPC.
