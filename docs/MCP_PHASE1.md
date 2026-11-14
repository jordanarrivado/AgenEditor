# Phase One MCP workflow

AgenEditor exposes typed tools over stdio using the [MCP Python SDK v2](https://py.sdk.modelcontextprotocol.io/).
The server reads and writes only inside the workspace chosen when it starts. It does not accept shell commands,
network URLs, FFmpeg filters, or arbitrary import directories as tool arguments.

## Start or configure a host

Run `uv sync --locked` first. In PowerShell from the repository root:

```powershell
New-Item -ItemType Directory -Force .local-projects | Out-Null
$engine = (Resolve-Path .venv\Scripts\ageneditor.exe).Path
$projects = (Resolve-Path .local-projects).Path

# Start directly for an MCP client that launches stdio servers:
& $engine mcp --workspace $projects

# Or register the command in a host:
codex mcp add ageneditor -- $engine mcp --workspace $projects
claude.cmd mcp add --scope project ageneditor -- $engine mcp --workspace $projects
```

The last two commands configure those hosts; they are not required for the Python engine. Claude Code's
project scope writes project MCP configuration. The MCP server writes protocol messages to stdout only.

## Tool sequence

1. Call `create_project(name)`. Keep its `project_id` and `import_directory`.
2. Place user-approved media in that project's `import_directory` using a normal local file operation.
   Call `register_asset(project_id, import_path, role)`, where `import_path` is relative to `imports/`.
   Registration copies the file into `originals/`, hashes it and probes its streams.
3. Call `create_timeline(project_id, render_profile, target_duration_frames)`. The built-in final profile
   is 1080×1920, 30 fps, H.264/AAC; the JSON shape is in `schemas/render_profile.v1.schema.json`.
4. Fill the returned timeline's `clips` with V1 source ranges and output positions. Use registered `asset_id`
   values and half-open integer frame ranges. For a source whose frame rate differs from the output, set
   `timeline_duration_frames` to the number of output frames. `validate_timeline` reports the required span.
5. Call `validate_timeline`, then `commit_timeline` with the current `expected_revision`. A conflict returns
   `REVISION_CONFLICT`; read the current timeline and rebase the plan.
6. Call `start_render(project_id, revision)`, poll `get_job_status`, then call `run_qa` after success.
   `get_qa_report` reads the stored result. The job's `output_path` points to a folder containing
   `final.mp4`, `timeline.json` and `qa.json` after QA passes.

For the synthetic 60-second source, the reference plan uses source ranges `[0,300)`, `[450,900)` and
`[1200,1650)`, at timeline positions `0`, `300`, and `750` frames. It totals 1200 frames or 40 seconds.
Run `uv run python scripts/phase1_reference.py --workspace .local-projects` for a reproducible example.

Tools return a JSON `EngineError` in MCP error content with `isError=true` when a call fails. V2 overlays,
captions, transcription and autonomous segment selection are later phases. The current browser UI remains Demo Mode.
