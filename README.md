# AgenEditor

Local-first, **editing-only** video editor. The Python engine supports known-range rendering and
local transcription with source-linked agent cut-plan validation. Projects retain immutable
imports and timeline revisions, durable jobs, technical QA and typed MCP stdio tools. The browser UI
is a separate Demo Mode prototype. The native Tauri alpha connects to the real shared engine.
Phase 5 adds hardening, diagnostic tools, dependency notices and release gates. Public release is
disabled at the owner's request; see [`docs/release-status.json`](docs/release-status.json).

Contributor guide: [`AGENTS.md`](AGENTS.md); roadmap: [`docs/AGENEDITOR_IMPLEMENTATION_ROADMAP.md`](docs/AGENEDITOR_IMPLEMENTATION_ROADMAP.md);
limitations: [`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md); security: [`docs/SECURITY_BOUNDARIES.md`](docs/SECURITY_BOUNDARIES.md).

Users: [install](docs/INSTALL.md) · [tutorial](docs/TUTORIAL.md) · [supported media](docs/SUPPORTED_MEDIA.md) · [privacy](docs/PRIVACY.md). Maintainers: [release checklist](docs/RELEASE_CHECKLIST.md) · [security audit](docs/SECURITY_AUDIT.md) · [benchmark](docs/BENCHMARK.md) · [licence report](docs/LICENSE_REPORT.md).

## Engine quick start

Python 3.12, uv, FFmpeg and FFprobe are required. FFmpeg/FFprobe may be supplied through
`AGENEDITOR_FFMPEG` and `AGENEDITOR_FFPROBE` if they are not on `PATH`.

```powershell
uv sync --locked
uv run ageneditor fixtures
uv run pytest
uv run python scripts/phase1_reference.py --workspace .local-projects
uv run ageneditor mcp --workspace .local-projects
```

The reference command uses three known ranges from the synthetic 60-second fixture. It creates a
project under `.local-projects/`, then prints the paths to `final.mp4`, `timeline.json`, and `qa.json`.
The MCP command is a stdio server for a client to launch, not a terminal UI. See
[`docs/MCP_PHASE1.md`](docs/MCP_PHASE1.md) for its tool sequence and host setup.

For local speech transcription, run `uv sync --locked --extra transcribe`, then explicitly download
one model with `uv run ageneditor cache-model tiny`. See
[`docs/MCP_PHASE2.md`](docs/MCP_PHASE2.md) for transcript paging and source-backed planning.

## Native Windows desktop

```powershell
npm ci
uv sync --locked --extra transcribe --group desktop
npm run desktop:dev
npm run desktop:build
```

Development builds need Rust, Windows C++ build tools, WebView2 and FFmpeg/FFprobe. The build packages
Python and FFmpeg in a per-user NSIS installer under `apps/desktop/src-tauri/target/release/bundle/nsis`.
Follow the [desktop tutorial](docs/TUTORIAL_DESKTOP.md). Clean-machine acceptance remains pending.

# Static UI prototype (v0.1, Demo Mode)

> **Demo Mode.** Projects, transcripts, timelines, QA results and jobs are sample data. The preview is a simulated playhead over CSS
> placeholder art. Export only previews settings — no file is rendered, written or offered for download. The Codex / Claude Code MCP
> connection is shown as *not connected*.

## Run

```bash
npm install
npm run dev            # http://localhost:5173
npm run build          # tsc (strict) + vite build
npm test               # Vitest unit + component tests
npx playwright install chromium   # once
npm run test:e2e       # Playwright: full demo flow + a11y/overflow checks + screenshots
```

`npm run test:e2e -- screenshots` regenerates the review screenshots in [`screenshots/`](screenshots/) (1440×900, 1920×1080, 1280×720,
compact 1100 px and narrow 900 px editor, plus every other screen).

## Screens and routes

| Route | Screen |
|---|---|
| `/` | Redirects to `/welcome` on first run, `/home` afterwards |
| `/welcome` | First-run welcome |
| `/home` | Dashboard |
| `/projects` | Library — search, status filter, sort, grid/list, rename, duplicate, archive |
| `/projects/new` | 4-step wizard with validation (defaults come from Settings) |
| `/projects/:id` | Project overview — brief, settings, assets, revisions, activity, agent brief |
| `/projects/:id/editor` | Editor workstation (`?rev=` selects a revision) |
| `/projects/:id/review` | Revision comparison, change diff, QA checklist, notes, approve in demo (`?rev=&base=`) |
| `/activity` | Simulation jobs (queued / running / completed / failed / canceled) |
| `/templates` | Editing-style and caption presets |
| `/settings` | General, editing defaults, Agent/MCP, privacy, about, Reset Demo Data |
| `/help` | Product philosophy, demo limitations, shortcuts |
| `*` | 404 recovery |

Global: `Ctrl+K` command palette, `?` shortcuts, Reset Demo Data in the left rail. Editor shortcuts are listed in-app (Help, or `?`).

## Architecture

```
src/
  app/            providers, router (lazy routes), AppDataContext (repo state + job simulator), SettingsContext
  services/       ProjectRepository interface  ← the integration seam
                  repositories.ts (the ONE place that picks the implementation)
  mocks/          fixtures.ts, MockProjectRepository (localStorage, falls back to memory)
  stores/         editorStore — transient editor UI state only (playhead, zoom, selection, panels, caption preview)
  hooks/          useProjectWorkspace (project-scoped data), useRevisionHistory (undo/redo), viewport helpers
  utils/          timecode, timelineGeometry, revisionDiff, validation, agentBrief — pure, unit-tested
  components/     ui/ primitives (Radix-based Modal, menus, tooltip), editor/, export/, projects/, common/
  features/       one folder per screen
```

* **Repository state** (projects, assets, revisions, jobs, notes) lives behind `ProjectRepository`. Screens never import the mock class.
* **Editor UI state** lives in `editorStore` and is not persisted.
* **Timeline** positions derive from integer output frames: `x = frames / fps * pixelsPerSecond`. One canonical `playheadFrames`, clamped
  to the revision length.
* Timeline edits (remove, duplicate, move, trim, fit/crop, track mute/hide/volume) go through `useRevisionHistory` → undo/redo, and are
  saved through the repository. They are demo operations, **not** source-accurate media edits.
* Design tokens live in `src/index.css` (`--bg-app`, `--accent`, `--track-*` …) and are exposed as Tailwind utilities (`bg-surface`,
  `text-fg-2`, `border-line` …). Dark is default; a light theme and reduced motion are in Settings.

## Integration seam (next step)

Implement `TauriProjectRepository implements ProjectRepository` over typed desktop IPC and return it from
`src/services/repositories.ts`. Method → planned command:

| Repository method | Desktop IPC |
|---|---|
| `listProjects` / `getProject` / `createProject` / `updateProject` | `project.list` / `project.get` / `project.create` / `project.update` |
| `listAssets`, `addAssetsToProject` | `asset.list`, native file dialog + `asset.import` |
| `listRevisions` / `getRevision` / `saveRevision` | `timeline.list` / `timeline.get` / `timeline.commit` (optimistic revision check) |
| `getTranscript` | `transcript.get` |
| `getQAItems` | `qa.get` |
| `createSimulationJob` | `render.preview.start` / `export.start` → persisted job id |
| `listJobs` / `updateJob` | `job.get` + job event subscription |
| `resetDemoData`, `createSampleRevision` | demo-only — remove for production |

The authoritative state will be the Python project services + SQLite. The React app must never spawn FFmpeg or own final project-file
writes. The MCP server is a separate adapter onto the same engine.

## Known limitations

* Clip moves/trims are made with numeric inspector fields (keyboard-accessible); there is no drag-to-move on the timeline.
* The transcript fixture has 12 utterances (the spec suggests ~25).
* Speed, preview quality, destination folder and file import are visibly disabled with an "engine integration" reason.
* Demo edits persist in this browser's `localStorage`; Reset Demo Data restores the fixtures.

## Licence

AgenEditor's own code is licensed under the [Apache License 2.0](LICENSE). Bundled third-party
components keep their own licences; see [docs/LICENSE_REPORT.md](docs/LICENSE_REPORT.md) and the
notices shipped with the installer.
