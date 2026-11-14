# AGENTS.md — working on AgenEditor

Read before changing code. Specs: [`docs/AGENEDITOR_PRODUCT_SPEC.md`](docs/AGENEDITOR_PRODUCT_SPEC.md) (authoritative),
[`docs/AGENEDITOR_IMPLEMENTATION_ROADMAP.md`](docs/AGENEDITOR_IMPLEMENTATION_ROADMAP.md),
[`docs/AGENEDITOR_STATIC_UI_IMPLEMENTATION.md`](docs/AGENEDITOR_STATIC_UI_IMPLEMENTATION.md).

## Product rules (non-negotiable)

* **Editing only, inside AgenEditor.** AgenEditor itself never generates video, images, voices or music, and never
  fetches stock media. Generation happens in external tools the user runs (ChatGPT, Gemini, Google Flow). AgenEditor
  only writes generation *prompts* from the plan, imports the user's results, and labels them `AI-generated (external)`.
  See "Asset preparation" in the product spec (§3.1).
* **Non-destructive.** Originals are never modified. Every timeline change is a new immutable revision.
* **Source-linked.** Every clip, caption word and B-roll overlay references a registered asset ID and range.
* **Honest status.** Never report a render, export, measurement or QA result that did not actually happen.
  The static UI labels all demo data as Demo Mode / Simulation.

## Repository layout

| Path | What |
|---|---|
| `packages/ageneditor/` | Python engine (Phase 0: contracts, timebase, probe, fixtures) |
| `schemas/` | Generated JSON Schemas for contracts v1 — regenerate, never hand-edit |
| `tests/engine/` | pytest (engine) · `tests/unit`, `tests/components` Vitest (UI) · `e2e/` Playwright (UI) |
| `apps/desktop/src/` | React/TS UI: browser Demo Mode and native live workspace. |
| `apps/desktop/src-tauri/` | Native shell, IPC, Windows packaging. |
| `docs/` | Specs and engineering notes (`TIMEBASE.md`, `SECURITY_BOUNDARIES.md`, `LICENSE_INVENTORY.md`) |

## Commands

```bash
# Engine (Python 3.12 via uv)
uv sync
uv run ageneditor fixtures           # synthetic reference media → tests/fixtures/media (gitignored)
uv run pytest                        # generates fixtures on first run if missing
uv run ruff check packages tests/engine && uv run ruff format packages tests/engine
uv run ageneditor schema             # after any contract change
uv run ageneditor probe <file> --hash

# UI
npm install && npm run dev           # http://localhost:5173
npm run build && npm test && npm run test:e2e
```

If `uv` is not on PATH on Windows: `py -m uv …`.

## Engineering rules

* **Contracts first.** Wire types live in `packages/ageneditor/contracts/` (Pydantic, `extra="forbid"`). Bump
  `schema_version` for breaking changes; regenerate `schemas/` (a test fails if they drift).
* **Time is integers + rationals.** Follow [`docs/TIMEBASE.md`](docs/TIMEBASE.md). No float seconds in domain logic.
* **Subprocesses:** argument lists only, never `shell=True`, always a timeout. FFmpeg arguments are built from
  validated contracts — never from agent-supplied filter strings.
* **Paths:** project-relative (`ProjectRelativePath`), checked again at I/O time for symlink escapes.
* **Errors:** raise `EngineException(ErrorCode…)`; adapters return the structured `EngineError`.
* **MCP stdio:** stdout is protocol only; logs go to stderr / files.
* Every media-processing change ships with a test against the fixtures and, where relevant, an example output.
* Don't create empty placeholder modules for future phases.

## Security

See [`docs/SECURITY_BOUNDARIES.md`](docs/SECURITY_BOUNDARIES.md).
