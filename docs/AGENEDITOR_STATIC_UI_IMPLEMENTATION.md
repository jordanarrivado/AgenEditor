# AgenEditor — Complete Static Desktop UI Implementation Specification

**Document type:** UI/UX architecture, page specifications, reusable components, mock-data contracts, and Codex implementation brief  
**Version:** 1.0  
**Date:** 2026-11-10  
**Product:** AgenEditor — local-first, editor-only AI video editor  
**Target:** Windows desktop first; responsive within a desktop application shell  
**Stack:** React + TypeScript + Vite + Tailwind CSS; Tauri 2 integration later  
**Build strategy:** **Complete the entire UI with local mock data and working UI-only interactions BEFORE integrating Python, FFmpeg, MCP, transcription, or Tauri sidecars.**

> **Status:** This document is a specification for UI implementation; it is not an implemented interface. All renders, transcripts, metadata, processing progress, and QA results shown in the first static build are explicit **demo data**, not real processing results. Never imply a mocked export is a finished video file.

## 0. Relationship to the other project specifications

This file complements:

- `AGENEDITOR_PRODUCT_SPEC.md` — the authoritative product vision, engineering principles, core engine, and MCP architecture.
- `AGENEDITOR_IMPLEMENTATION_ROADMAP.md` — engine-first production development and acceptance gates.

**Important planning distinction:** The product roadmap still requires a reliable deterministic editing engine before declaring an integrated release. This **UI-first static prototype** is a parallel visual/product-design workstream: it can be built and reviewed ahead of backend integration, but it cannot replace engine implementation or certify real editing capability.

**Non-negotiable product rule:** **AgenEditor edits user-supplied video/audio only.** No text-to-video, synthetic B-roll, voice generation, AI image generation, or stock-generation features appear in the app.

---

## 1. Goal, audience, and deliverable

### Goal

Implement a professional, visually consistent desktop application that convincingly demonstrates **every primary user journey** of AgenEditor with realistic fixtures and state-driven interactions. It should look and behave like an editing product rather than a landing page, while making it clear that the first phase is in **Demo Mode**.

### Reference scenario

A user has a 12-minute talking-head interview and 3 existing B-roll files, plus one music track. They want a **40-second 9:16 Instagram Reel** with a strong hook, accurate subtitles, supplied B-roll, cleaner pacing, balanced audio, and an export of `final.mp4`. The demo walks through this without actually accessing media-processing services.

### Deliverables for the static UI milestone

1. A working Vite/React/TypeScript project that can run in a normal browser during development.
2. All screens and overlays listed in Section 4, with one shared visual design system.
3. Deterministic mock projects, media assets, transcript data, timeline versions, render jobs, QA reports, and activity logs.
4. Navigation and stateful user interactions for the main workflow. A button must either work within mocked UI state or be clearly marked unavailable; no silent no-op controls.
5. Desktop-first layout with acceptable narrow-window fallbacks.
6. Storybook or a dedicated component-gallery route **if** practical; otherwise well-organized reusable components and screenshots for visual review.
7. Unit/component tests for major state changes and an end-to-end demo-flow test.
8. A clear seam for replacing the mock repository with real Tauri IPC and Python services later.

### Out of scope for static build

- Calling MCP, Codex, Claude Code, AI APIs, FFmpeg, FFprobe, or Python.
- Reading private local footage, generating real thumbnails, importing to engine storage, or extracting waveforms.
- Producing actual `.mp4`, `.srt`, or `.json` exports.
- Realtime NLE editing, drag-reorder with source-accurate trim, complex effects, or multicamera.
- Authentication, billing, collaboration, cloud upload, telemetry, and account management.

---

## 2. Product visual direction

### Visual personality

**Creative production workstation:** understated, cinematic, crisp, confident. Inspiration: professional post-production apps and modern developer tools, but avoid imitating any existing editor exactly.

Design adjectives: **precise, calm, modern, premium, tactile, information-dense but uncluttered**.

### Design tokens — default dark theme

| Token | Value | Usage |
|---|---|---|
| `--bg-app` | `#0B0F15` | Outer app backdrop |
| `--bg-sidebar` | `#10161F` | Navigation and dock panels |
| `--bg-surface` | `#151D29` | Primary cards and editing surfaces |
| `--bg-elevated` | `#1C2634` | Menus, dialog, selected surfaces |
| `--border` | `#2B3748` | Subtle panel divisions |
| `--text-primary` | `#F3F6FC` | Headings and main content |
| `--text-secondary` | `#A6B4C7` | Supporting content |
| `--text-muted` | `#76869B` | Hints and low-emphasis controls |
| `--accent` | `#84A9FF` | Selected tools, timeline playhead, primary actions |
| `--accent-hover` | `#A7C2FF` | Primary hover states |
| `--success` | `#4DC6A0` | Successful simulated QA state |
| `--warning` | `#F2BE6B` | Warning status |
| `--danger` | `#F07C82` | Errors and destructive actions |
| `--track-video` | `#5E88D7` | Video clips |
| `--track-broll` | `#5CABC8` | B-roll overlays |
| `--track-audio` | `#6BAE9B` | Voice and music |
| `--track-caption` | `#C5A56C` | Caption cues |

**Typography:** Inter or a similar readable open-source sans-serif. Use a monospaced system stack for timecodes and technical metadata. Typical desktop scale: 12–13px dense controls, 14px primary body, 18–22px section headings, 28–32px page headings. Do not rely on tiny text to fit content.

**Iconography:** `lucide-react`, with consistent 16px/18px/20px sizes.

**Shapes and effects:** 8–12px radii for cards/dialogs; 4–6px for compact controls; restrained elevation; thin borders; minimal gradients (logo / hero only). No flashy neon glow across the entire interface.

**Spacing:** 4px base unit; primary panel padding 16–20px; gaps 8/12/16/24px.

**Accessibility:** minimum WCAG AA text contrast, keyboard focus rings, accessible dialogs, labeled controls, semantic menu/buttons, reduced-motion support, and no status communicated by color alone.

### Brand lockup

- Wordmark: **AgenEditor** (exact spelling).
- Compact app mark: stylized cut-frame / playhead icon, not a robot illustration.
- Optional tagline in onboarding: **Your footage. Your vision. AI edited.**
- Status copy should consistently say **AI-assisted editing**, never *AI video generation*.

---

## 3. Information architecture and navigation

### Primary navigation

Persistent left rail on wider windows:

1. **Home** — projects dashboard.
2. **Projects** — searchable project library.
3. **Activity** — recent demo jobs, revisions, QA warnings.
4. **Templates** — static editing-style presets (not generative templates).
5. **Settings** — appearance, defaults, mock integrations, storage presentation.
6. **Help / About** — product philosophy, demo constraints, keyboard shortcuts.

**Primary CTA:** `New Project` at the top of the rail.

### Suggested routes

| Route | Screen | Context |
|---|---|---|
| `/` | Redirect to `/home` | App entry |
| `/welcome` | Welcome / first-run experience | First launch or demos |
| `/home` | Dashboard | Recent projects, quick starts |
| `/projects` | Project library | Search, filter, grid/list view |
| `/projects/new` | New Project wizard | Name, format, assets, goal |
| `/projects/:projectId` | Project overview | Brief, media, versions, recent work |
| `/projects/:projectId/editor` | Main editor workstation | Core editing screen |
| `/projects/:projectId/review` | Review / approval | Version comparison, QA, notes |
| `/activity` | Activity center | Mock jobs and event history |
| `/templates` | Editing templates / styles | Talking-head, podcast, UGC presets |
| `/settings` | Settings | Editing, preview, privacy, integrations |
| `/help` | Help / keyboard shortcuts | Docs and demo limitations |
| `*` | 404 / recovery screen | Friendly route recovery |

Use `react-router-dom` or equivalent stable routing. App navigation must not trigger a full-page reload. Persist selected theme, layout settings and last project view only if simple; fixtures should remain reproducible.

### Onboarding and empty states

Every content collection must have a designed **empty state**, a **loading placeholder** (mocked), an **error state**, and a **populated state**. Do not leave blank gray rectangles when content is missing.

---

## 4. Complete screen specification

### 4.1 Welcome / first-run screen

**Purpose:** Introduce the editing-only concept and start a demo.

**Layout:** Centered wordmark and dark cinematic hero; concise explanation; a row of three illustrated workflow steps; compact footer link to privacy and licensing overview.

**Content:**
- Headline: `Turn raw footage into a finished edit.`
- Subheadline: `Import your clips, give creative direction, and review an AI-assisted edit. No generated footage.`
- Primary: `Explore Demo Project` → editor workspace.
- Secondary: `Create Project` → wizard.
- Badge: `UI Prototype · Demo Mode`.

**Interactive states:** first-run, returning user, project-fixture unavailable.

### 4.2 Home dashboard

**Purpose:** Fast entry into current work.

**Layout:** Top bar (workspace title, search affordance, notifications, appearance/user menu placeholder); welcome heading; quick-action cards; recent projects table/cards; recent activity panel.

**Modules:**
- `Continue Editing` highlighted sample project.
- `New Project` CTA.
- Stat cards: projects, drafts awaiting review, demo jobs completed (explicitly sample values).
- Recent project rows with title, format, duration target, last-edited date, state.
- `Getting started` three-step guidance.

**Interactions:** launch sample, create mock project, open activity, filter recent projects.

### 4.3 Project library

**Purpose:** Manage all projects visually.

**Layout:** header + search + status filter + sort + grid/list toggle; thumbnail cards or detailed table.

**Project card:** title; sample thumbnail (original artwork / local placeholder); 9:16 or 16:9 tag; target duration; asset count; last updated; `Draft`, `Review`, or `Completed` status; overflow menu.

**Interactions:** search, status filter, sort by updated/name, toggle view, duplicate project mock, rename project mock, archive with confirmation. Deleting in Demo Mode only mutates fixture/UI state.

**Empty:** `No matching projects` with clear-filters action.

### 4.4 New Project wizard

**Purpose:** Lead user through creating an editing task.

**Steps:**

1. **Project details:** title, description, primary platform (`Instagram Reel`, `TikTok`, `YouTube Short`, `Custom`), aspect ratio (9:16 by default), target duration (40s default), frame rate (30 fps default).
2. **Media assets:** example drag-and-drop surface and `Choose sample assets` action. A *static-only* file picker may allow selecting names for UI display but must not imply bytes were registered or processed. Offer deterministic demo asset set.
3. **Editing brief:** large textarea with sample brief, `Strong hook`, `Clean captions`, `Use B-roll`, `Balance music` switches, style preset selection.
4. **Review:** summary of selections and clear `Create Demo Project` action.

**Validation:** required nonblank name; target 15–180 seconds for UI mock; valid aspect ratio; at least one mock primary-video asset; clear inline errors.

**Interactions:** Next/Back, preserve draft values, stepper, cancel confirmation, create project → project overview.

### 4.5 Project overview

**Purpose:** Show what exists before opening the editing workstation.

**Sections:** header with title/status; brief summary; asset summary; editable settings; timeline revisions preview; mock activity; prominent `Open Editor`, `Review Current Draft`, `Prepare AI Brief` actions.

**Interactions:** rename, update brief, browse asset list, open revision, copy editing brief.

### 4.6 Main editor workstation — most important screen

**Purpose:** Present everything needed to inspect, plan, preview, and refine the edit.

**Suggested layout at 1440×900:**

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│ Titlebar: AgenEditor / Project name    Saved · Demo    Undo Redo  Export   │
├───────────────┬───────────────────────────────────────┬─────────────────────┤
│ LEFT DOCK     │ CENTER PREVIEW                        │ RIGHT INSPECTOR     │
│ [Media]       │                                       │ Clip / Caption /    │
│ [Transcript]  │      ┌─────────────────────┐          │ Audio / Project     │
│ [Brief]       │      │    9:16 preview     │          │                     │
│ [Agent]       │      │    placeholder      │          │ Selected clip data  │
│               │      └─────────────────────┘          │ Inspector controls │
│ Asset cards   │     ◀  ▶  00:12:16 / 00:40:00        │                     │
├───────────────┴───────────────────────────────────────┴─────────────────────┤
│ TIMELINE HEADER: zoom - +    snap  versions     playhead  00:12:16         │
│ Time ruler  00:00      00:10          00:20        00:30        00:40        │
│ V2 B-ROLL       [-----]                 [---]                         ... │
│ V1 TALKING HEAD [========][===========][===============][============]     │
│ A1 DIALOGUE     [====================================================]     │
│ A2 MUSIC        [----------------------------------------------------]     │
│ T1 CAPTIONS     [---] [---] [---] [---] [---] [---] [---] [---]            │
├─────────────────────────────────────────────────────────────────────────────┤
│ Status: Demo timeline · No actual rendering connected     Jobs  QA        │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### Editor top bar
- Breadcrumb `Projects / Project Name`.
- Current revision selector (`v1`, `v2`, ...).
- Read-only `Demo Mode` marker.
- Undo/Redo controls for mock UI edit operations.
- `Review` and `Export` buttons; export opens a preview **configuration dialog only**.
- Save indicator must say `Demo changes saved locally` only if actually persisted. Otherwise say `Unsaved demo session`.

#### Left dock / resizable panes
Tabs:
- **Media** — asset list with type filter, sample thumbnails, durations, import placeholder and selected metadata.
- **Transcript** — timed transcript blocks, search/highlight, speaker labels, and `Jump to source` simulation.
- **Brief** — original editing goal and style settings; editable demo text.
- **Agent** — `Prepare handoff` panel: brief preview, MCP readiness as **Not connected**, `Copy instructions`, demo activity log. No fake live agent responses.

#### Center preview
- 9:16 stage with a provided static poster or pre-bundled permitted demo fixture. Never pretend a static image is a playable rendered cut.
- Overlay timecode, canvas safe-zone toggle, framing guides toggle, caption-style preview, and video transport controls.
- If there is no playable sample, `Play` toggles an animated demo playhead only with visible label `Simulated timeline preview`; no misleading audio/video playback claim.
- Optional `<video>` preview only when an actual local/licensed sample MP4 is included in the frontend assets and mapped correctly.
- Quick settings: preview resolution display, aspect ratio label, fit-to-screen.

#### Right inspector
Context-dependent panels:
- **Project:** target duration, aspect ratio, FPS and current revision.
- **Video clip:** asset name, source in/out, timeline start/end, trim values, crop position, fit mode, speed (show unsupported settings disabled).
- **B-roll:** media ID, overlay position/duration, visibility toggle, why used (mock rationale).
- **Captions:** sample text, font size, position, color, safe-margin guidelines, template selector.
- **Audio:** track volume slider, mute/solo UI, fades display, target LUFS label (*informational, not measured*).
- **No selection:** `Select a clip to inspect its details.`

#### Bottom timeline
- Scrollable horizontal tracks with sticky labels; ruler and vertical playhead.
- Tracks: `V2 B-roll`, `V1 Main Video`, `A1 Dialogue`, `A2 Music`, `T1 Captions`.
- Visual clip blocks use appropriate track colors and include short names, trimmed edges and source-ID indicators.
- Zoom controls, fit-to-window, snapping toggle, playhead movement by ruler click, clip selection, contextual toolbar.
- Clip dragging can be **visual only within mock state** if implemented reliably; it must not suggest source-faithful media operations. A click-to-select plus inspector editing is preferable to broken DnD.
- Context menu: `Inspect`, `Duplicate in demo timeline`, `Remove from demo timeline`, `Copy clip info`.
- Keyboard: Space toggles demo playhead; Left/Right moves mock playhead; `+`/`-` zoom; Delete removes selected simulated clip after confirmation or undo support; Escape dismisses overlays.

#### Editor empty states
- Project without assets → import/choose sample CTA.
- No timeline revision → `Create sample edit plan` CTA.
- No selected clip → inspector helper.
- Agent not connected → setup instructions, not an error pretending a missing render.

### 4.7 Agent handoff drawer / dialog

**Purpose:** Make Option A (Codex/Claude Code via MCP) visible in the product design, even while disconnected.

**Sections:**
- Selected project and current goal.
- Short explanation of required MCP connection.
- Client selector: Codex / Claude Code.
- Generated **text-only** handoff brief preview.
- Copy-to-clipboard, save as in-memory draft, and `View connection instructions`.
- Connection state: `Not connected in UI prototype`.
- Tool capabilities cards: Asset inspection, Timeline edits, Preview render, QA (all shown as planned/unavailable).

**Avoid:** a fake `Connect` button that looks successful, fabricated Codex chat responses, or claims that MCP tools are currently running.

### 4.8 Review and approval

**Purpose:** Give the user confidence before finalization.

**Layout:** version title, side-by-side (or tabbed) `v1 / v2` preview posters, revision timeline, change summary, QA panel, issue notes, approval CTA.

**Mock QA items:** output dimensions, codec, duration, loudness, caption alignment, black frames. Distinguish `Sample result` from `Not tested`.

**Interactions:** change revisions, expand QA details, add/remove review notes, mark `Needs revision` or `Approved in demo`, open selected revision in editor.

### 4.9 Export dialog and delivery screen

**Purpose:** Design the future export experience without creating fake files.

**Export modal fields:** filename, destination placeholder, output preset (`Instagram Reel 1080×1920`, `YouTube Shorts`, `Custom`), codec (H.264 display), FPS, quality (`Standard` / `High`), captions (`Burned-in`, `Sidecar`, `Both`), optional QA-before-export toggle.

**Static-only action:** `Preview Export Settings` → confirmation sheet showing proposed parameters; optional simulated job placed into the demo Activity queue clearly labeled `Simulation`. Do **not** create fake links to `final.mp4`.

**Delivery view:** show what a successful export would eventually contain (`final.mp4`, `captions.srt`, `timeline.json`, `qa_report.json`) as *planned output artifacts*, not downloadable files.

### 4.10 Activity / job center

**Purpose:** Show queued, running, completed, failed and canceled job presentation states.

**UI:** filterable table/cards of jobs with service name, project, start time, status, sample progress bar, error details, `View project`, retry/cancel **simulation** actions.

**States:** no jobs, running demo, completed demo, failed demo. Simulations have deterministic progress and never claim actual rendering.

### 4.11 Editing templates / presets

**Purpose:** Let users choose a creative *editing style* applied to existing footage.

Templates:
- `Clean Talking Head` — minimal captions and focused framing.
- `Podcast Highlight` — punchy clip selection and high-legibility captions.
- `Educational Explainer` — clean structure and on-screen supporting B-roll.
- `UGC Product Story` — user-supplied product footage, simple pacing and text overlays.

Each card includes: title, sample layout, caption styling, pacing description, compatible formats, `Apply to demo brief`.

**Avoid:** generation features, animated AI imagery promises, template-store billing.

### 4.12 Settings

Tabs/sections:
- **General:** theme (`Dark` initially), default project folder *display-only*, language, reduced motion, timezone display.
- **Editing defaults:** default 9:16 / 40 seconds / 30 fps, caption preset, preview quality, snapping default.
- **Agent / MCP:** Codex vs Claude Code setup instructions, disconnected badge, sample config snippets, MCP permissions explanatory text.
- **Privacy:** local-first explanation, future external-model data disclosure, mock toggle for future review of external sharing.
- **About:** version `UI Prototype`, license notes, keyboard shortcuts and docs links.

Settings should either change UI behavior immediately (theme, defaults, reduced motion) or be visibly labeled `Available after engine integration`.

### 4.13 Help and system states

- Shortcuts overlay (`?`).
- Import/asset permission explainer.
- Unsaved changes dialog.
- Confirm archive/remove dialog.
- Toasts for successful UI-only actions.
- Error boundary / “Something went wrong” recovery.
- 404 screen with `Return Home`.
- `Demo Mode` information popover.

---

## 5. Reusable component inventory

Build reusable primitives rather than implementing every screen separately.

### Shell and navigation
- `AppShell`, `AppTitlebar`, `SidebarNavigation`, `TopToolbar`, `WorkspaceBreadcrumbs`, `StatusBar`, `ResizablePanel`, `PanelTabs`, `KeyboardShortcutsDialog`.

### Common components
- `Button`, `IconButton`, `Badge`, `Input`, `Textarea`, `Select`, `Switch`, `Checkbox`, `Slider`, `Tooltip`, `Popover`, `Modal`, `DropdownMenu`, `ContextMenu`, `Toast`, `Skeleton`, `EmptyState`, `ErrorState`, `ConfirmDialog`.
- Prefer accessible primitives from **Radix UI / shadcn/ui** to avoid reimplementing keyboard/focus mechanics; own styling tokens and avoid unnecessary third-party widgets.

### Project workflow
- `ProjectCard`, `ProjectTable`, `ProjectStatusBadge`, `NewProjectStepper`, `AssetDropzoneMock`, `AssetCard`, `AssetList`, `ProjectSettingsSummary`, `RecentActivityFeed`.

### Editing workspace
- `EditorShell`, `MediaBin`, `TranscriptPanel`, `EditingBriefPanel`, `AgentHandoffPanel`, `PreviewStage`, `PreviewControls`, `SafeZoneOverlay`, `Timeline`, `TimeRuler`, `TimelineTrack`, `TimelineClip`, `TimelinePlayhead`, `TimelineZoomControls`, `InspectorPanel`, `ClipInspector`, `CaptionInspector`, `AudioInspector`, `VersionSwitcher`.

### Review and export
- `ReviewWorkspace`, `RevisionDiff`, `QAReportPanel`, `ReviewNotes`, `ExportDialog`, `ExportPresetCard`, `JobStatusCard`, `JobProgressPanel`.

### Design requirements
- One source of truth for selected project, current version, playhead, selected clip, and active panels.
- Use memoized selectors for computed timeline positions; no global mutable arrays.
- Do not encode app behavior inside purely presentational components.
- Accessible names for icon-only buttons.
- Design tokens, not one-off hardcoded Tailwind hex values throughout components.

---

## 6. Desktop layout and adaptive behavior

### Baseline target

- Design and screenshot target: **1440×900** desktop viewport.
- Recommended editing minimum: **1280×720**. Below this width, keep essential controls usable by collapsing panels.
- Support 1920×1080 and common HiDPI scales without blurry text or clipped dialogs.

### Suggested editor layout defaults

| Region | Initial width/height | Minimum / behavior |
|---|---|---|
| Navigation rail | 64px compact icons | Always accessible; labels in tooltip |
| Left media dock | 280px | 220–380px; collapsible |
| Right inspector | 300px | 250–380px; collapsible |
| Center preview | Remaining space | Maintain preview aspect ratio |
| Bottom timeline | 260px height | 180–440px; vertically resizable |
| Top toolbar | 52px | Sticky |
| Status bar | 28px | Sticky |

### Breakpoint behavior

- **≥1600px:** left and right panels open; rich preview/timeline.
- **1280–1599px:** default layout; panels resizable.
- **960–1279px:** icon navigation, inspector collapses into drawer; preview above timeline.
- **<960px:** narrow-window safe layout; use tabs to switch `Preview / Timeline / Inspector`. Display `Best experienced at 1280px+` note, without blocking the user.

Keyboard navigation and command accessibility matter more than mobile phone responsiveness; this is a desktop product.

---

## 7. Mock data models (frontend-only)

Use typed fixtures and a single repository interface. Avoid scattering constants across pages.

```ts
export type ProjectStatus = 'draft' | 'review' | 'approved';
export type MediaKind = 'video' | 'audio' | 'image' | 'subtitle';
export type TrackKind = 'video' | 'broll' | 'dialogue' | 'music' | 'captions';
export type JobStatus = 'queued' | 'running' | 'completed' | 'failed' | 'canceled';

export interface DemoProject {
  id: string;
  name: string;
  description: string;
  status: ProjectStatus;
  platform: 'instagram' | 'tiktok' | 'youtube' | 'custom';
  targetDurationFrames: number;  // 1200 frames = 40s at 30 fps
  fpsNum: number;
  fpsDen: number;
  width: number;
  height: number;
  brief: string;
  assetIds: string[];
  activeRevisionId: string | null;
  updatedAtIso: string;
}

export interface DemoAsset {
  id: string;
  projectId: string;
  kind: MediaKind;
  name: string;
  durationFrames?: number;
  frameRateNum?: number;
  frameRateDen?: number;
  width?: number;
  height?: number;
  thumbnailSrc?: string;  // bundled local demo asset; no remote requirement
  description?: string;
  isMock: true;
}

export interface DemoTimelineClip {
  id: string;
  trackId: string;
  assetId: string;
  timelineInFrames: number;
  durationFrames: number;
  sourceInFrames: number;
  sourceOutFrames: number;
  label?: string;
  rationale?: string;
}

export interface DemoTimelineTrack {
  id: string;
  type: TrackKind;
  label: string;
  clips: DemoTimelineClip[];
  muted?: boolean;
  hidden?: boolean;
}

export interface DemoRevision {
  id: string;
  projectId: string;
  revisionNumber: number;
  createdAtIso: string;
  durationFrames: number;
  tracks: DemoTimelineTrack[];
  notes: string;
}

export interface DemoJob {
  id: string;
  projectId: string;
  kind: 'analyze' | 'preview' | 'export' | 'qa';
  status: JobStatus;
  progressPercent: number;
  isSimulation: true;
  message: string;
}

export interface DemoQAItem {
  id: string;
  title: string;
  state: 'sample_pass' | 'sample_warning' | 'untested';
  detail: string;
}
```

**Time convention:** UI timeline uses integer **output frames** for a constant-frame-rate demo sequence. Actual engine-side source time normalization must follow the product spec and handle variable-frame-rate media. UI mocks must not accidentally establish false frame-accuracy guarantees.

### Deterministic fixture set

Create `src/mocks/fixtures.ts` including:

1. `reel_001` — *The Psychology of Habits*, 40s vertical, transcript present, five timeline tracks, three B-roll segments, two revisions, sample QA warnings.
2. `reel_002` — *Product Story UGC*, 35s vertical, draft, no preview yet.
3. `reel_003` — *Podcast Highlight*, 55s vertical, approved in demo.
4. Eight video assets, two audio assets, two subtitle fixtures, including asset types with informative filenames.
5. A transcript with ~25 timestamped utterances and at least one searchable phrase.
6. Demo activity history with queued/running/completed/failed sample statuses.
7. At least two caption presets and four editing-style presets.

Fixture dates should be stable and do not imply actual user projects. Bundled placeholder thumbnails must be user-created, permissively licensed, or CSS-generated. Avoid remote stock photos and unlicensed footage.

### Mock repository abstraction

```ts
export interface ProjectRepository {
  listProjects(): Promise<DemoProject[]>;
  getProject(projectId: string): Promise<DemoProject>;
  createProject(input: NewProjectInput): Promise<DemoProject>;
  updateProject(projectId: string, patch: Partial<DemoProject>): Promise<DemoProject>;
  listAssets(projectId: string): Promise<DemoAsset[]>;
  getRevision(projectId: string, revisionId: string): Promise<DemoRevision>;
  saveDemoRevision(projectId: string, draft: DemoRevision): Promise<DemoRevision>;
  listJobs(projectId?: string): Promise<DemoJob[]>;
}
```

Implement `MockProjectRepository` using in-memory state or isolated `localStorage` with a `Reset Demo Data` action. Avoid pretending this is production persistence. Later create `TauriProjectRepository` to call typed desktop commands. **Screens should depend on the interface, not the mock class.**

---

## 8. Detailed interaction matrix

Every visible action should have a defined effect, including in Demo Mode.

| User action | Static UI behavior | Production replacement |
|---|---|---|
| New Project | Creates mock project and navigates to overview | Project service / filesystem |
| Select demo assets | Adds fixture assets to project state | Native dialog + ingest worker |
| Search projects | Filters mock projects immediately | Indexed project query |
| Open Editor | Navigates to project's active demo revision | Load real timeline |
| Select clip | Highlights block and populates inspector | Timeline selection |
| Change caption style | Updates example caption overlay | Caption style engine |
| Move playhead | Updates timecode and indicator | Rendered preview seek |
| Change zoom | Rescales timeline blocks | Timeline view transform |
| Edit brief | Updates mock project brief | Project record |
| Prepare agent brief | Copies source-linked demo prompt; marks MCP disconnected | MCP agent handoff |
| Request simulated preview | Creates labeled simulation job | Render worker |
| Compare revisions | Switches demo revision views | Real revision storage |
| Add review note | Updates notes state | Review service |
| Approve in demo | Updates fixture/project status with label | Approval workflow |
| Open export | Opens settings modal | Export job request |
| Confirm mock export | Shows **configuration preview**, no output file | Actual render + QA |
| Change theme | Changes current UI theme | Persisted user settings |
| Reset Demo Data | Restores fixtures after confirmation | Never a production action |

If an interaction cannot be safely simulated, disable it visibly with a tooltip explaining `Requires media engine integration`.

---

## 9. Frontend technical architecture

### Recommended dependencies

**Runtime:**
- `react`, `react-dom`, `typescript`, `vite`
- `react-router-dom`
- `tailwindcss` with the official Vite integration matching the installed Tailwind major version
- `lucide-react`
- `zustand` for selection, pane visibility, playhead, mock timeline state
- `@tanstack/react-query` for repository-style queries and cache invalidation (optional for the first few screens, recommended before full app)
- Radix UI primitives or shadcn/ui components for menus, dialogs, sliders and accessible interaction
- `react-hook-form` + `zod` for wizard/settings validation (optional but recommended)

**Dev/test:**
- Vitest + React Testing Library
- Playwright for the 1440×900 walkthrough
- ESLint + TypeScript strict mode
- Prettier (consistent formatting)

Do not add Tauri APIs yet. Build and test as a browser SPA, then wrap in Tauri later. Keep IPC boundaries at the repository/service interface.

### Suggested source tree

```text
apps/desktop-ui/
├── index.html
├── package.json
├── vite.config.ts
├── tsconfig.json
├── public/
│   └── demo/                   # Original/licensed demo assets, if any
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── app/
│   │   ├── router.tsx
│   │   ├── providers.tsx
│   │   └── AppShell.tsx
│   ├── components/
│   │   ├── ui/                  # Accessible primitives
│   │   ├── navigation/
│   │   ├── projects/
│   │   ├── assets/
│   │   ├── editor/
│   │   ├── review/
│   │   └── export/
│   ├── features/
│   │   ├── dashboard/
│   │   ├── project-library/
│   │   ├── project-creation/
│   │   ├── editor-workspace/
│   │   ├── agent-handoff/
│   │   ├── activity/
│   │   ├── templates/
│   │   └── settings/
│   ├── hooks/
│   ├── mocks/
│   │   ├── fixtures.ts
│   │   ├── mockProjectRepository.ts
│   │   └── demoScenarios.ts
│   ├── services/
│   │   ├── projectRepository.ts
│   │   └── repositories.ts
│   ├── stores/
│   │   ├── uiStore.ts
│   │   └── editorStore.ts
│   ├── types/
│   │   ├── project.ts
│   │   ├── timeline.ts
│   │   └── job.ts
│   ├── utils/
│   │   ├── timecode.ts
│   │   ├── timelineGeometry.ts
│   │   └── validation.ts
│   └── styles/
│       ├── theme.css
│       └── globals.css
├── tests/
│   └── components/
└── e2e/
    └── demo-workflow.spec.ts
```

### State boundaries

- **Repository state** (projects, assets, versions, jobs): mock repository + query layer.
- **Editor UI state** (playhead, zoom, selected clip, open panels, active inspector tab): Zustand, not persisted by default.
- **Dialog/form state**: local state / React Hook Form.
- **Theme and simple user preferences**: persistent UI settings with reset action.

**Do not** duplicate the full project/revision object in multiple Zustand stores and query caches. The repository provides project data; stores provide transient UI controls.

---

## 10. Timeline UI geometry and deterministic behavior

Use CSS or SVG positioning derived from integer frame data:

```ts
const xPx = (timelineInFrames / outputFps) * pixelsPerSecond;
const widthPx = (durationFrames / outputFps) * pixelsPerSecond;
const playheadPx = (playheadFrames / outputFps) * pixelsPerSecond;
```

- Keep a single canonical `playheadFrames` in the editor store.
- Clamp playhead to `[0, revision.durationFrames]`.
- Use output-fps rational conversion helpers; for the 30 fps fixture, the simple formula above is sufficient.
- `Fit timeline` computes `pixelsPerSecond` from viewport width and 40-second duration.
- Track row height 36–44px; separate track labels from scrollable clips.
- Pointer selection must not interfere with vertical pane resizing.
- Visual playhead / hover lines must remain aligned when horizontal scrolling.
- Set a performance target of smooth scrolling with 100–200 simulated clips; virtualize only if profiling demonstrates a need.

**Demo transport:** If a playable demo video is not bundled, transport buttons modify timecode and selected position **only**. A visible `Simulated timeline — no rendered preview` label must remain near the stage.

---

## 11. Accessibility and keyboard interactions

| Shortcut | Behavior in UI prototype |
|---|---|
| `Ctrl/Cmd + K` | Open app command palette or project search |
| `Space` | Toggle simulated playhead when editor is focused (not in text input) |
| `← / →` | Move playhead by one frame (or chosen step with modifier) |
| `+ / -` | Zoom timeline |
| `Home / End` | Start/end of demo timeline |
| `Ctrl/Cmd + Z` | Undo mock edit if implementation supports undo history |
| `Ctrl/Cmd + Shift + Z` | Redo mock edit |
| `Delete` | Remove selected mock clip only with undo/confirmation |
| `Escape` | Close dialog/menu/selection context |
| `?` | Open shortcuts help |

- All dialogs trap focus and restore focus to triggering control.
- Sidebar, transcript and timeline must be keyboard discoverable.
- Avoid implementing a drag-only feature without a keyboard alternative.
- Use `aria-label` for controls that show only icons.
- Honor `prefers-reduced-motion` for panel and playback simulations.

---

## 12. UI implementation roadmap (static-first)

### UI-0 — Bootstrap and foundations (P0)

- [ ] Scaffold Vite + React + TypeScript in `apps/desktop-ui`.
- [ ] Configure Tailwind and design-token theme.
- [ ] Implement router, global shell, theme, icon system, typography, toasts and error boundary.
- [ ] Create mock types, fixture repository and query/provider abstractions.
- [ ] Create reusable buttons, form fields, cards, menus, tabs, dialogs and empty states.

**Exit:** app starts, common shell and navigation work, all routes show a purposeful placeholder rather than blank screen.

### UI-1 — Projects and onboarding (P0)

- [ ] Welcome, Home, Projects grid/list, New Project wizard, Project Overview.
- [ ] Search/filter/sort, create/edit/duplicate/archive mock project, validation.
- [ ] Settings defaults influence wizard initial selections.

**Exit:** user can create a complete **demo** project and open its overview, without page reload or broken state.

### UI-2 — Editing workstation (P0; highest UX priority)

- [ ] Three-pane editor workspace and resizable timeline dock.
- [ ] Media bin, transcript list, brief, agent handoff.
- [ ] Portrait preview stage with accurate demo-mode labeling.
- [ ] Track ruler, clip positioning, selection, playhead, zoom and inspector tabs.
- [ ] Caption-style demo overlay, audio slider UI, revision selector.
- [ ] Responsive panel collapse and keyboard shortcuts.

**Exit:** the sample 40-second project can be inspected end to end using demo clips, no dead UI controls, screenshot looks like a professional editing workspace at 1440×900.

### UI-3 — Review, export and job system (P1)

- [ ] Review workspace, QA sample cards, revision selection/diff, notes and approve-in-demo.
- [ ] Export configuration modal and planned-artifact summary.
- [ ] Activity list and clearly labeled deterministic simulation states.

**Exit:** user completes the mock project flow from initial brief to simulated delivery summary; no fictitious downloadable MP4.

### UI-4 — Polish and readiness for integration (P1)

- [ ] Add Templates, Help, About, complete Settings.
- [ ] Implement all empty/loading/error visual states.
- [ ] Add accessible focus, keyboard, reduced-motion and responsive tests.
- [ ] Add Playwright flow and visual screenshots for wide/compact editor.
- [ ] Audit data boundaries: UI never imports direct Tauri/FFmpeg/AI calls.

**Exit:** complete static frontend can be handed over to engine-integration work with minimal screen redesign.

---

## 13. Testing and visual acceptance checklist

### Functional UI acceptance

- [ ] Every route can be opened directly (including 404 recovery).
- [ ] User can start from Welcome → Home → New Project → Editor → Review → Export configuration.
- [ ] Search/filter/sort and grid/list toggle change visible results.
- [ ] New Project wizard validates invalid input and preserves correct input.
- [ ] Selecting a timeline clip updates the inspector.
- [ ] Playhead controls, zoom, track visibility and panels update mock state.
- [ ] Transcript search and source-jump change highlighted selection.
- [ ] Agent handoff copies a correct, labeled example brief with `Demo Mode` disclaimer.
- [ ] Mock job simulations are always explicitly marked simulations.
- [ ] Export dialog never claims to have rendered or downloaded real files.
- [ ] Demo reset restores deterministic fixtures.
- [ ] Navigation and dialogs are keyboard-accessible.

### Visual acceptance

- [ ] Consistent brand, color, radius, spacing and icon style across screens.
- [ ] No horizontal app-shell overflow at 1280×720; narrow editor has usable collapse behavior.
- [ ] Preview keeps 9:16 without cropping controls or stretching assets.
- [ ] Timeline clips, ruler, playhead and inspector labels remain aligned under zoom.
- [ ] No overlapping dropdowns, clipped tooltips, unreadable text or lost focus rings.
- [ ] Screenshots exist at 1440×900 and 1920×1080, plus narrow-window editor.
- [ ] Empty, loading and error screens are intentionally designed.

### Engineering acceptance

- [ ] `npm run build` succeeds; TypeScript strict mode passes.
- [ ] Lint and unit tests pass.
- [ ] Playwright full-demo-flow test passes using fixture data.
- [ ] No API keys, external video-generation providers, MCP calls or FFmpeg commands in frontend code.
- [ ] No claims of real media processing in static UI copy.
- [ ] UI service interface is documented for future IPC implementation.

---

## 14. Recommended frontend commands (adapt to installed versions)

For a fresh frontend workspace:

```bash
npm create vite@latest apps/desktop-ui -- --template react-ts
cd apps/desktop-ui
npm install
npm install react-router-dom lucide-react zustand @tanstack/react-query
npm install tailwindcss @tailwindcss/vite
npm install -D vitest @testing-library/react @testing-library/jest-dom jsdom @playwright/test
npm run dev
```

Use Tailwind's Vite plugin, configure `src/styles/theme.css`, and import styles in `main.tsx`. If adding Radix/shadcn and form utilities, install only the components actually used. Do not run `tauri init` until the static browser UI has passed acceptance tests.

### Suggested scripts

```json
{
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "test": "vitest run",
    "test:e2e": "playwright test",
    "lint": "eslint ."
  }
}
```

Add `@playwright/test` browser dependencies separately in local dev/CI. Use a fixed mock-data seed for repeatable screenshots.

---

## 15. Backend integration contract — future, not UI-0

Keep this map visible so static choices do not block production functionality:

| Frontend demo operation | Future production endpoint/command concept |
|---|---|
| `listProjects` | Desktop IPC `project.list` |
| `createProject` | IPC `project.create` |
| `listAssets` | IPC `asset.list` |
| `registerAsset` | Native file dialog + IPC `asset.import` |
| `getRevision` | IPC `timeline.get` |
| `saveDemoRevision` | IPC `timeline.commit` with optimistic revision check |
| `getTranscript` | IPC `transcript.get` |
| `prepareAgentBrief` | Agent handoff / documented MCP resources |
| `requestPreview` | IPC `render.preview.start` → persisted job ID |
| `getJobStatus` | IPC `job.get` / event subscription |
| `checkQA` | IPC `qa.get` |
| `requestExport` | IPC `export.start` → real output job |

**Authoritative state:** Python project services and SQLite, eventually. Tauri offers the local UI connection, while MCP is another adapter to the same engine. The React app must not run direct FFmpeg processes or own final project-file writes.

**Security and privacy:** asset IDs, validated project-scoped paths, no unrestricted shell from models, clear external-model disclosure, no hidden uploads.

---

## 16. Copy/paste implementation brief for Codex or Claude Code

> Use this brief to implement the static frontend. Follow the specification above as the source of truth.

```text
You are the lead desktop product designer and senior React/TypeScript engineer for AgenEditor.

PRODUCT
AgenEditor is a local-first desktop AI video editor. It transforms approved user-supplied raw footage, optional B-roll/music, and written instructions into polished edits. It does NOT generate video, images, voices or music. The future AI control surface is Codex/Claude Code via MCP, but this task is UI-ONLY.

MISSION
Implement the ENTIRE static frontend experience first. Use React + TypeScript + Vite + Tailwind CSS, with reusable, accessible components and deterministic mock data. Build it to look like a real professional desktop video editing workstation. Do not call real APIs, MCP servers, Tauri, Python, FFmpeg, AI services, or media processing tools yet.

READ
- docs/AGENEDITOR_PRODUCT_SPEC.md
- docs/AGENEDITOR_IMPLEMENTATION_ROADMAP.md
- docs/AGENEDITOR_STATIC_UI_IMPLEMENTATION.md

SCREENS
1. Welcome / onboarding
2. Home dashboard
3. Projects library with grid/list, search, filter and sort
4. New Project multi-step wizard
5. Project Overview
6. Main Editor workstation with left media/transcript/brief/agent dock, central 9:16 preview, right inspector, bottom five-track timeline
7. Agent handoff dialog/drawer
8. Review and revision comparison
9. Export settings and planned-delivery summary
10. Activity / job center
11. Editing style templates
12. Settings
13. Help / keyboard shortcuts
14. Consistent empty/loading/error states

IMPORTANT EDITOR BEHAVIOR
- Sample 40-second 9:16 talking-head project at 30 fps with five tracks (V2 B-roll, V1 speaker, A1 dialogue, A2 music, T1 captions).
- Source-linked mock clips with frame-based positions and editable inspector controls.
- Selecting a clip highlights it and updates its inspector.
- Zoom, playhead movement, transcript selection, panel collapse, revision selection and caption preview all operate on mock UI state.
- No fake rendering. Clearly label preview and jobs as Demo Mode or Simulation.
- No fake files offered for download. Export controls preview settings only.
- No fake live Codex/Claude Code connection. Show Not connected, with copyable setup handoff instructions.
- Every displayed control must be interactive or explicitly disabled with a reason.

DESIGN
Premium dark creative workstation: app #0B0F15, panels #10161F/#151D29, border #2B3748, text #F3F6FC, accent #84A9FF. Dense but legible; accessible; refined typography; consistent spacing; Lucide icons; restrained shadows. Desktop-first at 1440x900, reasonable at 1280x720 and adaptable when narrower.

ARCHITECTURE
Use a typed ProjectRepository interface and a MockProjectRepository implementation, not hardcoded project mutations inside components. Split repository state from transient editor UI state. Provide a clear future seam for Tauri IPC. Keep TypeScript strict. Componentize the timeline and inspector.

BUILD ORDER
UI-0 foundations and fixtures → UI-1 projects/onboarding → UI-2 complete editor workstation → UI-3 review/export/activity → UI-4 settings/templates/help/polish/testing.

TESTS AND DELIVERABLES
- Implement all screens with coherent navigation.
- Add unit/component tests plus one Playwright walkthrough (Welcome → New Project → Editor → Review → Export settings).
- Make npm run build succeed.
- Provide screenshots for 1440x900 editor, dashboard, project wizard, and review screen.
- Document mock-only limitations and the future integration seam.
- Do not claim backend features are functional and do not substitute placeholder buttons that silently do nothing.

Proceed incrementally and verify each milestone before proceeding. Prefer a complete, consistent working UI over flashy but nonfunctional visuals.
```

---

## 17. Final static UI definition of done

**AgenEditor Static UI v0.1 is done when:** a user can open the app, create a sample project, inspect supplied demo assets, navigate and manipulate a 40-second **mock timeline**, set captions/audio presentation options, prepare an external-agent editing brief, compare demo revisions, inspect sample QA warnings, and configure an eventual export—all with a polished, accessible desktop layout and **without claiming any video was actually edited**.

The immediate next implementation step after this static UI is to replace mock repository operations with typed desktop IPC and connect them to the existing Python editing engine. The UI, core engine, and MCP server must remain independently testable.
